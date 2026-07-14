"""Curator tools (bước 7): merge/split entity, đề xuất merge, annotation.

Máy chỉ ĐỀ XUẤT merge kèm confidence kỹ thuật (CHR-005); Owner QUYẾT (CHR-004).
Merge ĐẢO NGƯỢC ĐƯỢC (ONT-R5): chỉ đổi entity.status='merged_into:<id>', KHÔNG dời/xóa
dữ liệu; split = trả status='active'. Giải quyết tham chiếu ở tầng đọc (resolve_entity).
"""

from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from uirp.ids import new_id
from uirp.store import db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_entity(conn, ent_id: str, _depth: int = 0) -> str:
    """Theo chuỗi merged_into về entity canonical đang active."""
    if _depth > 16:
        return ent_id
    e = db.get(conn, "entity", ent_id)
    if e is None:
        return ent_id
    status = e["status"] or "active"
    if status.startswith("merged_into:"):
        return resolve_entity(conn, status.split(":", 1)[1], _depth + 1)
    return ent_id


def merge_entity(conn, keep_id: str, drop_id: str) -> None:
    if keep_id == drop_id:
        raise ValueError("không thể merge một entity vào chính nó")
    if not db.get(conn, "entity", keep_id) or not db.get(conn, "entity", drop_id):
        raise ValueError("entity không tồn tại")
    conn.execute("UPDATE entity SET status=? WHERE id=?", (f"merged_into:{keep_id}", drop_id))
    conn.commit()


def split_entity(conn, ent_id: str) -> None:
    conn.execute("UPDATE entity SET status='active' WHERE id=?", (ent_id,))
    conn.commit()


def scan_merge_proposals(conn, threshold: float = 0.72) -> int:
    """Đề xuất merge cho các cặp entity có tên gần giống (confidence = độ giống difflib)."""
    ents = db.query(
        conn, "SELECT id, canonical_name FROM entity WHERE status='active' ORDER BY canonical_name"
    )
    existing = {
        frozenset((r["a"], r["b"]))
        for r in db.query(
            conn, "SELECT entity_id_a a, entity_id_b b FROM merge_proposal WHERE status='pending'"
        )
    }
    n = 0
    for i in range(len(ents)):
        for j in range(i + 1, len(ents)):
            a, b = ents[i], ents[j]
            if frozenset((a["id"], b["id"])) in existing:
                continue
            ratio = SequenceMatcher(
                None, a["canonical_name"].lower(), b["canonical_name"].lower()
            ).ratio()
            if ratio >= threshold:
                db.insert(conn, "merge_proposal", {
                    "id": new_id("mrg"), "entity_id_a": a["id"], "entity_id_b": b["id"],
                    "confidence": round(ratio, 3), "status": "pending", "created_at": _now(),
                })
                n += 1
    return n


def list_proposals(conn) -> list[dict[str, Any]]:
    return db.query(conn, """
        SELECT m.id, m.confidence, a.canonical_name AS na, b.canonical_name AS nb,
               m.entity_id_a, m.entity_id_b
        FROM merge_proposal m
        JOIN entity a ON m.entity_id_a=a.id
        JOIN entity b ON m.entity_id_b=b.id
        WHERE m.status='pending' ORDER BY m.confidence DESC
    """)


def approve_proposals(conn, min_conf: float) -> int:
    rows = db.query(
        conn,
        "SELECT id, entity_id_a, entity_id_b FROM merge_proposal "
        "WHERE status='pending' AND confidence >= ?",
        (min_conf,),
    )
    for r in rows:
        keep = resolve_entity(conn, r["entity_id_a"])
        drop = r["entity_id_b"]
        if keep != drop:
            merge_entity(conn, keep, drop)
        conn.execute("UPDATE merge_proposal SET status='approved' WHERE id=?", (r["id"],))
    conn.commit()
    return len(rows)


def add_annotation(conn, target_id: str, body: str, verdict: str | None = None) -> str:
    aid = new_id("ann")
    db.insert(conn, "annotation", {
        "id": aid, "target_id": target_id, "body": body,
        "verdict": verdict, "created_at": _now(),
    })
    return aid
