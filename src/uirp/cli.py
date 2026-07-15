"""CLI UIRP (ARC §10). Giai đoạn 1 bước 1-2: init, run, jobs, doctor (+ _demo).

Các lệnh pipeline (topic/ingest/extract/report…) sẽ nối vào theo thứ tự ARC §13.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

from uirp import __version__, config, curate, platforms
from uirp.connectors import browser, manual
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
    base = [cfg.data_dir, cfg.db_path.parent, cfg.evidence_dir, cfg.logs_dir, cfg.reports_dir]
    # Thư mục inbox cho MỌI nền tảng đã khai báo (ADR-009).
    base += [cfg.inbox_dir / p.key for p in platforms.all_platforms()]
    return base


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
    if args.backend:  # ghi đè backend cho lần chạy này (dễ so sánh fake vs Claude thật)
        cfg.data["api"]["backend"] = args.backend
    print(f"Backend AI: {cfg.backend}")
    demo.register_all()          # handler demo (kiểm chứng state machine)
    pipeline_register(cfg)       # handler thật: parse, extract, translate, read_image
    conn = db.connect(cfg)
    if args.loop:  # chạy liên tục (Continuous Research, ADR-010)
        import time
        print(f"Chạy liên tục (nghỉ {args.interval}s mỗi vòng) — Ctrl+C để dừng.")
        try:
            while True:
                n = jobs.run(conn, cfg, once=True)
                if n:
                    print(f"  đã xử lý {n} job")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nĐã dừng.")
        conn.close()
        return 0
    n = jobs.run(conn, cfg, once=args.once)
    print(f"Đã xử lý {n} job trong lần chạy này.")
    conn.close()
    return 0


def cmd_web(cfg: config.Config, args: argparse.Namespace) -> int:
    from uirp import web
    if not args.no_open:
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    web.serve(cfg, args.port)
    return 0


def cmd_check(cfg: config.Config, args: argparse.Namespace) -> int:
    import importlib.util

    def ok(b: bool) -> str:
        return "✔" if b else "✘"

    print("=== Kiểm tra sẵn sàng UIRP ===")
    print(f"  [{ok(sys.version_info >= (3, 12))}] Python "
          f"{sys.version_info.major}.{sys.version_info.minor} (cần ≥ 3.12)")
    conn = db.connect(cfg)
    v = db.query(conn, "SELECT value FROM _meta WHERE key='schema_version'")[0]["value"]
    print(f"  [✔] DB khởi tạo (schema v{v})  →  {cfg.db_path}")

    be = cfg.backend
    print(f"  Backend AI: {be}")
    has_anth = importlib.util.find_spec("anthropic") is not None
    has_agent = importlib.util.find_spec("claude_agent_sdk") is not None
    print(f"      [{ok(has_anth)}] package anthropic (cho backend api_key)")
    print(f"      [{ok(has_agent)}] package claude_agent_sdk (cho backend thuê bao)")
    if be == "api_key":
        print(f"      [{ok(bool(cfg.api_key()))}] ANTHROPIC_API_KEY (biến môi trường)")

    has_pw = importlib.util.find_spec("playwright") is not None
    print(f"  [{ok(has_pw)}] Playwright (Mode B tự động — không cần nếu chỉ dùng Mode A)")
    nt = db.query(conn, "SELECT COUNT(*) n FROM topic")[0]["n"]
    print(f"  [{ok(nt > 0)}] có {nt} chủ đề; {len(platforms.all_platforms())} nền tảng khai báo")
    conn.close()

    print("\n=== Kết luận: đã đủ chạy chưa? ===")
    if be == "fake":
        print("  ✔ ĐỦ chạy DEMO offline ngay (Mode A lưu tay + xử lý bằng FakeBackend).")
        print("  → Để chạy THẬT (bóc tách/dịch bằng Claude), đổi config.toml:")
        print("      backend=\"claude_agent_sdk\"  (cài claude-agent-sdk + đăng nhập Claude Code), hoặc")
        print("      backend=\"api_key\"           (cài anthropic + đặt ANTHROPIC_API_KEY).")
    else:
        ready = (be == "api_key" and has_anth and cfg.api_key()) or (
            be == "claude_agent_sdk" and has_agent)
        print(f"  [{ok(bool(ready))}] Backend {be}: "
              + ("SẴN SÀNG chạy thật." if ready else "còn THIẾU điều kiện ở trên."))
    print("  Mode B (tự động tìm): "
          + ("cần Chrome đăng nhập + config [fetch] mode=cdp." if has_pw else "cần cài Playwright."))
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
    try:
        n = manual.ingest(conn, cfg, args.topic, args.platform)
    except ConfigError as e:
        print(f"Lỗi: {e}", file=sys.stderr)
        conn.close()
        return 1
    print(f"Đã nuốt {n} file từ inbox/{args.platform} → evidence + job parse. Chạy: uirp run --once")
    conn.close()
    return 0


def cmd_platforms(cfg: config.Config, args: argparse.Namespace) -> int:
    print("Nền tảng đã khai báo (ingest --platform <key> dùng được cho TẤT CẢ; "
          "fetch tự động chỉ khi cột Auto=✔):")
    print(f"  {'key':<13}{'khu vực':<8}{'Auto':<6}nền tảng")
    for p in platforms.all_platforms():
        auto = "co" if p.auto else "ModeA"
        note = f"  — {p.note}" if p.note else ""
        print(f"  {p.key:<13}{p.region:<8}{auto:<6}{p.display}{note}")
    print("\n(Auto=co: Mode B tự động khả thi, cần tinh chỉnh selector khi chạy thật. "
          "ModeA: anti-bot mạnh/đóng → dùng Mode A lưu tay.)")
    return 0


def cmd_fetch(cfg: config.Config, args: argparse.Namespace) -> int:
    conn = db.connect(cfg)
    if not db.get(conn, "topic", args.topic):
        print(f"Không có topic {args.topic}. Xem: uirp topic list", file=sys.stderr)
        conn.close()
        return 1
    print(f"⚠️ Mode B ({args.platform}): chỉ nguồn công khai, tài khoản phụ; gặp checkpoint sẽ dừng (ADR-002).")
    try:
        n = browser.collect(conn, cfg, args.topic, args.platform, args.mode, args.value)
    except (ConfigError, PermanentError) as e:
        print(f"Fetch dừng: {e}", file=sys.stderr)
        conn.close()
        return 1
    print(f"Đã thu {n} bài (mỗi bài: HTML + screenshot). Chạy: uirp run --once")
    conn.close()
    return 0


def cmd_discover(cfg: config.Config, args: argparse.Namespace) -> int:
    conn = db.connect(cfg)
    if not db.get(conn, "topic", args.topic):
        print(f"Không có topic {args.topic}. Xem: uirp topic list", file=sys.stderr)
        conn.close()
        return 1
    if args.platforms:
        keys = [k.strip() for k in args.platforms.split(",") if k.strip()]
    else:  # mặc định: mọi nền hỗ trợ tìm tự động (auto + có search_url)
        keys = [p.key for p in platforms.all_platforms() if p.auto and p.search_url]
    print(f"⚠️ Tự động tìm «{args.keyword}» trên {len(keys)} nền tảng — chỉ nguồn công khai, "
          f"tài khoản phụ, dừng khi checkpoint (ADR-002/009).")
    total = 0
    for k in keys:
        try:
            n = browser.collect(conn, cfg, args.topic, k, "keyword", args.keyword)
            print(f"  {k}: {n} bài")
            total += n
        except (ConfigError, PermanentError) as e:
            print(f"  {k}: bỏ qua — {e}")
    print(f"Tổng {total} bài. Chạy: uirp run --once (nội dung tiếng Trung sẽ tự dịch).")
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
    pr.add_argument("--backend", choices=["fake", "claude_agent_sdk", "api_key"], default=None,
                    help="Ghi đè backend AI cho lần chạy này (so sánh fake vs Claude thật)")
    pr.add_argument("--loop", action="store_true", help="Chạy liên tục (nghiên cứu qua đêm), Ctrl+C dừng")
    pr.add_argument("--interval", type=int, default=60, help="Giây nghỉ giữa các vòng khi --loop")

    sub.add_parser("check", help="Kiểm tra sẵn sàng: đã đủ chạy chưa, còn thiếu gì (ADR-010)")
    pw = sub.add_parser("web", help="Mở giao diện web nhập liệu (local, ADR-010)")
    pw.add_argument("--port", type=int, default=8787)
    pw.add_argument("--no-open", action="store_true", help="Không tự mở trình duyệt")

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

    pi = sub.add_parser("ingest", help="Mode A: nuốt file trong inbox/<platform> (đa nền tảng, ADR-009)")
    pi.add_argument("--topic", required=True)
    pi.add_argument("--platform", default="facebook", help="key nền tảng (xem: uirp platforms)")

    sub.add_parser("platforms", help="Liệt kê nền tảng đã khai báo (VN + Trung Quốc)")

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

    pf = sub.add_parser("fetch", help="Mode B: thu tự động qua trình duyệt (đa nền tảng)")
    pf.add_argument("--topic", required=True)
    pf.add_argument("--platform", default="facebook", help="key nền tảng (xem: uirp platforms)")
    pf.add_argument("--mode", choices=["url", "keyword", "profile", "group"], required=True)
    pf.add_argument("--value", required=True, help="URL bài / từ khóa / username / group id")

    pdis = sub.add_parser("discover", help="Tự động TÌM theo từ khóa trên nhiều nền tảng (Mode B)")
    pdis.add_argument("--topic", required=True)
    pdis.add_argument("--keyword", required=True)
    pdis.add_argument("--platforms", default=None,
                      help="key cách nhau bởi phẩy (mặc định: mọi nền hỗ trợ tìm)")

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
    "discover": cmd_discover,
    "platforms": cmd_platforms,
    "check": cmd_check,
    "web": cmd_web,
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
