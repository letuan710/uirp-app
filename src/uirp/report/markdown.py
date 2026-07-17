"""report/markdown: sinh báo cáo Markdown cho một Topic (ARC §9).

Trình bày Claim kèm nguồn + Entity + Annotation của Owner. KHÔNG kết luận đúng/sai —
mỗi claim ghi rõ "ai nói" và trỏ về evidence (CHR-004/039, Fact-Agnostic).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from uirp import curate
from uirp.config import Config
from uirp.pipeline.parse import _looks_like_icon
from uirp.store import db

_CLAIMS_SQL = """
SELECT claim.id, claim.statement, claim.asserted_by_entity_id AS by_id, o.kind AS obs_kind,
       ev.content_hash, ev.tombstoned_at, io.title, io.source_url
FROM claim
JOIN observation o        ON claim.observation_id = o.id
JOIN evidence ev          ON o.evidence_id = ev.id
JOIN information_object io ON io.evidence_id = ev.id
WHERE io.topic_id = ?
ORDER BY claim.created_at
"""

# Nội dung đọc nhanh theo nguồn: ưu tiên bản dịch > transcript > mô tả video > thân bài.
_CONTENT_SQL = """
SELECT io.id AS io_id, io.title, io.source_url, io.source_type, o.kind, o.content
FROM information_object io
JOIN observation o ON o.evidence_id = io.evidence_id
WHERE io.topic_id = ?
  AND o.kind IN ('translation','transcript','video_description','body_text','comment')
ORDER BY io.created_at, CASE o.kind
    WHEN 'translation' THEN 0 WHEN 'transcript' THEN 1
    WHEN 'video_description' THEN 2 WHEN 'body_text' THEN 3 ELSE 4 END
"""


# Chuỗi đặc trưng của khung giao diện YouTube/player — không phải nội dung bài.
_UI_JUNK_HINTS = (
    "if playback doesn't begin", "tap to unmute", "watch later share",
    "an error occurred while retrieving", "enable javascript", "please try again later",
)


def _is_ui_junk(text: str) -> bool:
    low = text.lower()
    return sum(h in low for h in _UI_JUNK_HINTS) >= 2


def build(conn, cfg: Config, topic_id: str) -> Path:
    topic = db.get(conn, "topic", topic_id)
    if topic is None:
        raise ValueError(f"không có topic {topic_id}")

    claims = db.query(conn, _CLAIMS_SQL, (topic_id,))
    # Entity/Relationship/Annotation chỉ trong PHẠM VI topic — không lôi dữ liệu topic khác
    # vào báo cáo (lỗi từng gặp: entity test của topic khác lẫn sang).
    entities = db.query(conn, """
        SELECT DISTINCT e.id, e.canonical_name, e.entity_type FROM entity e
        JOIN claim c ON c.asserted_by_entity_id = e.id
        JOIN observation o ON c.observation_id = o.id
        JOIN information_object io ON io.evidence_id = o.evidence_id
        WHERE io.topic_id=? AND e.status='active' ORDER BY e.canonical_name
    """, (topic_id,))
    rels = db.query(conn, """
        SELECT s.canonical_name AS subj, r.predicate, o2.canonical_name AS obj
        FROM relationship r
        JOIN entity s ON r.subject_entity_id=s.id
        JOIN entity o2 ON r.object_entity_id=o2.id
        JOIN claim c ON r.claim_id=c.id
        JOIN observation ob ON c.observation_id=ob.id
        JOIN information_object io ON io.evidence_id=ob.evidence_id
        WHERE io.topic_id=?
    """, (topic_id,))
    contents = db.query(conn, _CONTENT_SQL, (topic_id,))
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
    # Ghi chú Owner: chỉ những ghi chú trỏ tới object THUỘC topic này.
    topic_ids: set[str] = {topic_id}
    topic_ids |= {r["id"] for r in db.query(
        conn, "SELECT evidence_id AS id FROM information_object WHERE topic_id=?", (topic_id,))}
    topic_ids |= {c["id"] for c in claims}
    topic_ids |= {e["id"] for e in entities}
    ann = [a for a in db.query(
        conn, "SELECT target_id, body, verdict FROM annotation ORDER BY created_at")
        if a["target_id"] in topic_ids]

    lines: list[str] = [f"# Báo cáo nghiên cứu: {topic['name']}", ""]
    if topic["description"]:
        lines += [topic["description"], ""]
    lines += [
        f"*Sinh tự động {datetime.now(timezone.utc).isoformat()}.*",
        "*Báo cáo trình bày nội dung kèm nguồn — KHÔNG kết luận đúng/sai (CHR-004). "
        "Việc đánh giá thuộc về người đọc.*",
        "",
        f"*Thu được: {kmap.get('body_text', 0)} thân bài · {kmap.get('comment', 0)} bình luận · "
        f"{kmap.get('transcript', 0)} transcript video · {kmap.get('translation', 0)} bản dịch · "
        f"{kmap.get('image_ref', 0)} ảnh.*",
        "",
    ]

    # --- 1. NỘI DUNG ĐỌC ĐƯỢC theo nguồn (phần Owner cần nhất — ADR-012) ---
    # Mỗi nguồn lấy 1 bản text tốt nhất (dịch > transcript > mô tả video > thân bài);
    # bỏ text ngắn/rác giao diện; khử trùng lặp nội dung (cùng video thu nhiều lần).
    best: dict[str, dict] = {}
    seen_content: set[str] = set()
    for row in contents:
        text = " ".join((row["content"] or "").split())
        if row["io_id"] in best or len(text) < 120 or _is_ui_junk(text):
            continue
        sig = text[:200]
        if sig in seen_content:
            continue
        seen_content.add(sig)
        best[row["io_id"]] = row
    kind_label = {"translation": "bản dịch tiếng Việt", "transcript": "transcript video",
                  "video_description": "mô tả video", "body_text": "thân bài",
                  "comment": "bình luận"}
    lines += [f"## Nội dung thu được — {len(best)} nguồn có nội dung đọc được", ""]
    if not best:
        lines += ["*(chưa có — nguồn đã thu nhưng chưa xử lý xong, hoặc trang chỉ có "
                  "giao diện không có bài viết. Chạy xử lý rồi xem lại.)*", ""]
    for row in list(best.values())[:50]:
        src = row["title"] or row["source_url"] or row["source_type"]
        excerpt = " ".join((row["content"] or "").split())
        if len(excerpt) > 800:
            excerpt = excerpt[:800] + "…"
        lines.append(f"### {src}")
        if row["source_url"]:
            lines.append(f"*{row['source_url']}* · ({kind_label.get(row['kind'], row['kind'])})")
        lines += ["", f"> {excerpt}", ""]

    # --- 2. Tuyên bố AI trích xuất (câu khẳng định gắn người nói) ---
    lines += [
        f"## Các tuyên bố AI trích xuất được — {len(claims)}",
        "*(mỗi dòng: AI bóc một câu khẳng định từ nguồn, gắn người nói — KHÔNG phải "
        "kết luận của hệ thống)*",
        "",
    ]
    for c in claims:
        who = "nguồn"
        if c["by_id"]:
            e = db.get(conn, "entity", curate.resolve_entity(conn, c["by_id"]))
            who = e["canonical_name"] if e else "nguồn"
        src = c["title"] or c["source_url"] or "?"
        tomb = " · ⚠️ evidence đã tombstone" if c["tombstoned_at"] else ""
        dich = " *(dịch từ tiếng Trung)*" if c["obs_kind"] == "translation" else ""
        lines.append(f"- **{who}**: {c['statement']}{dich}")
        lines.append(f"  - nguồn: {src} · evidence `{c['content_hash'][:12]}`{tomb}")

    if entities:
        lines += ["", f"## Người/tổ chức được nhắc đến — {len(entities)}", ""]
        lines += [f"- {e['canonical_name']} ({e['entity_type']})" for e in entities]

    if rels:
        lines += ["", f"## Quan hệ (Relationships) — {len(rels)}", ""]
        lines += [f"- {r['subj']} —{r['predicate']}→ {r['obj']}" for r in rels]

    # --- Ảnh: khử trùng lặp theo src, bỏ icon/logo, chỉ liệt kê gọn ---
    uniq_imgs: list[str] = []
    seen_src: set[str] = set()
    for im in images:
        loc = im["locator"] or "{}"
        try:
            src = json.loads(loc).get("src", "")
        except ValueError:
            src = ""
        if not src or src in seen_src or _looks_like_icon(src, None, None):
            continue
        seen_src.add(src)
        uniq_imgs.append(src)
    if uniq_imgs:
        shown = uniq_imgs[:20]
        lines += ["", f"## Hình ảnh — {len(uniq_imgs)} ảnh nội dung (đã lọc icon/trùng)", ""]
        lines += [f"- {s[:120]}" for s in shown]
        if len(uniq_imgs) > len(shown):
            lines.append(f"- … và {len(uniq_imgs) - len(shown)} ảnh khác")

    if ann:
        lines += ["", "## Ghi chú của Owner (tách khỏi dữ liệu máy — CHR-038)", ""]
        for a in ann:
            v = f" [{a['verdict']}]" if a["verdict"] else ""
            lines.append(f"- `{a['target_id']}`{v}: {a['body']}")

    cfg.reports_dir.mkdir(parents=True, exist_ok=True)
    out = cfg.reports_dir / f"{topic_id}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
