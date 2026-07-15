"""Giao diện web tối giản, LOCAL-ONLY (ADR-010). stdlib http.server — P14, không framework.

Chỉ bind 127.0.0.1 (không mở ra mạng — an ninh CHR-035/ARC-020). Lớp mỏng gọi lại các hàm
CLI đã có: tạo topic, ingest (Mode A), discover (Mode B), run, report, platforms.
"""

from __future__ import annotations

import json
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

INDEX_HTML = """<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>UIRP</title>
<style>
 :root{--bg:#0f1115;--card:#1a1d24;--fg:#e7e9ee;--mut:#9aa0ac;--acc:#4c8bf5;--ok:#39b970;--warn:#e0a33e;--bd:#2a2f3a}
 *{box-sizing:border-box} body{margin:0;font:15px/1.5 system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--fg)}
 header{padding:14px 20px;background:var(--card);border-bottom:1px solid var(--bd);display:flex;gap:16px;align-items:center;flex-wrap:wrap}
 h1{font-size:18px;margin:0} .stat{color:var(--mut);font-size:13px}
 main{max-width:900px;margin:0 auto;padding:18px;display:grid;gap:16px}
 .card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:16px}
 .card h2{margin:0 0 10px;font-size:15px}
 label{display:block;font-size:13px;color:var(--mut);margin:8px 0 4px}
 input,select,textarea{width:100%;padding:9px;background:#0d0f13;border:1px solid var(--bd);border-radius:7px;color:var(--fg);font:inherit}
 button{padding:9px 14px;background:var(--acc);color:#fff;border:0;border-radius:7px;font:inherit;cursor:pointer;margin-top:8px}
 button.sec{background:#2a2f3a} button:hover{opacity:.9}
 .row{display:flex;gap:10px;flex-wrap:wrap} .row>*{flex:1;min-width:140px}
 pre{white-space:pre-wrap;background:#0d0f13;border:1px solid var(--bd);border-radius:7px;padding:12px;max-height:420px;overflow:auto;font-size:13px}
 .plist{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px}
 .pchip{font-size:12px;padding:6px 8px;border:1px solid var(--bd);border-radius:6px;background:#0d0f13}
 .pchip .a{color:var(--ok)} .pchip .m{color:var(--warn)}
 #msg{font-size:13px;color:var(--mut);min-height:18px}
 .item{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--bd);font-size:13px}
 .item:last-child{border-bottom:0} .item .t{color:var(--mut);font-size:12px}
 table{width:100%;border-collapse:collapse;font-size:13px}
 th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--bd)}
 th{color:var(--mut);font-weight:600}
 .btnrow{display:flex;gap:6px;flex-shrink:0}
 .btnrow button{margin-top:0;padding:5px 9px;font-size:12px}
</style></head><body>
<header><h1>🔎 UIRP</h1><span class="stat" id="stat">…</span></header>
<main>
 <div class="card"><h2>1 · Tìm kiếm nghiên cứu</h2>
  <label>Chủ đề / từ khóa</label>
  <input id="q" placeholder="ví dụ: lừa đảo đầu tư Mr Pips">
  <div class="row">
   <button id="btn_search" onclick="search()">🔍 Tìm trên tất cả nền tảng</button>
   <button id="btn_stop" class="sec" onclick="stopSearch()" style="display:none">⏹ Dừng</button>
  </div>
  <p class="stat">Tự tìm lần lượt trên các nền hỗ trợ tìm tự động (Facebook, YouTube, TikTok, X, Weibo…) —
   mỗi nền báo kết quả ngay khi xong, không cần chờ hết mới thấy gì.
   Nền chống bot mạnh (Xiaohongshu, Douyin, Kuaishou, Zalo) cần nạp file lưu tay — xem mục Nâng cao.</p>
  <div id="curtopic" class="stat"></div>
  <div id="searchprog" class="stat"></div>
  <div id="searchout"></div>
  <details>
   <summary>Nâng cao</summary>
   <label>Mở lại chủ đề đã có</label>
   <select id="topic" onchange="pickTopic()"></select>
   <label>Nạp file lưu tay cho 1 nền tảng cụ thể</label>
   <div class="row"><select id="pf_ingest"></select><button class="sec" onclick="ingest()">Nạp</button></div>
   <p class="stat">Thả file (.html/.png…) đã lưu vào <code>data/inbox/&lt;nền tảng&gt;/</code> trước khi bấm Nạp.</p>
  </details>
 </div>

 <div class="card"><h2>2 · Xử lý & báo cáo</h2>
  <div class="row"><button onclick="run()">▶ Chạy xử lý (parse→dịch→trích xuất)</button>
   <button class="sec" onclick="report()">📄 Xem báo cáo</button></div>
  <div id="msg"></div>
  <pre id="out"></pre>
 </div>

 <div class="card"><h2>3 · Curator — thực thể (gộp/tách)</h2>
  <div id="entlist"></div>
  <div class="row">
   <div><label>Giữ (keep id)</label><input id="mg_keep" placeholder="ent_…"></div>
   <div><label>Gộp vào (drop id)</label><input id="mg_drop" placeholder="ent_…"></div>
  </div>
  <button onclick="mergeEnt()">Gộp</button>
  <button class="sec" onclick="loadEntities()">↻ Tải lại danh sách</button>
 </div>

 <div class="card"><h2>4 · Curator — đề xuất gộp (review queue)</h2>
  <div class="row">
   <div><label>Ngưỡng độ giống (scan)</label><input id="rv_th" value="0.72"></div>
   <div><label>Confidence tối thiểu (duyệt hàng loạt)</label><input id="rv_min" value="0.85"></div>
  </div>
  <div class="row"><button onclick="rvScan()">Quét đề xuất mới</button>
   <button class="sec" onclick="rvApprove()">Duyệt hàng loạt</button></div>
  <div id="rvqueue"></div>
 </div>

 <div class="card"><h2>5 · Curator — ghi chú (annotation)</h2>
  <div class="row">
   <div><label>Target id (claim/entity/evidence…)</label><input id="an_target" placeholder="clm_… / ent_…"></div>
   <div><label>Verdict (tuỳ chọn)</label><input id="an_verdict" placeholder="ví dụ: xác nhận, nghi vấn"></div>
  </div>
  <label>Nội dung ghi chú</label><textarea id="an_body" rows="2"></textarea>
  <button onclick="annotate()">Ghi chú</button>
 </div>

 <div class="card"><h2>6 · Chi phí gọi AI (usage_log)</h2>
  <button class="sec" onclick="loadCost()">↻ Tải chi phí</button>
  <div id="costtbl"></div>
 </div>

 <div class="card"><h2>Nền tảng kết nối được</h2><div class="plist" id="plats"></div></div>
</main>
<script>
const $=s=>document.querySelector(s);
let CUR=null, CUR_NAME="";
async function api(path,opt){const r=await fetch(path,opt);return r.json()}
function msg(t){$("#msg").textContent=t}
function showCur(){$("#curtopic").textContent=CUR?`Đang làm việc trên chủ đề: ${CUR_NAME}`:"(chưa chọn chủ đề nào — gõ từ khóa rồi Tìm)"}
async function refresh(){
 const s=await api("/api/status");
 $("#stat").textContent=`evidence ${s.counts.evidence} · claim ${s.counts.claim} · entity ${s.counts.entity} · job `+Object.entries(s.jobs||{}).map(([k,v])=>`${k}:${v}`).join(" ");
 const ts=await api("/api/topics");const sel=$("#topic");sel.innerHTML="";
 const ph=document.createElement("option");ph.value="";ph.textContent="— chọn —";sel.appendChild(ph);
 ts.forEach(t=>{const o=document.createElement("option");o.value=t.id;o.textContent=`${t.name} (${t.n} nguồn)`;if(t.id===CUR)o.selected=true;sel.appendChild(o)});
}
async function loadPlatforms(){
 const ps=await api("/api/platforms");
 const ing=$("#pf_ingest");ing.innerHTML="";
 const box=$("#plats");box.innerHTML="";
 ps.forEach(p=>{
  const o=document.createElement("option");o.value=p.key;o.textContent=`${p.display} (${p.region})`;ing.appendChild(o);
  const c=document.createElement("div");c.className="pchip";
  c.innerHTML=`${p.display}<br><span class="${p.auto?'a':'m'}">${p.auto?'Mode A + B':'Mode A (lưu tay)'}</span>`;box.appendChild(c);
 });
}
function pickTopic(){const sel=$("#topic");if(!sel.value)return;CUR=sel.value;CUR_NAME=sel.options[sel.selectedIndex].textContent.replace(/\\s*\\(\\d+ nguồn\\)$/,"");showCur()}
let STOP_SEARCH=false, ABORT=null;
function addResultLine(text){const d=document.createElement("div");d.className="item";d.textContent=text;$("#searchout").appendChild(d)}
async function search(){
 const q=$("#q").value.trim();if(!q){msg("Gõ từ khóa/chủ đề trước đã.");return}
 msg("Đang tạo/mở chủ đề…");
 const t=await api("/api/topic",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:q})});
 if(t.error){msg("Lỗi: "+t.error);return}
 CUR=t.id;CUR_NAME=q;showCur();
 const plats=await api("/api/discover_platforms");
 STOP_SEARCH=false;
 $("#btn_search").disabled=true;$("#btn_stop").style.display="";
 $("#searchout").innerHTML="";
 if(!plats.length){$("#searchprog").textContent="(không có nền tảng nào hỗ trợ tìm tự động)";$("#btn_search").disabled=false;$("#btn_stop").style.display="none";return}
 let found=0;
 for(let i=0;i<plats.length;i++){
  if(STOP_SEARCH){$("#searchprog").textContent=`Đã dừng ở nền ${i}/${plats.length}.`;break}
  const p=plats[i];
  $("#searchprog").textContent=`Đang tìm: ${p.display} … (${i+1}/${plats.length})`;
  let r;
  ABORT=new AbortController();
  try{
   const resp=await fetch("/api/discover_one",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({topic:CUR,keyword:q,platform:p.key}),signal:ABORT.signal});
   r=await resp.json();
  }catch(e){
   if(e.name==="AbortError"){$("#searchprog").textContent=`Đã dừng ở nền ${i+1}/${plats.length}.`;break}
   r={error:String(e)};
  }
  if(r.error)addResultLine(`${p.display}: bỏ qua — ${r.error}`);
  else{addResultLine(`${p.display}: ${r.n} kết quả`);found+=r.n||0}
  if(i===plats.length-1)$("#searchprog").textContent=`Xong — quét ${plats.length} nền, tìm được ${found} kết quả.`;
 }
 $("#btn_search").disabled=false;$("#btn_stop").style.display="none";
 msg("Xong. Giờ bấm 'Chạy xử lý' ở mục 2.");await refresh();
}
function stopSearch(){STOP_SEARCH=true;if(ABORT)ABORT.abort()}
async function ingest(){if(!CUR){msg("Chưa có chủ đề — tìm kiếm trước hoặc mở chủ đề cũ ở mục Nâng cao.");return}const r=await api("/api/ingest",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({topic:CUR,platform:$("#pf_ingest").value})});msg(r.error?("Lỗi: "+r.error):`Đã nạp ${r.ingested} file. Bấm 'Chạy xử lý'.`);await refresh()}
async function run(){
 if(!CUR){msg("Chưa có chủ đề — tìm kiếm trước.");return}
 let total=0;
 while(true){
  msg(`Đang xử lý… (đã xong ${total} job)`);
  const r=await api("/api/run",{method:"POST"});
  if(r.error){msg("Lỗi: "+r.error);break}
  total+=r.processed;
  if(!r.pending||r.processed===0){msg(`Xong — đã xử lý ${total} job.`);break}
 }
 await refresh();
}
async function report(){if(!CUR){msg("Chưa có chủ đề — tìm kiếm trước.");return}const r=await api("/api/report?topic="+encodeURIComponent(CUR));$("#out").textContent=r.error?("Lỗi: "+r.error):r.markdown;msg("")}

async function loadEntities(){
 const es=await api("/api/entities");const box=$("#entlist");box.innerHTML="";
 if(!es.length){box.innerHTML='<p class="stat">(chưa có thực thể nào)</p>';return}
 es.forEach(e=>{
  const d=document.createElement("div");d.className="item";
  d.innerHTML=`<span>${e.canonical_name} <span class="t">[${e.entity_type}] ${e.id}</span></span>
   <span class="btnrow"><button class="sec" onclick="fillMerge('${e.id}')">chọn</button>
   <button class="sec" onclick="splitEnt('${e.id}')">Tách</button></span>`;
  box.appendChild(d);
 });
}
function fillMerge(id){if(!$("#mg_keep").value)$("#mg_keep").value=id;else $("#mg_drop").value=id}
async function mergeEnt(){const r=await api("/api/entity/merge",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({keep:$("#mg_keep").value,drop:$("#mg_drop").value})});msg(r.error?("Lỗi: "+r.error):"Đã gộp.");await loadEntities()}
async function splitEnt(id){const r=await api("/api/entity/split",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id})});msg(r.error?("Lỗi: "+r.error):"Đã tách.");await loadEntities()}

async function loadReview(){
 const rs=await api("/api/review/queue");const box=$("#rvqueue");box.innerHTML="";
 if(!rs.length){box.innerHTML='<p class="stat">(không có đề xuất chờ duyệt)</p>';return}
 rs.forEach(p=>{
  const d=document.createElement("div");d.className="item";
  d.innerHTML=`<span>conf=${p.confidence.toFixed(2)} «${p.na}» ≈ «${p.nb}»</span>
   <button class="sec" onclick="quickMerge('${p.entity_id_a}','${p.entity_id_b}')">Gộp cặp này</button>`;
  box.appendChild(d);
 });
}
async function quickMerge(a,b){const r=await api("/api/entity/merge",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({keep:a,drop:b})});msg(r.error?("Lỗi: "+r.error):"Đã gộp.");await loadReview();await loadEntities()}
async function rvScan(){const r=await api("/api/review/scan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({threshold:parseFloat($("#rv_th").value)})});msg(r.error?("Lỗi: "+r.error):`Đã tạo ${r.created} đề xuất mới.`);await loadReview()}
async function rvApprove(){const r=await api("/api/review/approve",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({min_confidence:parseFloat($("#rv_min").value)})});msg(r.error?("Lỗi: "+r.error):`Đã duyệt+gộp ${r.approved} đề xuất.`);await loadReview();await loadEntities()}

async function annotate(){const r=await api("/api/annotate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({target:$("#an_target").value,body:$("#an_body").value,verdict:$("#an_verdict").value||null})});msg(r.error?("Lỗi: "+r.error):`Đã ghi chú ${r.id}.`);if(!r.error)$("#an_body").value=""}

async function loadCost(){
 const rs=await api("/api/cost");const box=$("#costtbl");
 if(!rs.length){box.innerHTML='<p class="stat">(chưa có lời gọi AI nào)</p>';return}
 let h='<table><tr><th>tier</th><th>model</th><th>calls</th><th>token in</th><th>token out</th><th>~USD</th></tr>';
 rs.forEach(r=>{h+=`<tr><td>${r.tier}</td><td>${r.model}</td><td>${r.calls}</td><td>${r.tin||0}</td><td>${r.tout||0}</td><td>${(r.usd||0).toFixed(4)}</td></tr>`});
 box.innerHTML=h+'</table>';
}

showCur();loadPlatforms();refresh();loadEntities();loadReview();loadCost();
</script></body></html>"""


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
    """Rút gọn lỗi Playwright (có thể dài cả trang, chứa nguyên dòng lệnh Chrome) còn 1 dòng."""
    first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return first[:200]


_LOG_FILE = None  # đặt trong serve() → data/logs/web-YYYYMMDD.log


def _log(msg: str) -> None:
    """In log ra terminal + ghi file data/logs/ để tra cứu về sau (ADR-011)."""
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))
    if _LOG_FILE:
        try:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass  # log file lỗi không được chặn nghiệp vụ


class _Handler(BaseHTTPRequestHandler):
    cfg: Config = None  # type: ignore[assignment]

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", "0") or "0")
        return json.loads(self.rfile.read(n) or "{}") if n else {}

    def log_message(self, *a) -> None:  # im lặng
        pass

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            _send(self, INDEX_HTML.encode("utf-8"), ctype="text/html")
            return
        if path == "/api/platforms":
            _send(self, [{"key": p.key, "display": p.display, "region": p.region,
                          "auto": p.auto, "note": p.note} for p in platforms.all_platforms()])
            return
        if path == "/api/discover_platforms":
            _send(self, [{"key": p.key, "display": p.display}
                         for p in platforms.all_platforms() if p.auto and p.search_url])
            return
        conn = db.connect(self.cfg)
        try:
            if path == "/api/topics":
                _send(self, db.query(conn,
                    "SELECT t.id,t.name,(SELECT COUNT(*) FROM information_object io "
                    "WHERE io.topic_id=t.id) n FROM topic t ORDER BY t.created_at DESC"))
            elif path == "/api/status":
                counts = {t: db.query(conn, f"SELECT COUNT(*) n FROM {t}")[0]["n"]
                          for t in ("evidence", "observation", "claim", "entity")}
                jb = {r["state"]: r["n"] for r in db.query(conn,
                      "SELECT state,COUNT(*) n FROM job GROUP BY state")}
                _send(self, {"counts": counts, "jobs": jb})
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
            _send(self, {"error": str(e)}, 400)
        finally:
            conn.close()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        data = self._body()
        conn = db.connect(self.cfg)
        try:
            if path == "/api/topic":
                name = (data.get("name") or "").strip()
                if not name:
                    _send(self, {"error": "thiếu tên chủ đề"}, 400)
                else:
                    hit = db.query(conn,
                        "SELECT id FROM topic WHERE LOWER(name)=LOWER(?) ORDER BY created_at DESC LIMIT 1",
                        (name,))
                    if hit:
                        _log(f"chủ đề: mở lại «{name}» ({hit[0]['id']})")
                        _send(self, {"id": hit[0]["id"], "created": False})
                    else:
                        tid = new_id("top")
                        db.insert(conn, "topic", {"id": tid, "name": name,
                                  "description": data.get("desc"), "status": "active",
                                  "created_at": _now()})
                        _log(f"chủ đề: tạo mới «{name}» ({tid})")
                        _send(self, {"id": tid, "created": True})
            elif path == "/api/ingest":
                n = manual.ingest(conn, self.cfg, data["topic"], data.get("platform", "facebook"))
                _log(f"ingest {data.get('platform', 'facebook')}: nạp {n} file")
                _send(self, {"ingested": n})
            elif path == "/api/run":
                register_all(self.cfg)
                _log("run: xử lý lô job (tối đa 25/lượt)…")
                n = jobs.run(conn, self.cfg, once=True, max_jobs=25)
                left = db.query(conn,
                    "SELECT COUNT(*) n FROM job WHERE state='PENDING'")[0]["n"]
                _log(f"run: xong lô {n} job, còn chờ {left}")
                _send(self, {"processed": n, "pending": left})
            elif path == "/api/discover":
                keys = data.get("platforms") or [
                    p.key for p in platforms.all_platforms() if p.auto and p.search_url]
                results = {}
                for k in keys:
                    _log(f"discover {k}: đang tìm «{data['keyword']}»…")
                    try:
                        results[k] = browser.collect(conn, self.cfg, data["topic"], k,
                                                     "keyword", data["keyword"])
                        _log(f"discover {k}: {results[k]} kết quả")
                    except Exception as e:  # noqa: BLE001
                        results[k] = f"bỏ qua: {_short_err(e)}"
                        _log(f"discover {k}: lỗi — {_short_err(e)}")
                _send(self, {"results": results})
            elif path == "/api/discover_one":
                pf = data["platform"]
                _log(f"discover {pf}: đang tìm «{data['keyword']}»…")
                try:
                    n = browser.collect(conn, self.cfg, data["topic"], pf,
                                        "keyword", data["keyword"])
                    _log(f"discover {pf}: {n} kết quả")
                    _send(self, {"n": n})
                except Exception as e:  # noqa: BLE001
                    _log(f"discover {pf}: lỗi — {_short_err(e)}")
                    _send(self, {"n": None, "error": _short_err(e)})
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
            _send(self, {"error": str(e)}, 400)
        finally:
            conn.close()


def serve(cfg: Config, port: int = 8787) -> None:
    global _LOG_FILE
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    _LOG_FILE = cfg.logs_dir / f"web-{datetime.now():%Y%m%d}.log"
    _Handler.cfg = cfg
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    print(f"UIRP web đang chạy: http://127.0.0.1:{port}  (Ctrl+C để dừng)")
    print(f"Log ghi tại: {_LOG_FILE}")
    _log(f"--- khởi động web, backend={cfg.backend} ---")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng web.")
