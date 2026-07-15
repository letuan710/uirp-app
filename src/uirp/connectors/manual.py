"""Mode A ingest generic — đa nền tảng (ADR-009). Đọc data/inbox/<platform>/.

Nuốt theo ĐỊNH DẠNG (HTML/ảnh/txt), không theo nền tảng → dùng được cho MỌI mạng xã hội
đã khai báo trong registry. Chỉ tạo Evidence + IO + job parse; diễn giải là việc pipeline.
"""

from __future__ import annotations

import shutil

from uirp.config import Config
from uirp.connectors.common import store_and_queue
from uirp.platforms import get

_MEDIA = {
    ".html": "text/html", ".htm": "text/html", ".mhtml": "multipart/related",
    ".txt": "text/plain", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".webp": "image/webp",
}


def ingest(conn, cfg: Config, topic_id: str, platform_key: str) -> int:
    get(platform_key)  # kiểm tra nền tảng hợp lệ
    inbox = cfg.inbox_dir / platform_key
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
            source_type=f"{platform_key}_{subtype}", source_url=None,
            title=f.stem, capture_method="manual_ingest",
        )
        shutil.move(str(f), str(done / f.name))
        if created:
            count += 1
    return count
