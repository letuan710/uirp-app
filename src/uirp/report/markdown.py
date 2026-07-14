"""report/markdown: sinh báo cáo Markdown cho một Topic (ARC §9).

Trình bày Claim kèm nguồn + Entity + Annotation của Owner. KHÔNG kết luận đúng/sai —
mỗi claim ghi rõ "ai nói" và trỏ về evidence (CHR-004/039, Fact-Agnostic).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from uirp import curate
from uirp.config import Config
from uirp.store import db

_CLAIMS_SQL = """
SELECT claim.statement, claim.asserted_by_entity_id AS by_id,
       ev.content_hash, ev.tombstoned_at, io.title, io.source_url
FROM claim
JOIN observation o        ON claim.observation_id = o.id
JOIN evidence ev          ON o.evidence_id = ev.id
JOIN information_object io ON io.evidence_id = ev.id
WHERE io.topic_id = ?
ORDER BY claim.created_at
"""


def build(conn, cfg: Config, topic_id: str) -> Path:
    topic = db.get(conn, "topic", topic_id)
    if topic is None:
        raise ValueError(f"không có topic {topic_id}")

    claims = db.query(conn, _CLAIMS_SQL, (topic_id,))
    entities = db.query(
        conn,
        "SELECT canonical_name, entity_type FROM entity "
        "WHERE status='active' ORDER BY canonical_name",
    )
    rels = db.query(
        conn,
        "SELECT s.canonical_name AS subj, r.predicate, o.canonical_name AS obj "
        "FROM relationship r JOIN entity s ON r.subject_entity_id=s.id "
        "JOIN entity o ON r.object_entity_id=o.id",
    )
    kinds = db.query(conn, """
        SELECT o.kind, COUNT(*) AS n FROM observation o
        JOIN evidence ev ON o.evidence_id=ev.id
        JOIN information_object io ON io.evidence_id=ev.id
        WHERE io.topic_id=? GROUP BY o.kind
    """, (topic_id,))
    kmap = {k["kind"]: k["n"] for k in kinds}
    images = db.query(conn, """
        SELECT o.content, o.locator FROM observation o
        JOIN evidence ev ON o.evidence_id=ev.id
        JOIN information_object io ON io.evidence_id=ev.id
        WHERE io.topic_id=? AND o.kind='image_ref'
    """, (topic_id,))
    ann = db.query(conn, "SELECT target_id, body, verdict FROM annotation ORDER BY created_at")

    lines: list[str] = [f"# Báo cáo nghiên cứu: {topic['name']}", ""]
    if topic["description"]:
        lines += [topic["description"], ""]
    lines += [
        f"*Sinh tự động {datetime.now(timezone.utc).isoformat()}.*",
        "*Báo cáo trình bày các tuyên bố kèm nguồn — KHÔNG kết luận đúng/sai (CHR-004). "
        "Việc đánh giá thuộc về người đọc.*",
        "",
        f"*Thu được: {kmap.get('body_text', 0)} thân bài · {kmap.get('comment', 0)} bình luận · "
        f"{kmap.get('image_ref', 0)} ảnh.*",
        "",
        f"## Tuyên bố (Claims) — {len(claims)}",
        "*(gồm cả claim từ bình luận, gắn tên người bình luận)*",
        "",
    ]
    for c in claims:
        who = "nguồn"
        if c["by_id"]:
            e = db.get(conn, "entity", curate.resolve_entity(conn, c["by_id"]))
            who = e["canonical_name"] if e else "nguồn"
        src = c["title"] or c["source_url"] or "?"
        tomb = " · ⚠️ evidence đã tombstone" if c["tombstoned_at"] else ""
        lines.append(f"- **{who}**: {c['statement']}")
        lines.append(f"  - nguồn: {src} · evidence `{c['content_hash'][:12]}`{tomb}")

    lines += ["", f"## Thực thể (Entities) — {len(entities)}", ""]
    lines += [f"- {e['canonical_name']} ({e['entity_type']})" for e in entities]

    if rels:
        lines += ["", f"## Quan hệ (Relationships) — {len(rels)}", ""]
        lines += [f"- {r['subj']} —{r['predicate']}→ {r['obj']}" for r in rels]

    if images:
        lines += ["", f"## Hình ảnh (Image refs) — {len(images)}", ""]
        for im in images:
            loc = im["locator"] or ""
            lines.append(f"- {im['content']}  `{loc[:80]}`")

    if ann:
        lines += ["", "## Ghi chú của Owner (tách khỏi dữ liệu máy — CHR-038)", ""]
        for a in ann:
            v = f" [{a['verdict']}]" if a["verdict"] else ""
            lines.append(f"- `{a['target_id']}`{v}: {a['body']}")

    cfg.reports_dir.mkdir(parents=True, exist_ok=True)
    out = cfg.reports_dir / f"{topic_id}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
