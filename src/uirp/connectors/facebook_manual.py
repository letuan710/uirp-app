"""Connector Facebook — Mode A: ingest thủ công (ADR-002, ARC §6).

Owner thả file (HTML/MHTML/ảnh/txt) vào data/inbox/facebook/. `ingest` nuốt từng file:
tạo Evidence (dedup hash) + Information Object, đẻ job `parse`, rồi chuyển file sang _done/.
KHÔNG bóc text/gọi AI ở đây — đó là việc của pipeline (tách thu thập / diễn giải, ARC-008).
"""

from __future__ import annotations

import shutil

from uirp.config import Config
from uirp.connectors.common import store_and_queue

_MEDIA = {
    ".html": "text/html", ".htm": "text/html", ".mhtml": "multipart/related",
    ".txt": "text/plain", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
}


def ingest(conn, cfg: Config, topic_id: str) -> int:
    inbox = cfg.inbox_dir / "facebook"
    done = inbox / "_done"
    inbox.mkdir(parents=True, exist_ok=True)
    done.mkdir(parents=True, exist_ok=True)

    count = 0
    for f in sorted(inbox.iterdir()):
        if f.is_dir() or f.name.startswith(("_", ".")):
            continue
        raw = f.read_bytes()
        ext = f.suffix.lower().lstrip(".") or "bin"
        media = _MEDIA.get(f.suffix.lower(), "application/octet-stream")
        subtype = "screenshot" if media.startswith("image") else "post"
        created = store_and_queue(
            conn, cfg, topic_id, raw, ext, media,
            source_type=f"facebook_{subtype}", source_url=None,
            title=f.stem, capture_method="manual_ingest",
        )
        shutil.move(str(f), str(done / f.name))
        if created:
            count += 1
    return count
