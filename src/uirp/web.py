"""Giao diện web tối giản, LOCAL-ONLY (ADR-010). stdlib http.server — P14, không framework.

Chỉ bind 127.0.0.1 + kiểm tra Host/Origin (chống CSRF/DNS-rebinding — CHR-035/ARC-020).

Luồng "một nút": gõ từ khóa → /api/search quét SONG SONG các nền tảng (thread pool,
mỗi nền một profile trình duyệt riêng), evidence về tới đâu WORKER NỀN xử lý tới đó
(parse → dịch → trích xuất) — không phải bấm "Chạy xử lý". UI poll /api/progress.
Queue cạn → tự gộp entity trùng (ngưỡng 0.9) → báo cáo tự hiện.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from uirp import curate, platforms
from uirp.config import Config
from uirp.connectors import browser, manual
from uirp.core import jobs
from uirp.ids import new_id
from uirp.pipeline import register_all
from uirp.report import markdown
from uirp.store import db

# ---------------------------------------------------------------- trạng thái quét
_SCAN: dict[str, dict] = {}          # platform_key -> {display,state,n,msg}
_SCAN_LOCK = threading.Lock()
_STOP = threading.Event()            # "Dừng": hủy các nền chưa bắt đầu
_EXECUTOR: ThreadPoolExecutor | None = None


def _scan_snapshot() -> list[dict]:
    with _SCAN_LOCK:
        return [dict(v, key=k) for k, v in _SCAN.items()]


def _scan_active() -> bool:
    with _SCAN_LOCK:
        return any(v["state"] in ("queued", "scanning") for v in _SCAN.values())


def _scan_one(cfg: Config, topic_id: str, keyword: str, pkey: str) -> None:
    """Chạy trong thread pool — mỗi nền tảng một connection DB + một browser profile."""
    if _STOP.is_set():
        with _SCAN_LOCK:
            _SCAN[pkey].update(state="cancelled", msg="đã dừng trước khi quét")
        return
    with _SCAN_LOCK:
        _SCAN[pkey].update(state="scanning")
    conn = db.connect(cfg)
    try:
        n = browser.collect(conn, cfg, topic_id, pkey, "keyword", keyword)
        with _SCAN_LOCK:
            _SCAN[pkey].update(state="done", n=n)
        _log(f"quét {pkey}: {n} bài")
    except Exception as e:  # noqa: BLE001
        with _SCAN_LOCK:
            _SCAN[pkey].update(state="error", msg=_short_err(e))
        _log(f"quét {pkey}: lỗi — {_short_err(e)}")
    finally:
        conn.close()


# ---------------------------------------------------------------- worker nền
def _worker(cfg: Config) -> None:
    """Xử lý job LIÊN TỤC: quét về tới đâu parse/dịch/trích xuất tới đó (pipeline
    chồng lấn với quét — không chờ Owner bấm gì). Queue cạn → auto-merge entity trùng."""
    register_all(cfg)
    conn = db.connect(cfg)
    did_work = False
    while True:
        try:
            n = jobs.run(conn, cfg, once=True, max_jobs=50)
        except Exception as e:  # noqa: BLE001
            _log(f"worker lỗi: {_short_err(e)}")
            n = 0
        if n:
            did_work = True
            continue
        if did_work and not _scan_active():
            try:
                curate.scan_merge_proposals(conn, 0.90)
                merged = curate.approve_proposals(conn, 0.90)
                if merged:
                    _log(f"auto-merge: gộp {merged} cặp entity trùng (conf ≥ 0.9)")
            except Exception as e:  # noqa: BLE001
                _log(f"auto-merge lỗi: {_short_err(e)}")
            did_work = False
        time.sleep(1.0)


# ---------------------------------------------------------------- helpers
def _send(h: BaseHTTPRequestHandler, obj, code: int = 200, ctype: str = "application/json") -> None:
    body = (obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    h.send_response(code)
    h.send_header("Content-Type", f"{ctype}; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_err(e: Exception) -> str:
    first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return first[:200]


_LOG_FILE = None
_LOG_LOCK = threading.Lock()  # nhiều thread cùng ghi log


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    with _LOG_LOCK:
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode("ascii"))
        if _LOG_FILE:
            try:
                with open(_LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass


_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "[::1]")


class _Handler(BaseHTTPRequestHandler):
    cfg: Config = None  # type: ignore[assignment]

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", "0") or "0")
        return json.loads(self.rfile.read(n) or "{}") if n else {}

    def log_message(self, *a) -> None:
        pass

    def _guard(self) -> bool:
        """Chặn CSRF/DNS-rebinding: dù bind localhost, trang web bất kỳ vẫn có thể
        bắn request tới 127.0.0.1 — kiểm tra Host và (nếu có) Origin."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in _ALLOWED_HOSTS:
            _send(self, {"error": "forbidden (Host)"}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin:
            oh = urlparse(origin).hostname or ""
            if oh not in ("127.0.0.1", "localhost", "::1"):
                _send(self, {"error": "forbidden (Origin)"}, 403)
                return False
        return True

    # ------------------------------------------------------------- GET
    def do_GET(self) -> None:
        if not self._guard():
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            _send(self, INDEX_HTML.encode("utf-8"), ctype="text/html")
            return
        if path == "/api/platforms":
            _send(self, [{"key": p.key, "display": p.display, "region": p.region,
                          "auto": p.auto, "note": p.note} for p in platforms.all_platforms()])
            return
        conn = db.connect(self.cfg)
        try:
            if path == "/api/topics":
                _send(self, db.query(conn,
                    "SELECT t.id,t.name,(SELECT COUNT(*) FROM information_object io "
                    "WHERE io.topic_id=t.id) n FROM topic t ORDER BY t.created_at DESC"))
            elif path == "/api/progress":
                q = parse_qs(urlparse(self.path).query)
                tid = q.get("topic", [""])[0]
                jb = {r["state"]: r["n"] for r in db.query(conn,
                      "SELECT state,COUNT(*) n FROM job GROUP BY state")}
                counts = {t: db.query(conn, f"SELECT COUNT(*) n FROM {t}")[0]["n"]
                          for t in ("evidence", "claim", "entity")}
                if tid:
                    counts["topic_claims"] = db.query(conn, """
                        SELECT COUNT(*) n FROM claim c
                        JOIN observation o ON c.observation_id=o.id
                        JOIN information_object io ON io.evidence_id=o.evidence_id
                        WHERE io.topic_id=?""", (tid,))[0]["n"]
                active = _scan_active() or (jb.get("PENDING", 0) + jb.get("RUNNING", 0)) > 0
                _send(self, {"scan": _scan_snapshot(), "jobs": jb,
                             "counts": counts, "active": active})
            elif path == "/api/report":
                tid = parse_qs(urlparse(self.path).query).get("topic", [""])[0]
                out = markdown.build(conn, self.cfg, tid)
                _send(self, {"markdown": out.read_text(encoding="utf-8")})
            elif path == "/api/entities":
                _send(self, db.query(conn,
                    "SELECT id, entity_type, canonical_name FROM entity "
                    "WHERE status='active' ORDER BY canonical_name"))
            elif path == "/api/review/queue":
                _send(self, curate.list_proposals(conn))
            elif path == "/api/cost":
                _send(self, db.query(conn, """
                    SELECT tier, model, COUNT(*) AS calls,
                           SUM(tokens_in) AS tin, SUM(tokens_out) AS tout,
                           SUM(COALESCE(est_cost_usd,0)) AS usd
                    FROM usage_log GROUP BY tier, model ORDER BY tier
                """))
            else:
                _send(self, {"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            _send(self, {"error": _short_err(e)}, 400)
        finally:
            conn.close()

    # ------------------------------------------------------------- POST
    def do_POST(self) -> None:
        if not self._guard():
            return
        path = urlparse(self.path).path
        data = self._body()
        conn = db.connect(self.cfg)
        try:
            if path == "/api/search":
                keyword = (data.get("keyword") or "").strip()
                if not keyword:
                    _send(self, {"error": "thiếu từ khóa"}, 400)
                    return
                if _scan_active():
                    _send(self, {"error": "đang có phiên quét chạy — bấm Dừng trước"}, 409)
                    return
                hit = db.query(conn,
                    "SELECT id FROM topic WHERE LOWER(name)=LOWER(?) "
                    "ORDER BY created_at DESC LIMIT 1", (keyword,))
                if hit:
                    tid = hit[0]["id"]
                else:
                    tid = new_id("top")
                    db.insert(conn, "topic", {"id": tid, "name": keyword,
                              "description": None, "status": "active", "created_at": _now()})
                targets = [p for p in platforms.all_platforms() if p.auto and p.search_url]
                _STOP.clear()
                with _SCAN_LOCK:
                    _SCAN.clear()
                    for p in targets:
                        _SCAN[p.key] = {"display": p.display, "state": "queued",
                                        "n": 0, "msg": ""}
                for p in targets:  # quét song song, worker nền xử lý song song luôn
                    _EXECUTOR.submit(_scan_one, self.cfg, tid, keyword, p.key)
                _log(f"search «{keyword}» → topic {tid}, {len(targets)} nền tảng")
                _send(self, {"id": tid, "platforms": [p.key for p in targets]})
            elif path == "/api/stop":
                _STOP.set()  # nền chưa bắt đầu sẽ bị hủy; nền đang quét chạy nốt bài dở
                _send(self, {"ok": True})
            elif path == "/api/ingest":
                n = manual.ingest(conn, self.cfg, data["topic"], data.get("platform", "facebook"))
                _log(f"ingest {data.get('platform', 'facebook')}: nạp {n} file")
                _send(self, {"ingested": n})  # worker nền tự xử lý, không cần bấm gì thêm
            elif path == "/api/entity/merge":
                curate.merge_entity(conn, data["keep"], data["drop"])
                _send(self, {"ok": True})
            elif path == "/api/entity/split":
                curate.split_entity(conn, data["id"])
                _send(self, {"ok": True})
            elif path == "/api/review/scan":
                n = curate.scan_merge_proposals(conn, float(data.get("threshold", 0.72)))
                _send(self, {"created": n})
            elif path == "/api/review/approve":
                n = curate.approve_proposals(conn, float(data.get("min_confidence", 0.85)))
                _send(self, {"approved": n})
            elif path == "/api/annotate":
                aid = curate.add_annotation(conn, data["target"], data["body"], data.get("verdict"))
                _send(self, {"id": aid})
            else:
                _send(self, {"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            _send(self, {"error": _short_err(e)}, 400)
        finally:
            conn.close()


def serve(cfg: Config, port: int = 8787) -> None:
    global _LOG_FILE, _EXECUTOR
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    _LOG_FILE = cfg.logs_dir / f"web-{datetime.now():%Y%m%d}.log"
    _Handler.cfg = cfg
    _EXECUTOR = ThreadPoolExecutor(
        max_workers=int(cfg.fetch.get("parallel_platforms", 3)),
        thread_name_prefix="scan")
    threading.Thread(target=_worker, args=(cfg,), daemon=True, name="job-worker").start()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    print(f"UIRP web đang chạy: http://127.0.0.1:{port}  (Ctrl+C để dừng)")
    print(f"Log ghi tại: {_LOG_FILE}")
    _log(f"--- khởi động web, backend={cfg.backend}, worker nền BẬT ---")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng web.")


# ---------------------------------------------------------------- UI
INDEX_HTML = """<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>UIRP</title>
<style>
 :root{--bg:#0f1115;--card:#1a1d24;--fg:#e7e9ee;--mut:#9aa0ac;--acc:#4c8bf5;--ok:#39b970;--warn:#e0a33e;--err:#e05c5c;--bd:#2a2f3a}
 *{box-sizing:border-box} body{margin:0;font:15px/1.5 system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--fg)}
 header{padding:14px 20px;background:var(--card);border-bottom:1px solid var(--bd);display:flex;gap:16px;align-items:center;flex-wrap:wrap}
 h1{font-size:18px;margin:0} .stat{color:var(--mut);font-size:13px}
 main{max-width:900px;margin:0 auto;padding:18px;display:grid;gap:16px}
 .card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:16px}
 .card h2{margin:0 0 10px;font-size:15px}
 label{display:block;font-size:13px;color:var(--mut);margin:8px 0 4px}
 input,select,textarea{width:100%;padding:9px;background:#0d0f13;border:1px solid var(--bd);border-radius:7px;color:var(--fg);font:inherit}
 button{padding:9px 14px;background:var(--acc);color:#fff;border:0;border-radius:7px;font:inherit;cursor:pointer;margin-top:8px}
 button.sec{background:#2a2f3a} button:disabled{opacity:.5;cursor:default} button:hover{opacity:.9}
 .row{display:flex;gap:10px;flex-wrap:wrap} .row>*{flex:1;min-width:140px}
 pre{white-space:pre-wrap;background:#0d0f13;border:1px solid var(--bd);border-radius:7px;padding:12px;max-height:480px;overflow:auto;font-size:13px}
 #msg{font-size:13px;color:var(--mut);min-height:18px}
 .item{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--bd);font-size:13px}
 .item:last-child{border-bottom:0} .t{color:var(--mut);font-size:12px}
 .st-queued{color:var(--mut)} .st-scanning{color:var(--acc)} .st-done{color:var(--ok)}
 .st-error{color:var(--err)} .st-cancelled{color:var(--mut)}
 .bigrow{display:flex;gap:10px} .bigrow input{flex:1} .bigrow button{margin-top:0;white-space:nowrap}
 table{width:100%;border-collapse:collapse;font-size:13px}
 th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--bd)} th{color:var(--mut);font-weight:600}
 details{border-top:1px solid var(--bd);margin-top:12px;padding-top:10px}
 summary{cursor:pointer;color:var(--mut);font-size:13px}
 .btnrow{display:flex;gap:6px;flex-shrink:0} .btnrow button{margin-top:0;padding:5px 9px;font-size:12px}
 #prog{margin-top:10px}
</style></head><body>
<header><h1>🔎 UIRP</h1><span class="stat" id="stat">…</span></header>
<main>
 <div class="card"><h2>Tìm kiếm &amp; trích xuất — một nút</h2>
  <div class="bigrow">
   <input id="q" placeholder="gõ chủ đề / từ khóa rồi Enter — ví dụ: lừa đảo đầu tư Mr Pips"
          onkeydown="if(event.key==='Enter')search()">
   <button id="btn_search" onclick="search()">🔍 Tìm &amp; trích xuất</button>
   <button id="btn_stop" class="sec" onclick="stopSearch()" style="display:none">⏹ Dừng</button>
  </div>
  <p class="stat">Quét song song các nền tảng; kết quả về tới đâu hệ thống tự parse → dịch → trích xuất
   tới đó (không cần bấm gì thêm). Xong hết thì báo cáo tự hiện ở dưới.</p>
  <div id="curtopic" class="stat"></div>
  <div id="overall" class="stat"></div>
  <div id="prog"></div>
  <div id="msg"></div>
 </div>

 <div class="card"><h2>Báo cáo</h2>
  <div class="row"><button class="sec" onclick="loadReport()">↻ Tải lại báo cáo</button></div>
  <pre id="out">(chưa có — tìm kiếm ở trên trước)</pre>
 </div>

 <div class="card"><h2 style="margin:0">Nâng cao</h2>
  <details><summary>Mở lại chủ đề cũ / nạp file lưu tay</summary>
   <label>Chủ đề đã có</label>
   <select id="topic" onchange="pickTopic()"></select>
   <label>Nạp file lưu tay (thả .html/.png vào data/inbox/&lt;nền tảng&gt;/ trước)</label>
   <div class="row"><select id="pf_ingest"></select>
    <button class="sec" onclick="ingest()">Nạp — tự xử lý ngay</button></div>
  </details>
  <details><summary>Curator — gộp/tách thực thể, ghi chú (hiếm khi cần: hệ thống tự gộp trùng ≥0.9)</summary>
   <div id="entlist"></div>
   <div class="row">
    <div><label>Giữ (keep id)</label><input id="mg_keep" placeholder="ent_…"></div>
    <div><label>Gộp vào (drop id)</label><input id="mg_drop" placeholder="ent_…"></div>
   </div>
   <div class="row"><button onclick="mergeEnt()">Gộp</button>
    <button class="sec" onclick="loadEntities()">↻ Danh sách thực thể</button></div>
   <div id="rvqueue"></div>
   <div class="row"><button class="sec" onclick="rvScan()">Quét đề xuất gộp (0.72)</button>
    <button class="sec" onclick="rvApprove()">Duyệt hàng loạt (≥0.85)</button></div>
   <label>Ghi chú: target id</label><input id="an_target" placeholder="clm_… / ent_…">
   <label>Nội dung (verdict tùy chọn, cú pháp: nội dung | verdict)</label>
   <textarea id="an_body" rows="2"></textarea>
   <button onclick="annotate()">Ghi chú</button>
  </details>
  <details><summary>Chi phí gọi AI</summary>
   <button class="sec" onclick="loadCost()">↻ Tải chi phí</button>
   <div id="costtbl"></div>
  </details>
 </div>
</main>
<script>
const $=s=>document.querySelector(s);
let CUR=null, CUR_NAME="", POLL=null, WAS_ACTIVE=false;
async function api(path,opt){const r=await fetch(path,opt);return r.json()}
const post=(path,body)=>api(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body||{})});
function msg(t){$("#msg").textContent=t}
function showCur(){$("#curtopic").textContent=CUR?("Chủ đề hiện tại: "+CUR_NAME):""}

const ICON={queued:"⏳ chờ",scanning:"🔍 đang quét",done:"✅",error:"⚠️",cancelled:"⏹ hủy"};
function renderScan(list){
 const box=$("#prog");box.textContent="";
 list.forEach(p=>{
  const d=document.createElement("div");d.className="item";
  const left=document.createElement("span");left.textContent=p.display;
  const right=document.createElement("span");right.className="st-"+p.state;
  right.textContent=p.state==="done"?("✅ "+p.n+" bài"):(ICON[p.state]||p.state)+(p.msg?(" — "+p.msg):"");
  d.appendChild(left);d.appendChild(right);box.appendChild(d);
 });
}
async function poll(){
 let pr;
 try{pr=await api("/api/progress"+(CUR?("?topic="+encodeURIComponent(CUR)):""))}catch(e){return}
 renderScan(pr.scan||[]);
 const jb=pr.jobs||{}, c=pr.counts||{};
 const waiting=(jb.PENDING||0)+(jb.RUNNING||0);
 $("#overall").textContent="Xử lý: "+(jb.DONE||0)+" xong · "+waiting+" đang chờ"
   +(jb.FAILED?(" · "+jb.FAILED+" lỗi"):"")
   +(jb.WAITING_QUOTA?(" · "+jb.WAITING_QUOTA+" chờ quota"):"")
   +" — Claims"+(CUR?" (chủ đề này)":"")+": "+(CUR?(c.topic_claims||0):(c.claim||0))
   +" · Entities: "+(c.entity||0);
 $("#stat").textContent="evidence "+(c.evidence||0)+" · claim "+(c.claim||0)+" · entity "+(c.entity||0);
 if(WAS_ACTIVE && !pr.active){ // vừa chạy xong toàn bộ
  WAS_ACTIVE=false;
  $("#btn_search").disabled=false;$("#btn_stop").style.display="none";
  msg("✅ Xong toàn bộ — báo cáo bên dưới.");
  if(CUR)loadReport();
  await refreshTopics();
 }
 if(pr.active)WAS_ACTIVE=true;
}
function startPolling(){if(!POLL)POLL=setInterval(poll,2000);poll()}

async function search(){
 const q=$("#q").value.trim();if(!q){msg("Gõ từ khóa trước đã.");return}
 msg("Bắt đầu quét song song…");
 const r=await post("/api/search",{keyword:q});
 if(r.error){msg("Lỗi: "+r.error);return}
 CUR=r.id;CUR_NAME=q;showCur();WAS_ACTIVE=true;
 $("#btn_search").disabled=true;$("#btn_stop").style.display="";
 startPolling();
}
async function stopSearch(){await post("/api/stop");msg("Đã yêu cầu dừng — nền đang quét sẽ chạy nốt bài dở.")}

async function loadReport(){
 if(!CUR){msg("Chưa có chủ đề.");return}
 const r=await api("/api/report?topic="+encodeURIComponent(CUR));
 $("#out").textContent=r.error?("Lỗi: "+r.error):r.markdown;
}
async function refreshTopics(){
 const ts=await api("/api/topics");const sel=$("#topic");sel.textContent="";
 const ph=document.createElement("option");ph.value="";ph.textContent="— chọn —";sel.appendChild(ph);
 ts.forEach(t=>{const o=document.createElement("option");o.value=t.id;
  o.textContent=t.name+" ("+t.n+" nguồn)";o.dataset.name=t.name;
  if(t.id===CUR)o.selected=true;sel.appendChild(o)});
}
function pickTopic(){const sel=$("#topic");if(!sel.value)return;
 CUR=sel.value;CUR_NAME=sel.options[sel.selectedIndex].dataset.name||"";showCur();loadReport()}
async function ingest(){
 if(!CUR){msg("Chưa có chủ đề — tìm kiếm hoặc mở chủ đề cũ trước.");return}
 const r=await post("/api/ingest",{topic:CUR,platform:$("#pf_ingest").value});
 msg(r.error?("Lỗi: "+r.error):("Đã nạp "+r.ingested+" file — worker nền đang xử lý."));
 WAS_ACTIVE=true;startPolling();
}
async function loadPlatforms(){
 const ps=await api("/api/platforms");const ing=$("#pf_ingest");ing.textContent="";
 ps.forEach(p=>{const o=document.createElement("option");o.value=p.key;
  o.textContent=p.display+" ("+p.region+")";ing.appendChild(o)});
}

// ---- Curator (mục phụ) — dựng DOM bằng textContent, KHÔNG innerHTML (chống XSS:
// tên entity đến từ nội dung web quét về, không tin được) ----
async function loadEntities(){
 const es=await api("/api/entities");const box=$("#entlist");box.textContent="";
 if(!es.length){box.textContent="(chưa có thực thể nào)";return}
 es.forEach(e=>{
  const d=document.createElement("div");d.className="item";
  const s=document.createElement("span");s.textContent=e.canonical_name+" ";
  const t=document.createElement("span");t.className="t";t.textContent="["+e.entity_type+"] "+e.id;
  s.appendChild(t);
  const btns=document.createElement("span");btns.className="btnrow";
  const b1=document.createElement("button");b1.className="sec";b1.textContent="chọn";
  b1.onclick=()=>{if(!$("#mg_keep").value)$("#mg_keep").value=e.id;else $("#mg_drop").value=e.id};
  const b2=document.createElement("button");b2.className="sec";b2.textContent="tách";
  b2.onclick=async()=>{const r=await post("/api/entity/split",{id:e.id});
   msg(r.error?("Lỗi: "+r.error):"Đã tách.");loadEntities()};
  btns.appendChild(b1);btns.appendChild(b2);
  d.appendChild(s);d.appendChild(btns);box.appendChild(d);
 });
}
async function mergeEnt(){const r=await post("/api/entity/merge",{keep:$("#mg_keep").value,drop:$("#mg_drop").value});
 msg(r.error?("Lỗi: "+r.error):"Đã gộp.");loadEntities()}
async function loadReview(){
 const rs=await api("/api/review/queue");const box=$("#rvqueue");box.textContent="";
 rs.forEach(p=>{
  const d=document.createElement("div");d.className="item";
  const s=document.createElement("span");
  s.textContent="conf="+p.confidence.toFixed(2)+" «"+p.na+"» ≈ «"+p.nb+"»";
  const b=document.createElement("button");b.className="sec";b.textContent="Gộp cặp này";
  b.onclick=async()=>{const r=await post("/api/entity/merge",{keep:p.entity_id_a,drop:p.entity_id_b});
   msg(r.error?("Lỗi: "+r.error):"Đã gộp.");loadReview();loadEntities()};
  d.appendChild(s);d.appendChild(b);box.appendChild(d);
 });
}
async function rvScan(){const r=await post("/api/review/scan",{threshold:0.72});
 msg(r.error?("Lỗi: "+r.error):("Đã tạo "+r.created+" đề xuất."));loadReview()}
async function rvApprove(){const r=await post("/api/review/approve",{min_confidence:0.85});
 msg(r.error?("Lỗi: "+r.error):("Đã duyệt+gộp "+r.approved+"."));loadReview();loadEntities()}
async function annotate(){
 const raw=$("#an_body").value;const parts=raw.split("|");
 const body=parts[0].trim();const verdict=parts.length>1?parts[1].trim():null;
 const r=await post("/api/annotate",{target:$("#an_target").value,body:body,verdict:verdict});
 msg(r.error?("Lỗi: "+r.error):("Đã ghi chú "+r.id+"."));if(!r.error)$("#an_body").value="";
}
async function loadCost(){
 const rs=await api("/api/cost");const box=$("#costtbl");box.textContent="";
 if(!rs.length){box.textContent="(chưa có lời gọi AI nào)";return}
 const tb=document.createElement("table");
 const hd=document.createElement("tr");
 ["tier","model","calls","token in","token out","~USD"].forEach(h=>{
  const th=document.createElement("th");th.textContent=h;hd.appendChild(th)});
 tb.appendChild(hd);
 rs.forEach(r=>{const tr=document.createElement("tr");
  [r.tier,r.model,r.calls,r.tin||0,r.tout||0,(r.usd||0).toFixed(4)].forEach(v=>{
   const td=document.createElement("td");td.textContent=String(v);tr.appendChild(td)});
  tb.appendChild(tr)});
 box.appendChild(tb);
}

showCur();loadPlatforms();refreshTopics();loadEntities();loadReview();startPolling();
</script></body></html>"""
