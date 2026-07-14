"""extract: Observation → Claim/Entity/Relationship qua AI adapter (ARC §8).

Cổng lọc rẻ trước (classify_relevance tier S — STD5-R5), rồi extract_claims (tier M).
Máy chỉ bóc tách như nguồn trình bày (CHR-006); không phán xử đúng/sai (CHR-004).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from uirp.ai.adapter import AIClient, AIRequest
from uirp.config import Config
from uirp.errors import SchemaError
from uirp.ids import new_id
from uirp.store import db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_or_create_entity(conn, name: str, etype: str | None) -> str:
    name = (name or "").strip()
    rows = db.query(conn, "SELECT id FROM entity WHERE canonical_name=? LIMIT 1", (name,))
    if rows:
        return rows[0]["id"]
    eid = new_id("ent")
    db.insert(conn, "entity", {
        "id": eid, "entity_type": etype or "concept", "canonical_name": name,
        "status": "active", "created_at": _now(),
    })
    db.insert(conn, "entity_alias", {
        "id": new_id("ali"), "entity_id": eid, "alias": name, "created_at": _now(),
    })
    return eid


def make_handler(client: AIClient):
    def extract(conn, cfg: Config, payload: dict[str, Any], job_id: str) -> list:
        obs = db.get(conn, "observation", payload["obs_id"])
        if obs is None:
            return []
        # Idempotency (ARC-014): observation đã có claim thì thôi.
        if db.query(conn, "SELECT id FROM claim WHERE observation_id=? LIMIT 1", (obs["id"],)):
            return []

        # Tác giả đã biết từ lúc thu (bài hoặc bình luận) → chủ thể phát ngôn mặc định (ADR8-2).
        default_author_id: str | None = None
        locator = json.loads(obs["locator"]) if obs["locator"] else {}
        if locator.get("author"):
            default_author_id = _get_or_create_entity(conn, locator["author"], "person")

        io = db.query(
            conn,
            "SELECT topic_id FROM information_object WHERE evidence_id=? LIMIT 1",
            (obs["evidence_id"],),
        )
        topic_name = ""
        if io:
            t = db.get(conn, "topic", io[0]["topic_id"])
            topic_name = t["name"] if t else ""

        # Cổng lọc rẻ (STD5-R5): classify tier S trước khi tốn extraction tier M.
        rel = client.complete(
            AIRequest(tier="S", prompt_ref="classify_relevance",
                      payload={"text": obs["content"], "topic": topic_name}, expect_json=True),
            conn, job_id,
        )
        try:
            relevant = bool(json.loads(rel.text).get("relevant", True))
        except json.JSONDecodeError:
            relevant = True
        if not relevant:
            return []

        # Extraction tier M.
        resp = client.complete(
            AIRequest(tier="M", prompt_ref="extract_claims",
                      payload={"text": obs["content"]}, expect_json=True),
            conn, job_id,
        )
        try:
            data = json.loads(resp.text)
        except json.JSONDecodeError as e:
            raise SchemaError(f"extract_claims trả JSON sai: {resp.text[:200]!r}") from e

        prov = json.dumps(
            {"model": resp.model, "tier": "M", "prompt": "extract_claims@1"}, ensure_ascii=False
        )
        for ent in data.get("entities", []):
            _get_or_create_entity(conn, ent.get("name", ""), ent.get("type"))

        first_claim_id: str | None = None
        for c in data.get("claims", []):
            stmt = (c.get("statement") or "").strip()
            if not stmt:
                continue
            asserted = c.get("asserted_by")
            aid = _get_or_create_entity(conn, asserted, "person") if asserted else default_author_id
            cid = new_id("clm")
            db.insert(conn, "claim", {
                "id": cid, "statement": stmt, "asserted_by_entity_id": aid,
                "observation_id": obs["id"], "provenance": prov, "created_at": _now(),
            })
            first_claim_id = first_claim_id or cid

        # Relationship phải tựa vào một Claim (ONT-R6).
        if first_claim_id:
            for r in data.get("relationships", []):
                s, p, o = r.get("subject"), r.get("predicate"), r.get("object")
                if not (s and p and o):
                    continue
                db.insert(conn, "relationship", {
                    "id": new_id("rel"),
                    "subject_entity_id": _get_or_create_entity(conn, s, "concept"),
                    "predicate": p,
                    "object_entity_id": _get_or_create_entity(conn, o, "concept"),
                    "claim_id": first_claim_id, "created_at": _now(),
                })
        return []

    return extract
