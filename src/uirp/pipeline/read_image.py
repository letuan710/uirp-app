"""read_image: Evidence ảnh → Observation ocr_text (ARC-009b, ADR-008).

Gửi ảnh cho AI adapter (backend claude_vision đọc trực tiếp; hoặc local_ocr sau này).
Có chữ → tạo ocr_text rồi đẻ extract (ảnh cũng sinh claim). FakeBackend không đọc ảnh
→ tạo observation placeholder, không extract (rõ ràng: cần backend Claude thật).
"""

from __future__ import annotations

from typing import Any

from uirp.ai.adapter import AIClient, AIRequest
from uirp.config import Config
from uirp.pipeline.parse import _add_obs, route_obs
from uirp.store import db


def make_handler(client: AIClient):
    def read_image(conn, cfg: Config, payload: dict[str, Any], job_id: str) -> list:
        io = db.get(conn, "information_object", payload["io_id"])
        if io is None:
            return []
        ev = db.get(conn, "evidence", io["evidence_id"])
        if ev is None:
            return []
        if db.query(
            conn,
            "SELECT id FROM observation WHERE evidence_id=? AND kind='ocr_text' LIMIT 1",
            (ev["id"],),
        ):
            return []  # idempotency (ARC-014)

        img_path = cfg.data_dir / ev["file_path"]
        resp = client.complete(
            AIRequest(tier="S", prompt_ref="read_image", payload={}, images=[img_path]),
            conn, job_id,
        )
        text = resp.text.strip()
        if not text:
            _add_obs(conn, ev["id"], "ocr_text", "(ảnh chưa OCR — cần backend Claude thật)")
            return []
        oid = _add_obs(conn, ev["id"], "ocr_text", text)
        return [route_obs(text, oid)]  # ảnh tiếng Trung cũng tự dịch

    return read_image
