"""Kết nối SQLite + migration + helper query mỏng (không ORM — P14, ARC-005).

Migration forward-only theo ``_meta.schema_version`` (STD4-R10). schema.sql là v1;
các thay đổi sau đặt trong ``migrations/NNN_*.sql`` và áp tuần tự khi version lớn hơn.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from uirp.config import Config

_log = logging.getLogger(__name__)

_SCHEMA = Path(__file__).parent / "schema.sql"
_MIGRATIONS = Path(__file__).parent / "migrations"


def connect(cfg: Config) -> sqlite3.Connection:
    """Mở kết nối, bật pragma, chạy migration nếu cần."""
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # Nhiều thread (worker + web + scan) cùng ghi: chờ lock thay vì ném 'database is locked'.
    conn.execute("PRAGMA busy_timeout = 10000")
    _migrate(conn)
    return conn


def _schema_version(conn: sqlite3.Connection) -> int:
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_meta'"
    ).fetchone()
    if not has_meta:
        return 0
    row = conn.execute("SELECT value FROM _meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def _migrate(conn: sqlite3.Connection) -> None:
    version = _schema_version(conn)
    if version == 0:
        conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
        conn.commit()
        version = _schema_version(conn)
    # Áp các migration tuần tự > version hiện tại (forward-only).
    if _MIGRATIONS.is_dir():
        for path in sorted(_MIGRATIONS.glob("*.sql")):
            num = int(path.name.split("_", 1)[0])
            if num <= version:
                continue
            try:
                conn.executescript(path.read_text(encoding="utf-8"))
            except sqlite3.OperationalError as e:
                # Tính năng tùy chọn không khả dụng (ví dụ FTS5 thiếu trong build SQLite):
                # ghi cảnh báo, vẫn bump version để không thử lại; feature đó tạm không dùng được.
                if "fts5" in str(e).lower() or "no such module" in str(e).lower():
                    _log.warning("bỏ qua migration %s (module không khả dụng): %s", path.name, e)
                else:
                    raise
            conn.execute("UPDATE _meta SET value=? WHERE key='schema_version'", (str(num),))
            conn.commit()


# --- Helper query mỏng ---
# Lưu ý: tên bảng nội bộ (không phải input người dùng) nên nội suy trực tiếp an toàn;
# mọi GIÁ TRỊ đều tham số hóa bằng '?'.


def insert(
    conn: sqlite3.Connection, table: str, row: dict[str, Any], commit: bool = True
) -> str:
    """commit=False: gộp nhiều insert vào một transaction, caller tự commit
    (job handler ghi theo lô để crash giữa chừng không để lại dữ liệu dở dang)."""
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" * len(row))
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", tuple(row.values()))
    if commit:
        conn.commit()
    return str(row.get("id", ""))


def get(conn: sqlite3.Connection, table: str, id_: str) -> dict[str, Any] | None:
    r = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (id_,)).fetchone()
    return dict(r) if r else None


def query(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]
