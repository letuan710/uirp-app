"""parse: Evidence(trang lưu) → nhiều Observation (ADR8-2).

Bóc THÂN BÀI + từng BÌNH LUẬN (kèm tác giả) + từng ẢNH bằng stdlib html.parser
(rẻ, không token). DOM lạ/obfuscated → body gom hết, phần cấu trúc để AI fallback
(ARC-013c self-heal). Mỗi observation nội dung đẻ một job extract.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

from uirp.ai.adapter import AIClient
from uirp.config import Config
from uirp.errors import SourceFileError
from uirp.ids import new_id
from uirp.store import db

_SKIP = ("script", "style", "title", "head", "noscript")


def _class_of(attrs: list[tuple[str, str | None]]) -> str:
    for k, v in attrs:
        if k == "class":
            return (v or "").lower()
    return ""


class _StructuredExtractor(HTMLParser):
    """Bóc {body, comments[{author,text}], images[{src,alt}]} theo gợi ý class phổ biến."""

    def __init__(self) -> None:
        super().__init__()
        self.body: list[str] = []
        self.comments: list[dict[str, Any]] = []
        self.images: list[dict[str, str]] = []
        self.post_author: str | None = None
        self._skip = 0
        self._ctx: list[tuple[str, str | None]] = []  # role ∈ {None,'comment','author','post_author'}
        self._cur: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _SKIP:
            self._skip += 1
            return
        if tag == "img":
            d = dict(attrs)
            if d.get("src"):
                self.images.append({"src": d.get("src", ""), "alt": d.get("alt", "") or ""})
            return
        cls = _class_of(attrs)
        role: str | None = None
        if "comment" in cls and self._cur is None:
            role = "comment"
            self._cur = {"author": None, "text": []}
        elif self._cur is not None and self._cur["author"] is None and (
            "author" in cls or "cauthor" in cls or "name" in cls
        ):
            role = "author"
        elif self._cur is None and self.post_author is None and (
            "author" in cls or "poster" in cls or "username" in cls or tag == "h3"
        ):
            role = "post_author"  # tác giả bài (ngoài bình luận)
        self._ctx.append((tag, role))

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
            return
        for i in range(len(self._ctx) - 1, -1, -1):
            if self._ctx[i][0] == tag:
                _, role = self._ctx.pop(i)
                if role == "comment" and self._cur is not None:
                    self._finalize_comment()
                break

    def _finalize_comment(self) -> None:
        text = " ".join(self._cur["text"]).strip().lstrip(":").strip()
        author = self._cur["author"]
        # Fallback: "Tên: nội dung" khi không có span tác giả riêng.
        if author is None and ":" in text:
            head, _, tail = text.partition(":")
            if len(head.split()) <= 4 and head[:1].isupper():
                author, text = head.strip(), tail.strip()
        if text:
            self.comments.append({"author": author, "text": text})
        self._cur = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        t = data.strip()
        if not t:
            return
        if self._cur is not None:
            if any(r == "author" for _, r in self._ctx) and self._cur["author"] is None:
                self._cur["author"] = t
            else:
                self._cur["text"].append(t)
        elif any(r == "post_author" for _, r in self._ctx) and self.post_author is None:
            self.post_author = t  # không đưa tên tác giả vào thân bài
        else:
            self.body.append(t)


def _decode_html(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def _add_obs(conn, evidence_id: str, kind: str, content: str, locator: dict | None = None) -> str:
    obs_id = new_id("obs")
    db.insert(conn, "observation", {
        "id": obs_id, "evidence_id": evidence_id, "kind": kind, "content": content,
        "locator": json.dumps(locator, ensure_ascii=False) if locator else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return obs_id


def make_handler(client: AIClient):
    def parse(conn, cfg: Config, payload: dict[str, Any], job_id: str) -> list:
        io = db.get(conn, "information_object", payload["io_id"])
        if io is None:
            return []
        ev = db.get(conn, "evidence", io["evidence_id"])
        if ev is None:
            return []
        if db.query(conn, "SELECT id FROM observation WHERE evidence_id=? LIMIT 1", (ev["id"],)):
            return []  # idempotency (ARC-014)

        try:
            raw = (cfg.data_dir / ev["file_path"]).read_bytes()
        except OSError as e:
            raise SourceFileError(f"không đọc được evidence: {e}") from e

        media = ev["media_type"]
        children: list = []
        if media.startswith("image"):
            # Ảnh (screenshot) → observation image_ref; OCR/vision ở bước read_image.
            _add_obs(conn, ev["id"], "image_ref", io["title"] or "(ảnh)",
                     {"src": ev["file_path"], "alt": io["title"]})
            return children

        if media.startswith("text") or media == "multipart/related":
            html = _decode_html(raw)
            ext = _StructuredExtractor()
            try:
                ext.feed(html)
            except Exception:  # noqa: BLE001
                pass
            body = "\n".join(ext.body).strip()
            if body:
                loc = {"author": ext.post_author} if ext.post_author else None
                oid = _add_obs(conn, ev["id"], "body_text", body, loc)
                children.append(("extract", {"obs_id": oid}))
            for c in ext.comments:
                oid = _add_obs(conn, ev["id"], "comment", c["text"], {"author": c["author"]})
                children.append(("extract", {"obs_id": oid}))
            for img in ext.images:
                _add_obs(conn, ev["id"], "image_ref", img.get("alt") or "(ảnh)", img)
        return children

    return parse
