"""Painel web do mil-watch-br v2: ao vivo, regiões (índice de atividade), eventos e histórico.

Somente biblioteca padrão. Lê o SQLite do coletor em modo somente leitura.
Config (.env): DASHBOARD_PORT, DASHBOARD_BIND, DASHBOARD_USER, DASHBOARD_PASSWORD, DB_PATH,
ANOMALY_MIN_DAYS, ANOMALY_BASELINE_DAYS.
"""
import base64
import hmac
import json
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def load_env(path=".env"):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env()
import analysis  # noqa: E402
import catalog  # noqa: E402
import geo  # noqa: E402
import store  # noqa: E402

DB_PATH = os.getenv("DB_PATH", "data/milwatch.db")
PORT = int(os.getenv("DASHBOARD_PORT", "8088"))
BIND = os.getenv("DASHBOARD_BIND", "0.0.0.0")
USER = os.getenv("DASHBOARD_USER", "admin")
PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
MIN_DAYS = int(os.getenv("ANOMALY_MIN_DAYS", "14"))
BASE_DAYS = int(os.getenv("ANOMALY_BASELINE_DAYS", "28"))
BR_SCOPE = os.getenv("BRAZIL_ALERT_SCOPE", "US").upper()

_cache, _lock = {}, threading.Lock()


def cached(key, ttl, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (time.time(), val)
    return val


def db():
    return store.connect(DB_PATH, readonly=True)


def rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def since(days):
    return 0 if days <= 0 else int(time.time() - days * 86400)


def bases(con):
    return cached("bases", 1800, lambda: analysis.learned_bases(con))


def nato_sql():
    return "(" + ",".join(f"'{c}'" for c in sorted(geo.NATO)) + ")"


# ------------------------------------------------------------------ API
def api_meta(q):
    return {"regions": geo.REGION_NAMES, "boxes": geo.REGION_BOXES,
            "categories": catalog.CATEGORY_LABELS, "countries": geo.COUNTRY_NAMES,
            "nato": sorted(geo.NATO), "br_scope": BR_SCOPE, "min_days": MIN_DAYS}


def live_filter(q):
    where, args = ["s.ts >= ?", "s.lost = 0"], [int(time.time()) - int(q.get("max_age", 600))]
    if q.get("region"):
        where.append("s.region = ?")
        args.append(q["region"])
    if q.get("cat"):
        where.append("a.category = ?")
        args.append(q["cat"])
    if q.get("nato") == "1":
        where.append(f"a.country IN {nato_sql()}")
    return " AND ".join(where), args


def api_live(q):
    with db() as con:
        where, args = live_filter(q)
        out = rows(con, f"""SELECT s.hex, s.ts, s.lat, s.lon, s.alt, s.gs, s.track, s.squawk, s.region,
                                   s.orbit_since, a.reg, a.type, a.category, a.country, sg.callsign
                            FROM state s JOIN aircraft a ON a.hex = s.hex
                            LEFT JOIN sightings sg ON sg.id = s.sighting_id
                            WHERE {where} ORDER BY a.category, s.region""", args)
        bs = bases(con)
        for r in out:
            r["projection"] = analysis.projection(r["lat"], r["lon"], r["track"], r["gs"])
            preds = analysis.predict_destination(con, r, bs, top=1) if r["alt"] and not r["orbit_since"] else []
            r["dest"] = preds[0] if preds else None
        return out


def api_aircraft(q):
    hx = (q.get("hex") or "").lower()
    with db() as con:
        ac = con.execute("SELECT * FROM aircraft WHERE hex=?", (hx,)).fetchone()
        if not ac:
            return {"error": "não encontrado"}
        st = con.execute("SELECT * FROM state WHERE hex=?", (hx,)).fetchone()
        st = dict(st) if st else None
        out = {"aircraft": dict(ac), "state": st,
               "sightings": rows(con, """SELECT id, callsign, first_seen, last_seen, regions, entered_br,
                                         landed_lat, landed_lon FROM sightings WHERE hex=?
                                         ORDER BY first_seen DESC LIMIT 20""", (hx,)),
               "events": rows(con, "SELECT ts, type, severity, region, detail FROM events WHERE hex=? "
                                   "ORDER BY ts DESC LIMIT 20", (hx,)),
               "track": [list(r) for r in con.execute(
                   "SELECT lat, lon, alt, ts FROM points WHERE hex=? AND ts>=? ORDER BY ts",
                   (hx, int(time.time()) - 6 * 3600)).fetchall()],
               "predictions": []}
        if st and not st.get("orbit_since"):
            cs = con.execute("SELECT callsign FROM sightings WHERE id=?", (st["sighting_id"],)).fetchone()
            a = {**st, "hex": hx, "callsign": cs[0] if cs else ""}
            out["predictions"] = analysis.predict_destination(con, a, bases(con))
            out["projection"] = analysis.projection(st["lat"], st["lon"], st["track"], st["gs"])
        out["in_orbit"] = bool(st and st.get("orbit_since"))
        return out


def api_regions(q):
    def compute():
        with db() as con:
            now = int(time.time())
            idx = analysis.anomaly_index(con, now, BASE_DAYS, MIN_DAYS)
            live = dict(con.execute("SELECT region, COUNT(*) FROM state WHERE ts>=? AND lost=0 GROUP BY region",
                                    (now - 600,)).fetchall())
            days = con.execute("SELECT COUNT(*) FROM collected_hours").fetchone()[0] / 24
            return {"index": idx, "live": live, "days_collected": round(days, 1), "min_days": MIN_DAYS}
    return cached("regions", 300, compute)


def api_events(q):
    where, args = ["e.ts >= ?"], [since(int(q.get("days", 7)))]
    if q.get("region"):
        where.append("e.region = ?")
        args.append(q["region"])
    if q.get("type"):
        where.append("e.type = ?")
        args.append(q["type"])
    with db() as con:
        return rows(con, f"""SELECT e.*, a.reg, a.type AS actype, a.category, a.country
                             FROM events e LEFT JOIN aircraft a ON a.hex = e.hex
                             WHERE {' AND '.join(where)} ORDER BY e.ts DESC LIMIT 500""", args)


def sightings_filter(q):
    where, args = ["s.first_seen >= ?"], [since(int(q.get("days", 30)))]
    if q.get("br") == "1":
        where.append("s.entered_br = 1")
    if q.get("region"):
        where.append("(',' || s.regions || ',') LIKE ?")
        args.append(f"%,{q['region']},%")
    if q.get("cat"):
        where.append("s.category = ?")
        args.append(q["cat"])
    if q.get("nato") == "1":
        where.append(f"s.country IN {nato_sql()}")
    return " AND ".join(where), args


def api_sightings(q):
    where, args = sightings_filter(q)
    with db() as con:
        return rows(con, f"""SELECT s.id, s.hex, s.reg, s.type, s.callsign, s.first_seen, s.last_seen,
                                    s.category, s.country, s.regions, s.entered_br, s.n_points,
                                    (SELECT COUNT(*) FROM sightings x WHERE x.hex = s.hex) AS times_total,
                                    (SELECT MAX(alt) FROM points p WHERE p.hex = s.hex
                                       AND p.ts BETWEEN s.first_seen AND s.last_seen) AS max_alt
                             FROM sightings s WHERE {where} ORDER BY s.first_seen DESC LIMIT 500""", args)


def api_tracks(q):
    where, args = sightings_filter(q)
    out = []
    with db() as con:
        for s in rows(con, f"""SELECT id, hex, reg, type, callsign, category, first_seen, last_seen, track_json
                               FROM sightings s WHERE {where} ORDER BY first_seen DESC LIMIT 200""", args):
            tj = s.pop("track_json")
            pts = json.loads(tj) if tj else [list(p) for p in con.execute(
                "SELECT lat, lon, alt, ts FROM points WHERE hex=? AND ts BETWEEN ? AND ? ORDER BY ts",
                (s["hex"], s["first_seen"], s["last_seen"])).fetchall()]
            s["points"] = store.downsample(pts, 150)
            out.append(s)
    return out


def api_stats(q):
    s = since(int(q.get("days", 30)))
    now = int(time.time())
    with db() as con:
        one = lambda sql, *a: con.execute(sql, a).fetchone()[0]
        return {
            "live": one("SELECT COUNT(*) FROM state WHERE ts>=? AND lost=0", now - 600),
            "live_nato": one(f"""SELECT COUNT(*) FROM state s JOIN aircraft a ON a.hex=s.hex
                                 WHERE s.ts>=? AND s.lost=0 AND a.country IN {nato_sql()}""", now - 600),
            "aircraft_24h": one("SELECT COUNT(*) FROM aircraft WHERE last_seen>=?", now - 86400),
            "events_24h": one("SELECT COUNT(*) FROM events WHERE ts>=?", now - 86400),
            "br_period": one("SELECT COUNT(*) FROM sightings WHERE entered_br=1 AND first_seen>=?", s),
            "last_point": one("SELECT MAX(ts) FROM state"),
            "top": rows(con, """SELECT hex, reg, type, category, COUNT(*) n, MAX(first_seen) last
                                FROM sightings WHERE first_seen>=? GROUP BY hex ORDER BY n DESC, last DESC
                                LIMIT 15""", (s,)),
            "event_types": dict(con.execute("SELECT type, COUNT(*) FROM events WHERE ts>=? GROUP BY type",
                                            (s,)).fetchall()),
        }


ROUTES = {"/api/meta": api_meta, "/api/live": api_live, "/api/aircraft": api_aircraft,
          "/api/regions": api_regions, "/api/events": api_events, "/api/sightings": api_sightings,
          "/api/tracks": api_tracks, "/api/stats": api_stats}

PAGE = r"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>mil-watch</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
:root{--bg:#0f1419;--panel:#171d24;--line:#26303b;--text:#e6e9ec;--mut:#8b96a3;--acc:#4fb3ff;--warn:#ffb547;--bad:#ff5a5a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center;padding:12px 20px;border-bottom:1px solid var(--line)}
h1{font-size:17px;margin:0 8px 0 0}nav{display:flex;gap:4px;flex-wrap:wrap}
nav button{background:none;border:1px solid transparent;color:var(--mut);padding:6px 12px;border-radius:6px;font:inherit;cursor:pointer}
nav button.on{background:var(--panel);border-color:var(--line);color:var(--text)}
.filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-left:auto}
select,button.b{background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:5px 8px;font:inherit}
label.ck{color:var(--mut);display:flex;gap:4px;align-items:center}
main{padding:14px 20px;display:grid;gap:14px;max-width:1500px;margin:0 auto}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.kpi b{display:block;font-size:22px;font-variant-numeric:tabular-nums}.kpi small{color:var(--mut)}
.grid2{display:grid;grid-template-columns:2fr 1fr;gap:14px}@media(max-width:950px){.grid2{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;min-width:0}
.card h2{font-size:13px;margin:0;padding:9px 14px;border-bottom:1px solid var(--line);color:var(--mut);font-weight:600}
#map{height:480px}.side{max-height:480px;overflow:auto}.pad{padding:12px 14px}
.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:6px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--mut);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.4px}
tbody tr{cursor:pointer}tbody tr:hover{background:#1b232c}tr.sel{background:#1f2a36}
a{color:var(--acc);text-decoration:none}.mut{color:var(--mut)}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}
.rc{border-bottom:1px solid var(--line);padding:10px 14px;cursor:pointer}.rc:hover{background:#1b232c}
.rc .t{display:flex;justify-content:space-between;gap:8px}.lvl{font-size:12px;padding:1px 8px;border-radius:10px;border:1px solid}
.bar{height:5px;background:var(--line);border-radius:3px;margin-top:6px;overflow:hidden}.bar i{display:block;height:100%;background:var(--acc)}
dl{display:grid;grid-template-columns:auto 1fr;gap:3px 12px;margin:0}dt{color:var(--mut)}dd{margin:0}
.note{color:var(--mut);font-size:12px;line-height:1.5}.side td{white-space:normal}
footer{color:var(--mut);font-size:12px;padding:0 20px 20px;max-width:1500px;margin:0 auto}
.leaflet-container{background:#0b0f13}.arr{background:none;border:none}
</style></head><body>
<header><h1>mil-watch</h1>
<nav id="tabs"><button data-t="live" class="on">Agora</button><button data-t="regions">Regiões</button>
<button data-t="events">Eventos</button><button data-t="history">Histórico</button></nav>
<div class="filters"><select id="fRegion"><option value="">Todas as regiões</option></select>
<select id="fCat"><option value="">Todas as categorias</option></select>
<label class="ck"><input type="checkbox" id="fNato">só OTAN</label>
<label class="ck" id="wBr"><input type="checkbox" id="fBr">entradas no Brasil</label>
<select id="fDays"><option value="1">24 h</option><option value="7" selected>7 dias</option><option value="30">30 dias</option><option value="90">90 dias</option><option value="0">tudo</option></select>
<button class="b" id="reload">Atualizar</button></div></header>
<main>
<section class="kpis">
<div class="kpi"><small>No ar agora (10 min)</small><b id="k1">–</b></div>
<div class="kpi"><small>Agora, da OTAN</small><b id="k2">–</b></div>
<div class="kpi"><small>Aeronaves em 24 h</small><b id="k3">–</b></div>
<div class="kpi"><small>Eventos em 24 h</small><b id="k4">–</b></div>
<div class="kpi"><small>Entradas no Brasil (período)</small><b id="k5">–</b></div>
<div class="kpi"><small>Último sinal</small><b id="k6" style="font-size:15px;padding-top:5px">–</b></div>
</section>
<section class="grid2"><div class="card"><h2 id="mapTitle">Mapa</h2><div id="map"></div></div>
<div class="card"><h2 id="sideTitle"></h2><div class="side" id="side"></div></div></section>
<section class="card"><h2 id="tblTitle"></h2><div class="scroll"><table><thead id="thead"></thead><tbody id="tbody"></tbody></table></div></section>
</main>
<footer>Dados públicos de ADS-B (adsb.lol / airplanes.live). Cobertura incompleta, e aeronaves em missão costumam voar com transponder desligado: o painel mostra principalmente apoio, transporte e vigilância. Aeronaves da OTAN transmitem muito mais que as de Rússia e China, então a visão é desequilibrada. O Índice de Atividade compara cada região com o próprio histórico; não prevê ataques, e exercícios também o elevam. Previsões de destino são estimativas.</footer>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const TZ={timeZone:"America/Sao_Paulo"};
const fmt=t=>t?new Date(t*1000).toLocaleString("pt-BR",{...TZ,day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}):"–";
const dur=s=>{const m=Math.round(s/60);return m<60?m+" min":Math.floor(m/60)+"h"+String(m%60).padStart(2,"0")};
const ft=a=>a==null?"–":a===0?"solo":Math.round(a).toLocaleString("pt-BR")+" ft";
const CC={tanker:"#ffb547",aew:"#c38bff",isr:"#ff6b6b",maritime:"#4dd0e1",airlift:"#4fb3ff",bomber:"#ff3d7f",fighter:"#9be564",trainer:"#8b96a3",helo:"#bcaaa4",vip:"#eeeeee",utility:"#b0bec5",other:"#78909c"};
const LC={"normal":"#5fbf77","elevado":"#ffb547","alto":"#ff7043","muito alto":"#ff3d3d"};
const EV={emergencia:"🚨 Emergência",mudanca_rota:"↪️ Mudança de rota",orbita:"⭕ Órbita",descida_rapida:"⬇️ Descida rápida",sinal_perdido:"📡 Sinal perdido",anomalia:"📈 Atividade anômala"};
let META=null,tab="live",selHex=null,selRegion=null,timer=null,fitNext=true;
function fitLayer(){if(!map||!fitNext)return;fitNext=false;const ls=layer.getLayers();if(!ls.length)return;
 const g=L.featureGroup(ls);try{map.fitBounds(g.getBounds(),{padding:[30,30],maxZoom:6})}catch(e){}}
let map=null,layer=null,overlay=null;
if(window.L){map=L.map("map",{worldCopyJump:true,minZoom:2}).setView([20,0],2);
 L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",{maxZoom:12,attribution:"&copy; OpenStreetMap &copy; CARTO"}).addTo(map);
 layer=L.layerGroup().addTo(map);overlay=L.layerGroup().addTo(map);
}else{$("map").innerHTML='<p class="mut pad">Mapa indisponível (biblioteca de mapas não carregou).</p>'}
async function j(u){const r=await fetch(u);if(!r.ok)throw new Error("HTTP "+r.status);return r.json()}
function qs(extra){const p=new URLSearchParams({region:$("fRegion").value,cat:$("fCat").value,nato:$("fNato").checked?"1":"0",br:$("fBr").checked?"1":"0",days:$("fDays").value,...(extra||{})});return p.toString()}
const catName=c=>META?.categories[c]||c||"–";const ctry=c=>META?.countries[c]||c||"–";
function table(head,rowsHtml,empty){$("thead").innerHTML="<tr>"+head.map(h=>`<th>${h}</th>`).join("")+"</tr>";
 $("tbody").innerHTML=rowsHtml.join("")||`<tr><td class="mut" colspan="${head.length}">${empty||"Sem dados."}</td></tr>`}
function arrow(color,deg){return L.divIcon({className:"arr",iconSize:[18,18],iconAnchor:[9,9],
 html:`<svg width="18" height="18" viewBox="0 0 18 18" style="transform:rotate(${deg||0}deg)"><path d="M9 1 L15 16 L9 12 L3 16 Z" fill="${color}" stroke="#0b0f13" stroke-width="1"/></svg>`})}
function drawTrack(pts,color,label,g,w){for(let i=1;i<pts.length;i++){const a=pts[i-1],b=pts[i];
 if(Math.abs(a[1]-b[1])>180)continue;L.polyline([[a[0],a[1]],[b[0],b[1]]],{color:color,weight:w||2,opacity:.85}).bindPopup(label).addTo(g)}}

async function kpis(){const s=await j("api/stats?"+qs());
 $("k1").textContent=s.live;$("k2").textContent=s.live_nato;$("k3").textContent=s.aircraft_24h;$("k4").textContent=s.events_24h;$("k5").textContent=s.br_period;$("k6").textContent=fmt(s.last_point);return s}

// ---------------- Agora
async function loadLive(){
 $("mapTitle").textContent="Aeronaves militares no ar agora (seta = rumo; clique para detalhes e previsão)";
 const data=await j("api/live?"+qs());
 if(map){layer.clearLayers();data.forEach(a=>{
  const m=L.marker([a.lat,a.lon],{icon:arrow(CC[a.category]||CC.other,a.track)}).addTo(layer);
  m.bindTooltip(`${esc(a.type)} ${esc(a.reg||a.hex)} · ${esc(a.callsign)}`);m.on("click",()=>selectAircraft(a.hex))})}
 const byCat={};data.forEach(a=>byCat[a.category]=(byCat[a.category]||0)+1);
 if(!selHex){$("sideTitle").textContent="No ar agora, por categoria";
  $("side").innerHTML='<div class="pad"><table><tbody>'+Object.entries(byCat).sort((a,b)=>b[1]-a[1]).map(([c,n])=>`<tr><td><span class="dot" style="background:${CC[c]||CC.other}"></span>${esc(catName(c))}</td><td>${n}</td></tr>`).join("")+'</tbody></table><p class="note">Clique numa aeronave no mapa ou na tabela para ver rota recente, projeção e destino provável.</p></div>'}
 $("tblTitle").textContent=`Aeronaves no ar (${data.length})`;
 table(["Tipo","Categoria","Registro","Callsign","País","Região","Alt.","Vel.","Rumo","Destino provável"],
  data.map(a=>`<tr data-hex="${esc(a.hex)}" class="${a.hex===selHex?"sel":""}"><td>${esc(a.type)}</td><td><span class="dot" style="background:${CC[a.category]||CC.other}"></span>${esc(catName(a.category))}</td><td>${esc(a.reg)}</td><td>${esc(a.callsign)}</td><td>${esc(ctry(a.country))}</td><td>${esc(a.region)}${a.orbit_since?" ⭕":""}</td><td>${ft(a.alt)}</td><td>${a.gs?Math.round(a.gs)+" kt":"–"}</td><td>${a.track!=null?Math.round(a.track)+"°":"–"}</td><td>${a.dest&&(a.dest.confidence!=="baixa"||a.dest.eta_min<=180)?esc(a.dest.name)+` <span class="mut">(${a.dest.confidence}, ~${a.dest.eta_min} min)</span>`:(a.orbit_since?'<span class="mut">em órbita</span>':'<span class="mut">–</span>')}</td></tr>`),
  "Nenhuma aeronave no ar com esses filtros.");
 document.querySelectorAll("#tbody tr[data-hex]").forEach(r=>r.onclick=()=>selectAircraft(r.dataset.hex));
 if(selHex)selectAircraft(selHex,true);else if(fitNext&&map){fitNext=false;map.setView([20,0],2)}
}
async function selectAircraft(hex,quiet){selHex=hex;const d=await j("api/aircraft?hex="+encodeURIComponent(hex));if(d.error)return;
 const a=d.aircraft,s=d.state||{};document.querySelectorAll("#tbody tr[data-hex]").forEach(r=>r.classList.toggle("sel",r.dataset.hex===hex));
 $("sideTitle").innerHTML=`${esc(a.type)} ${esc(a.reg||a.hex)} <a href="#" id="unsel" style="float:right">fechar</a>`;
 const preds=d.predictions.map(p=>`<tr><td>${esc(p.name)}</td><td>${p.dist_km} km</td><td>~${p.eta_min} min</td><td>${p.confidence}${p.history?` · ${p.history} pouso(s) antes`:""}</td></tr>`).join("")||'<tr><td class="mut">${d.in_orbit?"Em órbita: sem destino enquanto circula.":"Sem destino provável (em solo ou sem base conhecida na direção do voo)."}</td></tr>';
 const evs=d.events.slice(0,6).map(e=>`<tr><td>${fmt(e.ts)}</td><td>${EV[e.type]||esc(e.type)}</td></tr>`).join("");
 $("side").innerHTML=`<div class="pad"><dl><dt>Categoria</dt><dd>${esc(catName(a.category))}</dd><dt>País</dt><dd>${esc(ctry(a.country))}</dd>
 <dt>Região</dt><dd>${esc(s.region)}</dd><dt>Altitude</dt><dd>${ft(s.alt)}</dd><dt>Velocidade</dt><dd>${s.gs?Math.round(s.gs)+" kt":"–"}</dd>
 <dt>Rumo</dt><dd>${s.track!=null?Math.round(s.track)+"°":"–"}</dd><dt>Visto em</dt><dd>${d.sightings.length} passagem(ns) registradas</dd>
 <dt>Rastro</dt><dd><a target="_blank" rel="noopener" href="https://globe.adsbexchange.com/?icao=${esc(a.hex)}">ADS-B Exchange</a></dd></dl>
 <h2 style="padding:10px 0 4px;border:0">Destino provável</h2><table><tbody>${preds}</tbody></table>
 ${evs?`<h2 style="padding:10px 0 4px;border:0">Eventos recentes</h2><table><tbody>${evs}</tbody></table>`:""}
 <p class="note">Linha contínua: últimas 6 h. Tracejada: projeção de 60 min mantendo rumo e velocidade.</p></div>`;
 $("unsel").onclick=e=>{e.preventDefault();selHex=null;if(overlay)overlay.clearLayers();loadLive()};
 if(map){overlay.clearLayers();const g=L.featureGroup().addTo(overlay);drawTrack(d.track,"#ffffff",esc(a.reg||a.hex),g,3);
  if(d.projection&&d.projection.length&&s.lat!=null)L.polyline([[s.lat,s.lon],...d.projection.map(p=>[p[0],p[1]])],{color:"#ffffff",dashArray:"6 6",weight:2}).addTo(g);
  d.predictions.slice(0,1).forEach(p=>L.circleMarker([p.lat,p.lon],{radius:7,color:"#ffb547",weight:2,fill:false}).bindTooltip("Destino provável: "+esc(p.name)).addTo(g));
  if(!quiet&&g.getLayers().length)map.fitBounds(g.getBounds(),{padding:[40,40],maxZoom:7})}}

// ---------------- Regiões
async function loadRegions(){
 $("mapTitle").textContent="Índice de Atividade Anômala por região (últimas 24 h vs. histórico)";
 const d=await j("api/regions");selHex=null;
 const byName={};d.index.forEach(i=>byName[i.region]=i);
 if(map){layer.clearLayers();overlay.clearLayers();Object.entries(META.boxes).forEach(([name,b])=>{const i=byName[name];if(!i)return;
  const col=i.level?LC[i.level]:"#56606b";
  L.rectangle([[b[1],b[0]],[b[3],b[2]]],{color:col,weight:1,fillColor:col,fillOpacity:i.level&&i.level!=="normal"?.35:.12})
   .bindTooltip(`${esc(name)}: ${i.level||"coletando base"} · ${d.live[name]||0} no ar`).on("click",()=>selectRegion(name,d)).addTo(layer)})}
 const pct=Math.min(100,Math.round(d.days_collected/d.min_days*100));
 $("sideTitle").textContent="Regiões";
 $("side").innerHTML=`<div class="pad note">Base histórica: ${d.days_collected} de ${d.min_days} dias necessários.<div class="bar"><i style="width:${pct}%"></i></div></div>`+
  d.index.map(i=>{const col=i.level?LC[i.level]:"#8b96a3";const lab=i.level?i.level:(i.status==="sem_dados"?"sem dados 24h":"coletando "+i.n_base+"/"+i.min_days);
   return `<div class="rc" data-r="${esc(i.region)}"><div class="t"><b>${esc(i.region)}</b><span class="lvl" style="color:${col};border-color:${col}">${esc(lab)}</span></div>
   <div class="mut">${d.live[i.region]||0} no ar · atividade 24h ${i.current}${i.mean!=null?" · média "+i.mean+" · "+(i.z>=0?"+":"")+i.z+" desvios":""}</div></div>`}).join("");
 document.querySelectorAll(".rc[data-r]").forEach(e=>e.onclick=()=>selectRegion(e.dataset.r,d));
 selectRegion(selRegion&&byName[selRegion]?selRegion:(d.index[0]||{}).region,d);
}
function selectRegion(name,d){if(!name){table(["Categoria"],[],"Ainda não há dados agregados (a primeira hora fecha em até 60 min).");return}
 selRegion=name;const i=d.index.find(x=>x.region===name);
 document.querySelectorAll(".rc").forEach(e=>e.style.background=e.dataset.r===name?"#1f2a36":"");
 $("tblTitle").textContent=`${name}: aeronave-horas nas últimas 24 h por categoria`;
 table(["Categoria","Últimas 24 h","Média histórica","Variação","Peso no índice"],i.categories.map(c=>{
  const v=c.mean?((c.current-c.mean)/c.mean*100):null;
  return `<tr><td><span class="dot" style="background:${CC[c.category]||CC.other}"></span>${esc(c.label)}</td><td>${c.current}</td><td>${c.mean??"–"}</td><td>${v==null?"–":(v>=0?"+":"")+Math.round(v)+"%"}</td><td class="mut">${({tanker:3,aew:3,isr:3,bomber:4,airlift:2,maritime:2,fighter:1.5,vip:1}[c.category]??0.5)}</td></tr>`}));
 if(map&&META.boxes[name]){const b=META.boxes[name];map.fitBounds([[b[1],b[0]],[b[3],b[2]]],{padding:[10,10]})}}

// ---------------- Eventos
async function loadEvents(){
 $("mapTitle").textContent="Eventos detectados no período";selHex=null;
 const ev=await j("api/events?"+qs());
 if(map){layer.clearLayers();overlay.clearLayers();ev.filter(e=>e.lat!=null).forEach(e=>
  L.circleMarker([e.lat,e.lon],{radius:6,color:e.severity==="alta"?"#ff3d3d":e.type==="orbita"?"#c38bff":"#ffb547",weight:2,fillOpacity:.5})
   .bindPopup(`${EV[e.type]||esc(e.type)}<br>${esc(e.actype)} ${esc(e.reg)}<br>${esc(e.detail)}<br>${fmt(e.ts)}`).addTo(layer));fitLayer()}
 const cnt={};ev.forEach(e=>cnt[e.type]=(cnt[e.type]||0)+1);
 $("sideTitle").textContent="Eventos por tipo";
 $("side").innerHTML='<div class="pad"><table><tbody>'+(Object.entries(cnt).map(([k,n])=>`<tr><td>${EV[k]||esc(k)}</td><td>${n}</td></tr>`).join("")||'<tr><td class="mut">Nenhum evento no período.</td></tr>')+
  '</tbody></table><p class="note">Órbita: voo circulando no mesmo lugar (típico de reabastecedor, radar e reconhecimento em serviço). Mudança de rota: novo rumo estável ≥ 60°. Sinal perdido: pode ser só falta de receptor.</p></div>';
 $("tblTitle").textContent=`Eventos (${ev.length})`;
 table(["Quando (Brasília)","Evento","Aeronave","Categoria","País","Região","Detalhe",""],ev.map(e=>`<tr data-hex="${esc(e.hex)}"><td>${fmt(e.ts)}</td><td>${EV[e.type]||esc(e.type)}</td><td>${esc(e.actype)} ${esc(e.reg)}</td><td>${esc(catName(e.category))}</td><td>${esc(ctry(e.country))}</td><td>${esc(e.region)}</td><td class="mut">${esc(e.detail)}</td><td>${e.hex?`<a target="_blank" rel="noopener" href="https://globe.adsbexchange.com/?icao=${esc(e.hex)}&showTrace=${new Date(e.ts*1000).toISOString().slice(0,10)}">rastro</a>`:""}</td></tr>`),"Nenhum evento no período.");
 document.querySelectorAll("#tbody tr[data-hex]").forEach(r=>r.onclick=()=>{const e=ev.find(x=>x.hex===r.dataset.hex);if(map&&e&&e.lat!=null)map.setView([e.lat,e.lon],6)});
}

// ---------------- Histórico
async function loadHistory(){
 $("mapTitle").textContent="Rotas registradas no período (clique numa linha para destacar)";selHex=null;
 const [hi,tr,st]=await Promise.all([j("api/sightings?"+qs()),j("api/tracks?"+qs()),j("api/stats?"+qs())]);
 const lines={};
 if(map){layer.clearLayers();overlay.clearLayers();tr.forEach(s=>{if(!s.points.length)return;const g=L.featureGroup();
  const lab=`<b>${esc(s.type)} ${esc(s.reg)}</b><br>${esc(s.callsign)}<br>${fmt(s.first_seen)}`;
  drawTrack(s.points,CC[s.category]||CC.other,lab,g,2);g.addTo(layer);lines[s.id]=g});fitLayer()}
 $("sideTitle").textContent="Mais frequentes no período";
 $("side").innerHTML='<table><thead><tr><th>Registro</th><th>Tipo</th><th>Vezes</th><th>Última</th></tr></thead><tbody>'+(st.top.map(r=>`<tr><td><span class="dot" style="background:${CC[r.category]||CC.other}"></span>${esc(r.reg||r.hex)}</td><td>${esc(r.type)}</td><td>${r.n}</td><td class="mut">${fmt(r.last)}</td></tr>`).join("")||'<tr><td class="mut">sem dados</td></tr>')+'</tbody></table>';
 $("tblTitle").textContent=`Histórico de passagens (${hi.length})`;
 table(["Início (Brasília)","Duração","Tipo","Registro","Callsign","País","Regiões","Alt. máx.","Vezes",""],hi.map(r=>`<tr data-id="${r.id}"><td>${fmt(r.first_seen)}</td><td>${dur(r.last_seen-r.first_seen)}</td><td>${esc(r.type)}</td><td>${esc(r.reg)}</td><td>${esc(r.callsign)}</td><td>${esc(ctry(r.country))}</td><td>${esc((r.regions||"").replace(/,/g,", "))}${r.entered_br?" 🇧🇷":""}</td><td>${ft(r.max_alt)}</td><td>${r.times_total>1?r.times_total+"x":"1ª"}</td><td><a target="_blank" rel="noopener" href="https://globe.adsbexchange.com/?icao=${esc(r.hex)}&showTrace=${new Date(r.first_seen*1000).toISOString().slice(0,10)}">rastro</a></td></tr>`),"Nenhuma passagem no período.");
 document.querySelectorAll("#tbody tr[data-id]").forEach(r=>r.onclick=()=>{document.querySelectorAll("#tbody tr").forEach(x=>x.classList.toggle("sel",x===r));
  if(!map)return;Object.entries(lines).forEach(([k,g])=>g.eachLayer(l=>l.setStyle({opacity:k==r.dataset.id?1:.15,weight:k==r.dataset.id?4:2})));
  if(lines[r.dataset.id])map.fitBounds(lines[r.dataset.id].getBounds(),{padding:[30,30],maxZoom:7})});
}

const LOAD={live:loadLive,regions:loadRegions,events:loadEvents,history:loadHistory};
async function refresh(){try{await kpis();await LOAD[tab]()}catch(e){$("tbody").innerHTML=`<tr><td class="mut">Erro ao carregar: ${esc(e.message)}</td></tr>`}}
function setTab(t){tab=t;document.querySelectorAll("#tabs button").forEach(b=>b.classList.toggle("on",b.dataset.t===t));
 $("wBr").style.display=t==="history"?"":"none";$("fDays").style.display=(t==="events"||t==="history")?"":"none";
 fitNext=true;if(overlay)overlay.clearLayers();clearInterval(timer);timer=setInterval(refresh,t==="live"?60000:180000);refresh()}
document.querySelectorAll("#tabs button").forEach(b=>b.onclick=()=>setTab(b.dataset.t));
["fRegion","fCat","fNato","fBr","fDays"].forEach(id=>$(id).onchange=()=>{fitNext=true;refresh()});$("reload").onclick=refresh;
(async()=>{try{META=await j("api/meta");}catch(e){META={regions:[],boxes:{},categories:{},countries:{},min_days:14}}
 META.regions.forEach(r=>$("fRegion").insertAdjacentHTML("beforeend",`<option>${esc(r)}</option>`));
 Object.entries(META.categories).forEach(([k,v])=>$("fCat").insertAdjacentHTML("beforeend",`<option value="${k}">${esc(v)}</option>`));
 setTab("live")})();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "mil-watch"

    def log_message(self, fmt, *args):
        pass

    def authorized(self):
        if not PASSWORD:
            return True
        h = self.headers.get("Authorization", "")
        if not h.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(h[6:]).decode().partition(":")
        except Exception:
            return False
        return hmac.compare_digest(user, USER) and hmac.compare_digest(pw, PASSWORD)

    def send(self, code, body, ctype):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/health":
            return self.send(200, "ok", "text/plain")
        if not self.authorized():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="mil-watch"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path in ("/", "/index.html"):
                self.send(200, PAGE, "text/html; charset=utf-8")
            elif url.path in ROUTES:
                self.send(200, json.dumps(ROUTES[url.path](q)), "application/json")
            else:
                self.send(404, "não encontrado", "text/plain; charset=utf-8")
        except (sqlite3.OperationalError, ValueError) as e:
            self.send(503, json.dumps({"erro": f"indisponível: {e}"}), "application/json")


def main():
    if not PASSWORD:
        print("AVISO: DASHBOARD_PASSWORD vazio, painel sem senha!")
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"Painel em http://{BIND}:{PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
