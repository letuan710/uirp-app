"""Test bước 3-8: pipeline (parse/extract/translate/read_image), curate, đa nền tảng.

Dùng FakeBackend (offline, không gọi Claude) — STD4-R11. Chạy: PYTHONPATH=src pytest
"""

from __future__ import annotations

import base64

import pytest

from uirp import config, curate, platforms, textutil
from uirp.connectors import manual
from uirp.core import jobs
from uirp.ids import new_id
from uirp.pipeline import register_all
from uirp.store import db

# PNG 1x1 hợp lệ để test read_image.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest.fixture()
def cfg(tmp_path):
    c = config.load(root=tmp_path)
    c.data["api"]["backend"] = "fake"
    c.data_dir.mkdir(parents=True, exist_ok=True)
    return c


@pytest.fixture()
def conn(cfg):
    con = db.connect(cfg)
    register_all(cfg)  # parse/extract/translate/read_image với FakeBackend
    yield con
    con.close()


def _topic(conn, tid="top_TEST"):
    db.insert(conn, "topic", {"id": tid, "name": "t", "status": "active", "created_at": "2026-01-01"})
    return tid


def _run_html(cfg, conn, html, platform="facebook", tid="top_TEST"):
    inbox = cfg.inbox_dir / platform
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"{new_id('io')}.html").write_text(html, encoding="utf-8")
    manual.ingest(conn, cfg, tid, platform)
    jobs.run(conn, cfg, once=True)


def _kinds(conn):
    return {r["kind"]: r["n"] for r in db.query(conn, "SELECT kind,COUNT(*) n FROM observation GROUP BY kind")}


# --- parse đa observation (ADR-008) ---
def test_parse_body_comment_image(cfg, conn):
    _topic(conn)
    _run_html(cfg, conn, (
        '<html><body><div class="author">A</div>'
        '<div class="body">Sàn XYZ lừa đảo nhà đầu tư tại Hà Nội, mọi người tránh xa.</div>'
        '<img src="http://x/a.jpg" alt="ảnh">'
        '<div class="comment"><span class="cauthor">B</span>: Tôi cũng bị mất tiền ở sàn XYZ.</div>'
        '</body></html>'))
    k = _kinds(conn)
    assert k.get("body_text") == 1
    assert k.get("comment") == 1
    assert k.get("image_ref") == 1


def test_comment_and_post_author_attributed(cfg, conn):
    _topic(conn)
    _run_html(cfg, conn, (
        '<html><body><div class="author">Người Đăng</div>'
        '<div class="body">Sàn XYZ lừa đảo tại Hà Nội, hãy cẩn thận nhé mọi người.</div>'
        '<div class="comment"><span class="cauthor">Người Bình Luận</span>: Tôi cũng mất tiền ở đó.</div>'
        '</body></html>'))
    names = {r["canonical_name"] for r in db.query(
        conn,
        "SELECT DISTINCT e.canonical_name FROM claim c JOIN entity e ON c.asserted_by_entity_id=e.id",
    )}
    assert "Người Đăng" in names       # thân bài gắn tác giả bài
    assert "Người Bình Luận" in names  # bình luận gắn người bình luận


# --- tự dịch tiếng Trung (ADR-009) ---
def test_chinese_is_translated(cfg, conn):
    _topic(conn)
    _run_html(cfg, conn,
              '<html><body><div class="body">某投资平台涉嫌诈骗，很多投资者损失惨重。</div></body></html>')
    tr = db.query(conn, "SELECT lang, derived_from_obs_id FROM observation WHERE kind='translation'")
    assert tr and tr[0]["lang"] == "vi"
    assert tr[0]["derived_from_obs_id"] is not None  # dẫn xuất, truy về gốc (ONT-R3)
    assert db.query(conn, "SELECT 1 FROM usage_log WHERE prompt LIKE 'translate@%'")


def test_vietnamese_not_translated(cfg, conn):
    _topic(conn)
    _run_html(cfg, conn,
              '<html><body><div class="body">Sàn XYZ lừa đảo tại Hà Nội, mọi người tránh xa nhé.</div></body></html>')
    assert not db.query(conn, "SELECT 1 FROM observation WHERE kind='translation'")


# --- read_image (ADR-008) ---
def test_image_goes_through_read_image(cfg, conn):
    _topic(conn)
    inbox = cfg.inbox_dir / "facebook"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "s.png").write_bytes(_PNG)
    manual.ingest(conn, cfg, "top_TEST", "facebook")
    jobs.run(conn, cfg, once=True)
    assert db.query(conn, "SELECT 1 FROM observation WHERE kind='ocr_text'")
    assert db.query(conn, "SELECT 1 FROM usage_log WHERE prompt LIKE 'read_image@%'")


# --- textutil ---
def test_is_chinese():
    assert textutil.is_chinese("某平台涉嫌诈骗投资者")
    assert not textutil.is_chinese("Sàn XYZ lừa đảo tại Hà Nội")
    assert not textutil.is_chinese("")


# --- registry đa nền tảng (ADR-009) ---
def test_platforms_registry():
    assert platforms.get("weibo").region == "CN"
    assert platforms.get("facebook").auto is True
    assert platforms.get("douyin").auto is False  # anti-bot → Mode A
    keys = {p.key for p in platforms.all_platforms()}
    assert {"facebook", "weibo", "tiktok", "zalo", "xiaohongshu"} <= keys
    with pytest.raises(Exception):
        platforms.get("khong_ton_tai")


def test_ingest_multi_platform(cfg, conn):
    _topic(conn, "top_WB")
    inbox = cfg.inbox_dir / "weibo"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "w.html").write_text(
        "<html><body><div class=body>nội dung đủ dài để thành observation nhé.</div></body></html>",
        encoding="utf-8",
    )
    assert manual.ingest(conn, cfg, "top_WB", "weibo") == 1
    st = db.query(conn, "SELECT source_type FROM information_object")[0]["source_type"]
    assert st == "weibo_post"


# --- curate: merge/split đảo ngược (ONT-R5) ---
def test_merge_split_reversible(conn):
    a, b = new_id("ent"), new_id("ent")
    for eid, name in [(a, "ABC Trading"), (b, "ABC Trading Ltd")]:
        db.insert(conn, "entity", {"id": eid, "entity_type": "org", "canonical_name": name,
                                   "status": "active", "created_at": "2026-01-01"})
    assert curate.scan_merge_proposals(conn, 0.7) == 1
    curate.approve_proposals(conn, 0.7)
    assert db.get(conn, "entity", b)["status"].startswith("merged_into:")
    assert curate.resolve_entity(conn, b) == a  # resolve về canonical
    curate.split_entity(conn, b)
    assert db.get(conn, "entity", b)["status"] == "active"  # tách lại được


def test_annotation(conn):
    aid = curate.add_annotation(conn, "clm_x", "ghi chú của tôi", "credible")
    assert db.get(conn, "annotation", aid)["verdict"] == "credible"
