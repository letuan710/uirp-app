"""CLI UIRP (ARC §10). Giai đoạn 1 bước 1-2: init, run, jobs, doctor (+ _demo).

Các lệnh pipeline (topic/ingest/extract/report…) sẽ nối vào theo thứ tự ARC §13.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

from uirp import __version__, config, curate
from uirp.connectors import facebook_browser, facebook_manual
from uirp.errors import ConfigError, PermanentError
from uirp.core import demo, jobs
from uirp.ids import new_id
from uirp.pipeline import register_all as pipeline_register
from uirp.report import markdown
from uirp.store import db, evidence


def _setup_logging(cfg: config.Config) -> None:
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(cfg.logs_dir / "uirp.log", encoding="utf-8")],
    )


def _dirs(cfg: config.Config) -> list[Path]:
    return [
        cfg.data_dir,
        cfg.db_path.parent,
        cfg.evidence_dir,
        cfg.inbox_dir / "facebook",
        cfg.logs_dir,
        cfg.reports_dir,
    ]


def cmd_init(cfg: config.Config, args: argparse.Namespace) -> int:
    for d in _dirs(cfg):
        d.mkdir(parents=True, exist_ok=True)
    conn = db.connect(cfg)  # chạy migration → tạo schema v1
    ver = db.query(conn, "SELECT value FROM _meta WHERE key='schema_version'")[0]["value"]
    print(f"UIRP {__version__} — khởi tạo xong tại {cfg.root}")
    print(f"  data/            : {cfg.data_dir}")
    print(f"  DB schema version: {ver}")
    print(f"  backend AI       : {cfg.backend}")
    if cfg.backend == "api_key":
        ok = "có" if cfg.api_key() else "THIẾU (đặt biến môi trường ANTHROPIC_API_KEY)"
        print(f"  ANTHROPIC_API_KEY: {ok}")
    else:
        print("  (backend thuê bao — cần Claude Code đã đăng nhập; không cần API key)")
    conn.close()
    return 0


def cmd_run(cfg: config.Config, args: argparse.Namespace) -> int:
    demo.register_all()          # handler demo (kiểm chứng state machine)
    pipeline_register(cfg)       # handler thật: parse, extract (bước 4-5)
    conn = db.connect(cfg)
    n = jobs.run(conn, cfg, once=args.once)
    print(f"Đã xử lý {n} job trong lần chạy này.")
    conn.close()
    return 0


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def cmd_topic(cfg: config.Config, args: argparse.Namespace) -> int:
    conn = db.connect(cfg)
    if args.topic_cmd == "add":
        tid = new_id("top")
        db.insert(conn, "topic", {
            "id": tid, "name": args.name, "description": args.desc,
            "status": "active", "created_at": _now(),
        })
        print(f"Đã tạo topic {tid}: {args.name}")
    else:  # list
        rows = db.query(conn, """
            SELECT t.id, t.name, t.status,
              (SELECT COUNT(*) FROM information_object io WHERE io.topic_id=t.id) AS n_src
            FROM topic t ORDER BY t.created_at
        """)
        if not rows:
            print("(chưa có topic — tạo: uirp topic add \"Tên\")")
        for r in rows:
            print(f"  {r['id']}  [{r['status']}]  {r['name']}  ({r['n_src']} nguồn)")
    conn.close()
    return 0


def cmd_ingest(cfg: config.Config, args: argparse.Namespace) -> int:
    conn = db.connect(cfg)
    if not db.get(conn, "topic", args.topic):
        print(f"Không có topic {args.topic}. Xem: uirp topic list", file=sys.stderr)
        conn.close()
        return 1
    n = facebook_manual.ingest(conn, cfg, args.topic)
    print(f"Đã nuốt {n} file từ inbox → evidence + job parse. Chạy: uirp run --once")
    conn.close()
    return 0


def cmd_fetch(cfg: config.Config, args: argparse.Namespace) -> int:
    conn = db.connect(cfg)
    if not db.get(conn, "topic", args.topic):
        print(f"Không có topic {args.topic}. Xem: uirp topic list", file=sys.stderr)
        conn.close()
        return 1
    print(f"⚠️ Mode B: chỉ dùng nguồn công khai, tài khoản phụ; gặp checkpoint sẽ dừng (ADR-002).")
    try:
        n = facebook_browser.collect(conn, cfg, args.topic, args.mode, args.value)
    except (ConfigError, PermanentError) as e:
        print(f"Fetch dừng: {e}", file=sys.stderr)
        conn.close()
        return 1
    print(f"Đã thu {n} bài (mỗi bài: HTML + screenshot). Chạy: uirp run --once")
    conn.close()
    return 0


def cmd_report(cfg: config.Config, args: argparse.Namespace) -> int:
    conn = db.connect(cfg)
    out = markdown.build(conn, cfg, args.topic)
    print(f"Đã sinh báo cáo: {out}")
    conn.close()
    return 0


def cmd_search(cfg: config.Config, args: argparse.Namespace) -> int:
    conn = db.connect(cfg)
    has_fts = db.query(
        conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='claim_fts'"
    )
    if not has_fts:
        print("FTS5 không khả dụng trong build SQLite này — search chưa dùng được.")
        conn.close()
        return 1
    try:
        claims = db.query(
            conn,
            "SELECT c.statement FROM claim_fts f JOIN claim c ON c.id=f.clm_id "
            "WHERE claim_fts MATCH ? ORDER BY rank LIMIT 20",
            (args.query,),
        )
        obs = db.query(
            conn,
            "SELECT o.content FROM observation_fts f JOIN observation o ON o.id=f.obs_id "
            "WHERE observation_fts MATCH ? ORDER BY rank LIMIT 5",
            (args.query,),
        )
    except sqlite3.OperationalError as e:
        print(f"Truy vấn FTS không hợp lệ: {e}", file=sys.stderr)
        conn.close()
        return 1
    print(f"Claim khớp «{args.query}» — {len(claims)}:")
    for c in claims:
        print(f"  • {c['statement']}")
    if obs:
        print(f"(và {len(obs)} observation khớp)")
    conn.close()
    return 0


def cmd_cost(cfg: config.Config, args: argparse.Namespace) -> int:
    conn = db.connect(cfg)
    rows = db.query(conn, """
        SELECT tier, model, COUNT(*) AS calls,
               SUM(tokens_in) AS tin, SUM(tokens_out) AS tout,
               SUM(COALESCE(est_cost_usd,0)) AS usd
        FROM usage_log GROUP BY tier, model ORDER BY tier
    """)
    if not rows:
        print("(chưa có lời gọi AI nào)")
    else:
        print("tier  model                      calls   token_in  token_out   ~USD")
        for r in rows:
            print(f"  {str(r['tier']):<4} {r['model']:<26} {r['calls']:>5} "
                  f"{(r['tin'] or 0):>10} {(r['tout'] or 0):>10} {(r['usd'] or 0):>7.4f}")
    conn.close()
    return 0


def cmd_jobs(cfg: config.Config, args: argparse.Namespace) -> int:
    conn = db.connect(cfg)
    if args.state:
        rows = db.query(
            conn,
            "SELECT state, job_type, retry_count, retry_at, error_kind FROM job "
            "WHERE state=? ORDER BY updated_at DESC",
            (args.state,),
        )
    else:
        rows = db.query(
            conn,
            "SELECT state, COUNT(*) AS n FROM job GROUP BY state ORDER BY state",
        )
    if not rows:
        print("(không có job)")
    elif args.state:
        for r in rows:
            extra = r["error_kind"] or ""
            if r["retry_at"]:
                extra += f" retry_at={r['retry_at']}"
            print(f"  {r['state']:<14} {r['job_type']:<18} retry={r['retry_count']} {extra}")
    else:
        for r in rows:
            print(f"  {r['state']:<14} {r['n']}")
    conn.close()
    return 0


def cmd_doctor(cfg: config.Config, args: argparse.Namespace) -> int:
    """Gom FAILED theo (job_type, error_kind) — chẩn đoán lỗi lặp (ARC-013b)."""
    conn = db.connect(cfg)
    rows = db.query(
        conn,
        "SELECT job_type, error_kind, COUNT(*) AS n FROM job "
        "WHERE state='FAILED' GROUP BY job_type, error_kind ORDER BY n DESC",
    )
    if not rows:
        print("Không có job FAILED. 🎉")
    else:
        print("Lỗi lặp (job_type / error_kind / số lượng):")
        for r in rows:
            print(f"  {r['n']:>4}  {r['job_type']:<18} {r['error_kind']}")
    conn.close()
    return 0


def cmd_demo(cfg: config.Config, args: argparse.Namespace) -> int:
    conn = db.connect(cfg)
    n = demo.seed(conn)
    print(f"Đã seed {n} job demo. Chạy: uirp run --once  rồi  uirp jobs")
    conn.close()
    return 0


def cmd_entity(cfg: config.Config, args: argparse.Namespace) -> int:
    conn = db.connect(cfg)
    try:
        if args.entity_cmd == "merge":
            curate.merge_entity(conn, args.keep, args.drop)
            print(f"Đã gộp {args.drop} → {args.keep} (đảo ngược được: uirp entity split {args.drop})")
        else:  # split
            curate.split_entity(conn, args.ent_id)
            print(f"Đã tách {args.ent_id} về active")
    except ValueError as e:
        print(f"Lỗi: {e}", file=sys.stderr)
        conn.close()
        return 1
    conn.close()
    return 0


def cmd_review(cfg: config.Config, args: argparse.Namespace) -> int:
    conn = db.connect(cfg)
    if args.review_cmd == "scan":
        n = curate.scan_merge_proposals(conn, args.threshold)
        print(f"Đã tạo {n} đề xuất merge (độ giống ≥ {args.threshold}). Xem: uirp review queue")
    elif args.review_cmd == "queue":
        rows = curate.list_proposals(conn)
        if not rows:
            print("(không có đề xuất merge nào chờ duyệt)")
        for r in rows:
            print(f"  conf={r['confidence']:.2f}  «{r['na']}»  ≈  «{r['nb']}»")
            print(f"        merge: uirp entity merge {r['entity_id_a']} {r['entity_id_b']}")
    else:  # approve-all
        n = curate.approve_proposals(conn, args.min_confidence)
        print(f"Đã duyệt+gộp {n} đề xuất có confidence ≥ {args.min_confidence}.")
    conn.close()
    return 0


def cmd_annotate(cfg: config.Config, args: argparse.Namespace) -> int:
    conn = db.connect(cfg)
    aid = curate.add_annotation(conn, args.target, args.body, args.verdict)
    print(f"Đã ghi chú {aid} vào {args.target}"
          + (f" [{args.verdict}]" if args.verdict else ""))
    conn.close()
    return 0


def cmd_vacuum(cfg: config.Config, args: argparse.Namespace) -> int:
    conn = db.connect(cfg)
    conn.isolation_level = None  # autocommit để VACUUM chạy ngoài transaction
    conn.execute("VACUUM")
    conn.execute("ANALYZE")
    print("Đã VACUUM + ANALYZE (thu gọn + cập nhật thống kê DB — ARC-021b).")
    conn.close()
    return 0


def cmd_backup(cfg: config.Config, args: argparse.Namespace) -> int:
    if args.remote:
        print("Backup cloud (--remote) chưa triển khai — cần cấu hình + mã hóa client-side (ARC-020b).")
        return 0
    import zipfile
    from datetime import datetime, timezone

    bdir = cfg.data_dir / "backup"
    bdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = bdir / f"uirp-backup-{ts}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for sub in ("db", "evidence"):
            base = cfg.data_dir / sub
            if not base.exists():
                continue
            for f in base.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(cfg.data_dir))
    print(f"Đã backup DB + evidence → {out}")
    print("⚠️ Bản local chưa mã hóa; đưa ra ổ ngoài/cloud thì mã hóa client-side (ARC-020).")
    return 0


def cmd_erase_entity(cfg: config.Config, args: argparse.Namespace) -> int:
    conn = db.connect(cfg)
    if not db.get(conn, "entity", args.ent_id):
        print(f"không có entity {args.ent_id}", file=sys.stderr)
        conn.close()
        return 1
    cluster = [
        e["id"] for e in db.query(conn, "SELECT id FROM entity")
        if curate.resolve_entity(conn, e["id"]) == args.ent_id
    ]
    ph = ",".join("?" * len(cluster))
    evs = db.query(conn, f"""
        SELECT DISTINCT ev.id, ev.file_path FROM evidence ev
        JOIN observation o ON o.evidence_id=ev.id
        JOIN claim c ON c.observation_id=o.id
        WHERE c.asserted_by_entity_id IN ({ph}) AND ev.tombstoned_at IS NULL
    """, tuple(cluster))
    for ev in evs:
        evidence.tombstone_file(cfg, ev["file_path"])
        conn.execute(
            "UPDATE evidence SET tombstoned_at=?, tombstone_reason=? WHERE id=?",
            (_now(), f"erase-entity {args.ent_id}", ev["id"]),
        )
    conn.commit()
    print(f"Đã tombstone {len(evs)} evidence do entity này phát ngôn (CHR-032/033).")
    if not evs:
        print("(entity này không là chủ thể phát ngôn của claim nào → không có gì để xóa)")
    conn.close()
    return 0


def _stub(name: str, step: str):
    def _run(cfg: config.Config, args: argparse.Namespace) -> int:
        print(f"Lệnh '{name}' chưa triển khai — thuộc {step} (xem ARC-001 §13).")
        return 0

    return _run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="uirp", description="UIRP — nền tảng nghiên cứu tri thức cá nhân")
    p.add_argument("--version", action="version", version=f"uirp {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Khởi tạo data/ + DB")

    pr = sub.add_parser("run", help="Chạy scheduler xử lý hàng đợi job")
    pr.add_argument("--once", action="store_true", help="Xử lý hết việc sẵn sàng rồi thoát (không chờ quota)")

    pj = sub.add_parser("jobs", help="Xem job (không --state: đếm theo trạng thái)")
    pj.add_argument("--state", help="Lọc theo trạng thái: PENDING|RUNNING|DONE|FAILED|WAITING_QUOTA")

    sub.add_parser("doctor", help="Gom lỗi FAILED lặp lại (ARC-013b)")
    sub.add_parser("_demo", help="(nội bộ) seed job demo kiểm chứng state machine")

    # topic add / topic list
    pt = sub.add_parser("topic", help="Quản lý Research Topic")
    tsub = pt.add_subparsers(dest="topic_cmd", required=True)
    pta = tsub.add_parser("add", help="Tạo topic mới")
    pta.add_argument("name")
    pta.add_argument("--desc", default=None)
    tsub.add_parser("list", help="Liệt kê topic")

    pi = sub.add_parser("ingest", help="Mode A: nuốt file trong inbox/facebook (ADR-002)")
    pi.add_argument("--topic", required=True)

    prp = sub.add_parser("report", help="Sinh báo cáo Markdown cho một topic")
    prp.add_argument("--topic", required=True)

    sub.add_parser("cost", help="Báo cáo token/chi phí theo tier (CHR-054)")

    ps = sub.add_parser("search", help="Tìm trong claim/observation (FTS5)")
    ps.add_argument("query")

    # entity merge/split (ONT-R5)
    pe = sub.add_parser("entity", help="Gộp/tách entity (Curator)")
    esub = pe.add_subparsers(dest="entity_cmd", required=True)
    pem = esub.add_parser("merge", help="Gộp DROP vào KEEP (đảo ngược được)")
    pem.add_argument("keep")
    pem.add_argument("drop")
    pes = esub.add_parser("split", help="Tách một entity đã gộp về active")
    pes.add_argument("ent_id")

    # review queue merge (ARC-016c)
    pr = sub.add_parser("review", help="Hàng đợi đề xuất merge entity")
    rsub = pr.add_subparsers(dest="review_cmd", required=True)
    prsc = rsub.add_parser("scan", help="Quét sinh đề xuất merge theo độ giống tên")
    prsc.add_argument("--threshold", type=float, default=0.72)
    rsub.add_parser("queue", help="Xem đề xuất đang chờ")
    pra = rsub.add_parser("approve-all", help="Duyệt+gộp hàng loạt theo ngưỡng confidence")
    pra.add_argument("--min-confidence", dest="min_confidence", type=float, required=True)

    pan = sub.add_parser("annotate", help="Ghi chú của Owner vào một object (CHR-038)")
    pan.add_argument("target")
    pan.add_argument("body")
    pan.add_argument("--verdict", choices=["credible", "doubtful", "false", "noted"], default=None)

    sub.add_parser("vacuum", help="Thu gọn DB (VACUUM+ANALYZE)")

    pb = sub.add_parser("backup", help="Sao lưu DB + evidence")
    pb.add_argument("--remote", action="store_true", help="Upload cloud mã hóa (chưa triển khai)")

    pee = sub.add_parser("erase-entity", help="Tombstone evidence liên quan một entity (CHR-033)")
    pee.add_argument("ent_id")

    pf = sub.add_parser("fetch", help="Mode B: thu tự động qua trình duyệt (ADR-002/007/008)")
    pf.add_argument("--topic", required=True)
    pf.add_argument("--mode", choices=["url", "keyword", "profile", "group"], required=True)
    pf.add_argument("--value", required=True, help="URL bài / từ khóa / username / group id")

    return p


_DISPATCH = {
    "init": cmd_init,
    "run": cmd_run,
    "jobs": cmd_jobs,
    "doctor": cmd_doctor,
    "_demo": cmd_demo,
    "topic": cmd_topic,
    "ingest": cmd_ingest,
    "report": cmd_report,
    "cost": cmd_cost,
    "search": cmd_search,
    "entity": cmd_entity,
    "review": cmd_review,
    "annotate": cmd_annotate,
    "vacuum": cmd_vacuum,
    "backup": cmd_backup,
    "erase-entity": cmd_erase_entity,
    "fetch": cmd_fetch,
}


def _force_utf8() -> None:
    """Console Windows mặc định cp1252 không in được tiếng Việt — ép UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    args = build_parser().parse_args(argv)
    cfg = config.load(Path.cwd())
    try:
        _setup_logging(cfg)
    except OSError:
        pass  # init chưa chạy thì chưa có thư mục — bỏ qua, init sẽ tạo
    handler = _DISPATCH.get(args.command)
    if handler is None:
        handler = _stub(args.command, "pipeline")
    try:
        return handler(cfg, args)
    except sqlite3.OperationalError as e:
        print(f"Lỗi DB: {e}. Đã chạy 'uirp init' chưa?", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
