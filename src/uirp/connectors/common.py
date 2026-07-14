"""Helper chung cho connector: lưu bytes → Evidence + Information Object + job parse.

Dùng chung Mode A (facebook_manual) và Mode B (facebook_browser) — ARC-008. Dedup evidence
theo content hash và dedup IO theo (evidence, topic) để ingest/fetch idempotent (ARC-014).
"""

from __future__ import annotations

from datetime import datetime, timezone

from uirp.config import Config
from uirp.core import jobs
from uirp.ids import new_id
from uirp.store import db, evidence


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def store_and_queue(
    conn, cfg: Config, topic_id: str, raw: bytes, ext: str, media: str,
    source_type: str, source_url: str | None, title: str, capture_method: str,
) -> bool:
    """Trả True nếu tạo IO mới + đẻ job parse; False nếu đã có (dedup, không làm gì)."""
    h, path, _is_new = evidence.put(cfg, raw, ext)
    rows = db.query(conn, "SELECT id FROM evidence WHERE content_hash = ?", (h,))
    if rows:
        ev_id = rows[0]["id"]
    else:
        ev_id = new_id("ev")
        db.insert(conn, "evidence", {
            "id": ev_id, "content_hash": h,
            "file_path": str(path.relative_to(cfg.data_dir)),
            "media_type": media, "size_bytes": len(raw),
            "capture_method": capture_method, "captured_time": _now(), "created_at": _now(),
        })

    if db.query(
        conn, "SELECT id FROM information_object WHERE evidence_id=? AND topic_id=?",
        (ev_id, topic_id),
    ):
        return False

    io_id = new_id("io")
    db.insert(conn, "information_object", {
        "id": io_id, "source_type": source_type, "source_url": source_url,
        "title": title, "topic_id": topic_id,
        "captured_time": _now(), "evidence_id": ev_id, "created_at": _now(),
    })
    jobs.enqueue(conn, "parse", {"io_id": io_id})
    return True
