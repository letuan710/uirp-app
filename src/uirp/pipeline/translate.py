"""translate: Observation tiếng Trung → Observation bản dịch tiếng Việt (ONT-R3, ADR-009).

Bản dịch là Observation DẪN XUẤT (kind='translation', derived_from_obs_id trỏ về gốc,
lang='vi') — gốc tiếng Trung được giữ nguyên (evidence-first). Bản dịch rồi đẻ extract
để claim ra tiếng Việt (dễ dùng cho Owner), lineage vẫn truy về nguồn.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from uirp.ai.adapter import AIClient, AIRequest
from uirp.config import Config
from uirp.ids import new_id
from uirp.store import db


def make_handler(client: AIClient):
    def translate(conn, cfg: Config, payload: dict[str, Any], job_id: str) -> list:
        obs = db.get(conn, "observation", payload["obs_id"])
        if obs is None:
            return []
        # Idempotency (ARC-014): đã có bản dịch của observation này?
        if db.query(
            conn,
            "SELECT id FROM observation WHERE derived_from_obs_id=? AND kind='translation' LIMIT 1",
            (obs["id"],),
        ):
            return []

        resp = client.complete(
            AIRequest(tier="M", prompt_ref="translate", payload={"text": obs["content"]}),
            conn, job_id,
        )
        text = resp.text.strip()
        if not text:
            return []

        tid = new_id("obs")
        db.insert(conn, "observation", {
            "id": tid, "evidence_id": obs["evidence_id"], "kind": "translation",
            "content": text, "lang": "vi", "derived_from_obs_id": obs["id"],
            "locator": obs["locator"],  # giữ tác giả để extract gắn chủ thể phát ngôn
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        return [("extract", {"obs_id": tid})]

    return translate
