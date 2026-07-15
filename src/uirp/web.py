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
 <div class="card"><h2>1 · Chủ đề nghiên cứu</h2>
  <div class="row">
   <div><label>Chọn chủ đề</label><select id="topic"></select></div>
   <div><label>Hoặc tạo mới</label><input id="newtopic" placeholder="Tên chủ đề mới"></div>
  </div>
  <button class="sec" onclick="mkTopic()">Tạo chủ đề</button>
 </div>

 <div class="card"><h2>2 · Nạp dữ liệu lưu tay (Mode A — mọi nền tảng)</h2>
  <label>Nền tảng</label><select id="pf_ingest"></select>
  <p class="stat">Thả file (.html/.png…) đã lưu vào <code>data/inbox/&lt;nền tảng&gt;/</code> rồi bấm Nạp.</p>
  <button onclick="ingest()">Nạp vào chủ đề</button>
 </div>

 <div class="card"><h2>3 · Tự động tìm (Mode B — cần trình duyệt)</h2>
  <label>Từ khóa</label><input id="kw" placeholder="ví dụ: lừa đảo đầu tư">
  <label>Nền tảng (trống = mọi nền hỗ trợ tìm)</label><input id="pf_disc" placeholder="facebook,weibo,tiktok">
  <button onclick="discover()">Tự động tìm & thu</button>
 </div>

 <div class="card"><h2>4 · Xử lý & báo cáo</h2>
  <div class="row"><button onclick="run()">▶ Chạy xử lý (parse→dịch→trích xuất)</button>
   <button class="sec" onclick="report()">📄 Xem báo cáo</button></div>
  <div id="msg"></div>
  <pre id="out"></pre>
 </div>

 <div class="card"><h2>5 · Curator — thực thể (gộp/tách)</h2>
  <div id="entlist"></div>
  <div class="row">
   <div><label>Giữ (keep id)</label><input id="mg_keep" placeholder="ent_…"></div>
   <div><label>Gộp vào (drop id)</label><input id="mg_drop" placeholder="ent_…"></div>
  </div>
  <button onclick="mergeEnt()">Gộp</button>
  <button class="sec" onclick="loadEntities()">↻ Tải lại danh sách</button>
 </div>

 <div class="card"><h2>6 · Curator — đề xuất gộp (review queue)</h2>
  <div class="row">
   <div><label>Ngưỡng độ giống (scan)</label><input id="rv_th" value="0.72"></div>
   <div><label>Confidence tối thiểu (duyệt hàng loạt)</label><input id="rv_min" value="0.85"></div>
  </div>
  <div class="row"><button onclick="rvScan()">Quét đề xuất mới</button>
   <button class="sec" onclick="rvApprove()">Duyệt hàng loạt</button></div>
  <div id="rvqueue"></div>
 </div>

 <div class="card"><h2>7 · Curator — ghi chú (annotation)</h2>
  <div class="row">
   <div><label>Target id (claim/entity/evidence…)</label><input id="an_target" placeholder="clm_… / ent_…"></div>
   <div><label>Verdict (tuỳ chọn)</label><input id="an_verdict" placeholder="ví dụ: xác nhận, nghi vấn"></div>
  </div>
  <label>Nội dung ghi chú</label><textarea id="an_body" rows="2"></textarea>
  <button onclick="annotate()">Ghi chú</button>
 </div>

 <div class="card"><h2>8 · Chi phí gọi AI (usage_log)</h2>
  <button class="sec" onclick="loadCost()">↻ Tải chi phí</button>
  <div id="costtbl"></div>
 </div>

 <div class="card"><h2>Nền tảng kết nối được</h2><div class="plist" id="plats"></div></div>
</main>
<script>
const $=s=>document.querySelector(s);
async function api(path,opt){const r=await fetch(path,opt);return r.json()}
function msg(t){$("#msg").textContent=t}
async function refresh(){
 const s=await api("/api/status");
 $("#stat").textContent=`evidence ${s.counts.evidence} · claim ${s.counts.claim} · entity ${s.counts.entity} · job `+Object.entries(s.jobs||{}).map(([k,v])=>`${k}:${v}`).join(" ");
 const ts=await api("/api/topics");const sel=$("#topic");sel.innerHTML="";
 ts.forEach(t=>{const o=document.createElement("option");o.value=t.id;o.textContent=`${t.name} (${t.n} nguồn)`;sel.appendChild(o)});
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
async function mkTopic(){const n=$("#newtopic").value.trim();if(!n)return;await api("/api/topic",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:n})});$("#newtopic").value="";await refresh();msg("Đã tạo chủ đề.")}
async function ingest(){const r=await api("/api/ingest",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({topic:$("#topic").value,platform:$("#pf_ingest").value})});msg(r.error?("Lỗi: "+r.error):`Đã nạp ${r.ingested} file. Bấm 'Chạy xử lý'.`);await refresh()}
async function run(){msg("Đang xử lý…");const r=await api("/api/run",{method:"POST"});msg(r.error?("Lỗi: "+r.error):`Đã xử lý ${r.processed} job.`);await refresh()}
async function discover(){msg("Đang tìm (cần trình duyệt)…");const pf=$("#pf_disc").value.trim();const body={topic:$("#topic").value,keyword:$("#kw").value,platforms:pf?pf.split(",").map(s=>s.trim()):null};const r=await api("/api/discover",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});msg(r.error?("Lỗi: "+r.error):("Kết quả: "+JSON.stringify(r.results)));await refresh()}
async function report(){const r=await api("/api/report?topic="+encodeURIComponent($("#topic").value));$("#out").textContent=r.error?("Lỗi: "+r.error):r.markdown;msg("")}

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

loadPlatforms();refresh();loadEntities();loadReview();loadCost();
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
                tid = new_id("top")
                db.insert(conn, "topic", {"id": tid, "name": data["name"],
                          "description": data.get("desc"), "status": "active", "created_at": _now()})
                _send(self, {"id": tid})
            elif path == "/api/ingest":
                n = manual.ingest(conn, self.cfg, data["topic"], data.get("platform", "facebook"))
                _send(self, {"ingested": n})
            elif path == "/api/run":
                register_all(self.cfg)
                _send(self, {"processed": jobs.run(conn, self.cfg, once=True)})
            elif path == "/api/discover":
                keys = data.get("platforms") or [
                    p.key for p in platforms.all_platforms() if p.auto and p.search_url]
                results = {}
                for k in keys:
                    try:
                        results[k] = browser.collect(conn, self.cfg, data["topic"], k,
                                                     "keyword", data["keyword"])
                    except Exception as e:  # noqa: BLE001
                        results[k] = f"bỏ qua: {e}"
                _send(self, {"results": results})
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
    _Handler.cfg = cfg
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    print(f"UIRP web đang chạy: http://127.0.0.1:{port}  (Ctrl+C để dừng)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng web.")
