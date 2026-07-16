"""Durable job scheduler (CHR-056/057, ARC §8, DGM-002).

Hàng đợi là bảng ``job`` trong SQLite — không broker ngoài (P14). Trạng thái ghi xuống
đĩa sau mỗi job nên tắt máy/hết quota đều tiếp lại đúng chỗ. Handler theo ``job_type``
đăng ký qua ``register()``; idempotency là trách nhiệm của từng handler (ARC-014).
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from uirp.config import Config
from uirp.errors import QuotaExceeded, TransientError, error_kind
from uirp.ids import new_id
from uirp.store import db

# Handler: (conn, cfg, payload, job_id) -> danh sách job con (job_type, payload) cần enqueue.
Handler = Callable[
    [sqlite3.Connection, Config, dict[str, Any], str], list[tuple[str, dict[str, Any]]]
]
_HANDLERS: dict[str, Handler] = {}


def register(job_type: str, handler: Handler) -> None:
    _HANDLERS[job_type] = handler


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    id: str
    job_type: str
    state: str
    payload: dict[str, Any]
    retry_count: int


def enqueue(
    conn: sqlite3.Connection, job_type: str, payload: dict[str, Any] | None = None
) -> str:
    now = _now()
    job_id = new_id("job")
    db.insert(
        conn,
        "job",
        {
            "id": job_id,
            "job_type": job_type,
            "state": "PENDING",
            "payload": json.dumps(payload or {}, ensure_ascii=False),
            "retry_count": 0,
            "created_at": now,
            "updated_at": now,
        },
    )
    return job_id


def _set_state(conn: sqlite3.Connection, job_id: str, **fields: Any) -> None:
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE job SET {cols} WHERE id = ?", (*fields.values(), job_id))
    conn.commit()


def _reclaim_stuck(conn: sqlite3.Connection, cfg: Config) -> None:
    """Job RUNNING quá hạn (crash/tắt máy giữa chừng) → trả PENDING (ARC edge case)."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=cfg.running_timeout_minutes)
    ).isoformat()
    conn.execute(
        "UPDATE job SET state='PENDING', updated_at=? WHERE state='RUNNING' AND updated_at < ?",
        (_now(), cutoff),
    )
    conn.commit()


def _wake_due_quota(conn: sqlite3.Connection) -> None:
    """WAITING_QUOTA đã tới retry_at → PENDING (ARC-012)."""
    conn.execute(
        "UPDATE job SET state='PENDING', updated_at=? "
        "WHERE state='WAITING_QUOTA' AND (retry_at IS NULL OR retry_at <= ?)",
        (_now(), _now()),
    )
    conn.commit()


def _claim_next(conn: sqlite3.Connection) -> Job | None:
    """Claim ATOMIC: UPDATE có điều kiện state='PENDING' — nhiều worker/thread
    cùng chạy không thể nẫng trùng một job (kiểm tra rowcount)."""
    while True:
        row = conn.execute(
            "SELECT id, job_type, payload, retry_count FROM job "
            "WHERE state='PENDING' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        cur = conn.execute(
            "UPDATE job SET state='RUNNING', updated_at=? WHERE id=? AND state='PENDING'",
            (_now(), row["id"]),
        )
        conn.commit()
        if cur.rowcount == 0:
            continue  # thread khác vừa claim mất → thử job kế tiếp
        return Job(
            id=row["id"],
            job_type=row["job_type"],
            state="RUNNING",
            payload=json.loads(row["payload"] or "{}"),
            retry_count=row["retry_count"],
        )


def _next_quota_wait(conn: sqlite3.Connection, cfg: Config) -> float | None:
    """Số giây tới retry_at gần nhất của WAITING_QUOTA; None nếu không có."""
    row = conn.execute(
        "SELECT MIN(retry_at) AS soonest FROM job WHERE state='WAITING_QUOTA'"
    ).fetchone()
    if row is None or row["soonest"] is None:
        # có job WAITING_QUOTA nhưng không có retry_at → dùng mặc định
        any_waiting = conn.execute(
            "SELECT 1 FROM job WHERE state='WAITING_QUOTA' LIMIT 1"
        ).fetchone()
        return float(cfg.default_wait_seconds) if any_waiting else None
    delta = (datetime.fromisoformat(row["soonest"]) - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, delta)


def _execute(conn: sqlite3.Connection, cfg: Config, job: Job) -> None:
    handler = _HANDLERS.get(job.job_type)
    if handler is None:
        _set_state(
            conn, job.id, state="FAILED",
            error=f"không có handler cho job_type={job.job_type}",
            error_kind="PermanentError",
        )
        return
    try:
        children = handler(conn, cfg, job.payload, job.id)
        for child_type, child_payload in children:
            enqueue(conn, child_type, child_payload)
        _set_state(conn, job.id, state="DONE")
    except QuotaExceeded as e:
        conn.rollback()  # bỏ dữ liệu dở dang của handler (nếu có)
        retry_at = (
            datetime.fromtimestamp(e.retry_at, timezone.utc).isoformat()
            if e.retry_at
            else (datetime.now(timezone.utc) + timedelta(seconds=cfg.default_wait_seconds)).isoformat()
        )
        _set_state(conn, job.id, state="WAITING_QUOTA", retry_at=retry_at, error_kind="QuotaExceeded")
    except TransientError as e:
        conn.rollback()
        if job.retry_count + 1 < cfg.max_retry:
            _set_state(conn, job.id, state="PENDING", retry_count=job.retry_count + 1,
                       error=str(e), error_kind=error_kind(e))
        else:
            _set_state(conn, job.id, state="FAILED", error=str(e), error_kind=error_kind(e))
    except Exception as e:  # noqa: BLE001 - mọi lỗi khác (gồm PermanentError) coi là vĩnh viễn
        conn.rollback()
        _set_state(conn, job.id, state="FAILED", error=repr(e), error_kind=error_kind(e))


def run(conn: sqlite3.Connection, cfg: Config, once: bool = False,
        max_jobs: int | None = None) -> int:
    """Chạy scheduler tới khi hết việc.

    once=True: xử lý hết job sẵn sàng rồi trả về (không ngủ chờ quota).
    once=False: ngủ tới retry_at gần nhất rồi chạy tiếp (ARC-015).
    max_jobs: dừng sau N job — cho web xử lý theo lô, request ngắn (ADR-011).
    Trả số job đã xử lý (DONE/FAILED/WAITING_QUOTA) trong lần gọi này.
    """
    processed = 0
    _reclaim_stuck(conn, cfg)
    while True:
        if max_jobs is not None and processed >= max_jobs:
            return processed
        _wake_due_quota(conn)
        job = _claim_next(conn)
        if job is not None:
            _execute(conn, cfg, job)
            processed += 1
            continue
        # Hết PENDING. Còn WAITING_QUOTA không?
        wait = _next_quota_wait(conn, cfg)
        if wait is None or once:
            return processed
        time.sleep(min(wait, 300) + 0.5)  # ngủ tối đa 5 phút mỗi nhịp rồi kiểm lại
