"""Handler demo để kiểm chứng state machine (ARC §13 bước 2) — KHÔNG phải pipeline thật.

Cho phép chạy ``uirp _demo`` seed vài job giả rồi ``uirp run --once`` xem đủ 4 nhánh
trạng thái: DONE, đẻ job con, FAILED (vĩnh viễn), retry→FAILED (tạm), WAITING_QUOTA.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from uirp.config import Config
from uirp.core import jobs
from uirp.errors import PermanentError, QuotaExceeded, TransientError


def _noop(conn: sqlite3.Connection, cfg: Config, payload: dict[str, Any], job_id: str) -> list:
    return []


def _spawn(conn: sqlite3.Connection, cfg: Config, payload: dict[str, Any], job_id: str) -> list:
    return [("demo_noop", {"from": "spawn"})]


def _fail_perm(conn: sqlite3.Connection, cfg: Config, payload: dict[str, Any], job_id: str) -> list:
    raise PermanentError("lỗi vĩnh viễn demo (ParseError-loại)")


def _fail_temp(conn: sqlite3.Connection, cfg: Config, payload: dict[str, Any], job_id: str) -> list:
    raise TransientError("lỗi tạm demo (sẽ retry)")


def _quota(conn: sqlite3.Connection, cfg: Config, payload: dict[str, Any], job_id: str) -> list:
    raise QuotaExceeded(message="hết quota demo")


def register_all() -> None:
    jobs.register("demo_noop", _noop)
    jobs.register("demo_spawn", _spawn)
    jobs.register("demo_fail_perm", _fail_perm)
    jobs.register("demo_fail_temp", _fail_temp)
    jobs.register("demo_quota", _quota)


def seed(conn: sqlite3.Connection) -> int:
    for jt in ("demo_noop", "demo_spawn", "demo_fail_perm", "demo_fail_temp", "demo_quota"):
        jobs.enqueue(conn, jt, {})
    return 5
