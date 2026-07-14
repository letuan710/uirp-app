"""Test bước 1-2: store + scheduler durable (STD4-R11, ARC §13).

Chạy: PYTHONPATH=src pytest   (không gọi Claude — chỉ stdlib + FakeBackend sau này).
"""

from __future__ import annotations

import re

import pytest

from uirp import config, ids
from uirp.core import demo, jobs
from uirp.errors import PermanentError, QuotaExceeded, TransientError, error_kind
from uirp.store import db, evidence


@pytest.fixture()
def cfg(tmp_path):
    c = config.load(root=tmp_path)
    (c.data_dir).mkdir(parents=True, exist_ok=True)
    return c


@pytest.fixture()
def conn(cfg):
    demo.register_all()
    con = db.connect(cfg)
    yield con
    con.close()


# --- ids (ONT-R7) ---
def test_id_format():
    i = ids.new_id("ev")
    assert re.fullmatch(r"ev_[0-9A-HJKMNP-TV-Z]{26}", i), i
    assert ids.new_id("obs") != ids.new_id("obs")  # không đụng độ


def test_id_rejects_bad_prefix():
    with pytest.raises(ValueError):
        ids.new_id("xxx")


# --- schema/migration (ONT-R8, STD4-R10) ---
def test_schema_version_is_1(conn):
    v = db.query(conn, "SELECT value FROM _meta WHERE key='schema_version'")
    assert v[0]["value"] == "1"


def test_all_11_ontology_tables_exist(conn):
    names = {
        r["name"]
        for r in db.query(conn, "SELECT name FROM sqlite_master WHERE type='table'")
    }
    for t in [
        "topic", "information_object", "evidence", "observation", "claim",
        "entity", "entity_alias", "merge_proposal", "relationship",
        "job", "usage_log", "annotation",
    ]:
        assert t in names, f"thiếu bảng {t}"


# --- evidence dedup (CHR-043, ONT-R7) ---
def test_evidence_dedup(cfg):
    h1, p1, new1 = evidence.put(cfg, b"hello world", "txt")
    h2, p2, new2 = evidence.put(cfg, b"hello world", "txt")
    assert h1 == h2 and p1 == p2
    assert new1 is True and new2 is False  # lần hai không ghi lại (dedup)


# --- errors (STD4-R5) ---
def test_error_kind_is_class_name():
    assert error_kind(ParseErr := PermanentError("x")) == "PermanentError"
    assert error_kind(TransientError("x")) == "TransientError"


# --- state machine (DGM-002) ---
def test_done_and_child_spawn(conn, cfg):
    jobs.enqueue(conn, "demo_spawn", {})
    jobs.run(conn, cfg, once=True)
    states = {r["job_type"]: r["state"] for r in db.query(conn, "SELECT job_type, state FROM job")}
    assert states["demo_spawn"] == "DONE"
    # đã đẻ job con demo_noop và cũng DONE
    noops = db.query(conn, "SELECT state FROM job WHERE job_type='demo_noop'")
    assert noops and all(r["state"] == "DONE" for r in noops)


def test_permanent_error_fails_immediately(conn, cfg):
    jid = jobs.enqueue(conn, "demo_fail_perm", {})
    jobs.run(conn, cfg, once=True)
    row = db.get(conn, "job", jid)
    assert row["state"] == "FAILED"
    assert row["retry_count"] == 0  # không retry
    assert row["error_kind"] == "PermanentError"


def test_transient_error_retries_then_fails(conn, cfg):
    jid = jobs.enqueue(conn, "demo_fail_temp", {})
    jobs.run(conn, cfg, once=True)
    row = db.get(conn, "job", jid)
    assert row["state"] == "FAILED"
    assert row["retry_count"] == cfg.max_retry - 1  # đã retry tới hạn
    assert row["error_kind"] == "TransientError"


def test_quota_goes_waiting(conn, cfg):
    jid = jobs.enqueue(conn, "demo_quota", {})
    jobs.run(conn, cfg, once=True)
    row = db.get(conn, "job", jid)
    assert row["state"] == "WAITING_QUOTA"
    assert row["retry_at"] is not None  # có mốc thử lại


def test_rerun_does_not_reprocess_done(conn, cfg):
    jobs.enqueue(conn, "demo_noop", {})
    assert jobs.run(conn, cfg, once=True) == 1
    assert jobs.run(conn, cfg, once=True) == 0  # DONE không chạy lại (idempotent ở mức job)


def test_reclaim_stuck_running(conn, cfg):
    db.insert(conn, "job", {
        "id": "job_STUCK", "job_type": "demo_noop", "state": "RUNNING",
        "payload": "{}", "retry_count": 0,
        "created_at": "2020-01-01T00:00:00+00:00", "updated_at": "2020-01-01T00:00:00+00:00",
    })
    jobs.run(conn, cfg, once=True)
    assert db.get(conn, "job", "job_STUCK")["state"] == "DONE"  # reclaim → chạy → DONE
