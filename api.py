"""ProxyForge — REST API + live dashboard."""
from __future__ import annotations

import logging
import time

from aiohttp import web

log = logging.getLogger("api")

DASHBOARD = r"""<!doctype html><html><head><meta charset=utf-8>
<title>ProxyForge — live proxy engine</title>
<style>
:root{--bg:#0b0f14;--panel:#121821;--line:#1e2733;--tx:#d7e1ec;--dim:#7d8ca0;
--acc:#3ddc97;--warn:#ffb454;--bad:#ff6b6b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);
font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
header{display:flex;align-items:center;gap:16px;padding:18px 24px;border-bottom:1px solid var(--line)}
h1{font-size:18px;margin:0;color:var(--acc);letter-spacing:1px}
h1 span{color:var(--dim);font-size:12px;font-weight:400}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;padding:18px 24px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
.card b{display:block;font-size:22px}.card i{color:var(--dim);font-style:normal;
font-size:11px;text-transform:uppercase;letter-spacing:1px}
table{width:100%;border-collapse:collapse}
th{color:var(--dim);text-align:left;font-size:11px;text-transform:uppercase;padding:8px 10px;border-bottom:1px solid var(--line)}
td{padding:7px 10px;border-bottom:1px solid var(--line)}tr:hover td{background:#16202b}
.pill{padding:2px 8px;border-radius:99px;font-size:11px;border:1px solid var(--line)}
.pA{color:var(--acc)}.pB{color:var(--warn)}.pC{color:var(--dim)}
.elite{color:var(--acc)}.anonymous{color:var(--warn)}.transparent{color:var(--bad)}
input{background:var(--panel);border:1px solid var(--line);color:var(--tx);
padding:8px 10px;border-radius:8px;margin-left:auto;width:240px}
.wrap{padding:0 24px 40px;overflow-x:auto}
</style></head><body>
<header><h1>PROXYFORGE <span>live proxy engine</span></h1>
<input id=q placeholder="filter proxy / country..."></header>
<div class=cards id=cards></div>
<div class=wrap><table><thead><tr><th>proxy</th><th>proto</th><th>cc</th><th>anonymity</th>
<th>latency</th><th>speed</th><th>grade</th><th>checks ok/total</th></tr></thead>
<tbody id=tb></tbody></table></div>
<script>
const fmt=(n,u)=>n==null?'—':(u==='kb'?Math.round(n)+' kb/s':Math.round(n)+' ms');
async function tick(){
 try{
  const s=await (await fetch('/stats')).json();
  document.getElementById('cards').innerHTML=[
   ['alive proxies',s.alive],['tracked',s.tracked],['checks / sec',s.checks_per_sec],
   ['p50 latency',(s.p50_latency_ms??'—')+' ms'],['elite',s.anonymity.elite||0],
   ['grade A',s.grades.A||0],['sources up',s.sources_up+'/'+s.sources_total],
   ['uptime',Math.floor(s.uptime_sec/60)+'m']
  ].map(([k,v])=>`<div class=card><b>${v??'—'}</b><i>${k}</i></div>`).join('');
  const j=await (await fetch('/proxies?limit=150')).json();
  const q=document.getElementById('q').value.toLowerCase();
  document.getElementById('tb').innerHTML=j.proxies
   .filter(p=>!q||(p.proxy+p.country+p.protocol).toLowerCase().includes(q))
   .map(p=>`<tr><td>${p.proxy}</td><td>${p.protocol}</td><td>${p.country_code||'?'}</td>
    <td class=${p.anonymity}>${p.anonymity}</td><td>${fmt(p.latency_ms)}</td>
    <td>${fmt(p.speed_kbps,'kb')}</td><td><span class="pill p${p.grade}">${p.grade}</span></td>
    <td>${p.ok}/${p.ok+p.fail}</td></tr>`).join('');
 }catch(e){console.error(e)}
}
document.getElementById('q').oninput=tick;tick();setInterval(tick,5000);
</script></body></html>"""


def build_app(engine) -> web.Application:
    cfg, pool = engine.cfg, engine.pool
    started = time.time()

    def filters_from(request):
        q = request.rel_url.query

        def fnum(name, default=None):
            try:
                return float(q.get(name))
            except (TypeError, ValueError):
                return default

        return {
            "protocol": q.get("protocol") or None,
            "country": q.get("country") or None,
            "anonymity": q.get("anonymity") or None,
            "min_score": fnum("min_score"),
            "https_only": q.get("https") in ("1", "true", "yes"),
            "limit": int(fnum("limit", 0) or 0) or None,
        }

    async def index(request):
        return web.Response(text=DASHBOARD, content_type="text/html")

    async def get_one(request):
        f = filters_from(request)
        p = pool.acquire(protocol=f["protocol"], country=f["country"],
                         anonymity=f["anonymity"], min_score=f["min_score"],
                         https_only=f["https_only"])
        if p is None:
            return web.json_response({"error": "no alive proxy matches filters"}, status=503)
        if request.rel_url.query.get("format") == "txt":
            return web.Response(text=f"{p.host}:{p.port}\n")
        return web.json_response(p.to_dict())

    async def proxies(request):
        f = filters_from(request)
        order = request.rel_url.query.get("order", "score")
        rows = pool.query(order=order, limit=f.pop("limit") or 100, **f)
        if request.rel_url.query.get("format") == "txt":
            return web.Response(text="".join(f"{r['host']}:{r['port']}\n" for r in rows))
        return web.json_response({"count": len(rows), "proxies": rows})

    async def export(request):
        rows = pool.query(limit=None)
        if request.rel_url.query.get("format") == "json":
            return web.json_response({"count": len(rows), "proxies": rows})
        return web.Response(text="".join(f"{r['host']}:{r['port']}\n" for r in rows))

    async def report(request):
        try:
            data = await request.json()
            key, ok = data["proxy"], bool(data.get("ok", True))
        except Exception:
            return web.json_response(
                {"error": 'body: {"proxy": "http://ip:port", "ok": true}'}, status=400)
        return web.json_response({"ok": pool.report(key, ok)})

    async def stats(request):
        s = pool.stats()
        health = engine.fetcher.health_summary()
        s.update({
            "checks_per_sec": round(engine.checker.rate, 1),
            "checked_total": engine.checker.total_checked,
            "queue_depth": engine.queue.qsize(),
            "real_ip": engine.checker.real_ip,
            "strategy": cfg.strategy,
            "gateway": f"{cfg.gateway_host}:{cfg.gateway_port}" if cfg.gateway_enabled else None,
            "uptime_sec": int(time.time() - started),
            "sources_total": len(health),
            "sources_up": sum(1 for h in health
                              if h["last_ok_ago"] is not None
                              and h["last_ok_ago"] < cfg.source_interval * 3),
            "sources": health,
        })
        return web.json_response(s)

    async def healthz(request):
        return web.json_response({"status": "ok", "alive": pool.stats()["alive"]})

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/get", get_one)
    app.router.add_get("/proxies", proxies)
    app.router.add_get("/export", export)
    app.router.add_post("/report", report)
    app.router.add_get("/stats", stats)
    app.router.add_get("/healthz", healthz)
    return app
