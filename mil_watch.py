"""mil-watch-br v2: coleta global de aeronaves militares em ADS-B público.

- Guarda todas as aeronaves militares do mundo, por região.
- Detecta eventos: mudança brusca de rota, órbita, emergência, descida rápida, perda de sinal.
- Mantém o alerta original: militares (padrão: dos EUA) entrando no Brasil.
- Agrega atividade por hora e região para o Índice de Atividade Anômala.
- Envia alertas e resumo diário pelo Telegram.

Somente biblioteca padrão. Configuração por variáveis de ambiente (.env).
"""
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

import analysis
import catalog
import geo
import store
from geo import in_brazil, is_us_hex  # noqa: F401  (usados pelos testes e por scripts)


def load_env(path=".env"):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env()
env = os.getenv
TG_TOKEN = env("TELEGRAM_TOKEN", "")
TG_CHAT = env("TELEGRAM_CHAT_ID", "")
INTERVAL = int(env("INTERVAL_SECONDS", "90"))
DB_PATH = env("DB_PATH", "data/milwatch.db")
LOG_PATH = env("LOG_PATH", "data/milwatch.log")
ALERT_COOLDOWN_H = float(env("ALERT_COOLDOWN_HOURS", "6"))
SESSION_GAP = float(env("SESSION_GAP_MINUTES", "30")) * 60
REPORT_EVERY_DAYS = float(env("REPORT_EVERY_DAYS", "7"))
SOURCES = [s.strip() for s in env(
    "API_SOURCES", "https://api.adsb.lol/v2/mil,https://api.airplanes.live/v2/mil").split(",") if s.strip()]
BR_SCOPE = env("BRAZIL_ALERT_SCOPE", "US").upper()          # US | NATO | FOREIGN | ALL
ALERT_EVENTS = {e.strip() for e in env("ALERT_EVENTS", "emergencia,anomalia").split(",") if e.strip()}
ALERT_EVENT_REGIONS = {r.strip() for r in env("ALERT_EVENT_REGIONS", "").split(",") if r.strip()}
SENSITIVE_REGIONS = {r.strip() for r in env(
    "SENSITIVE_REGIONS",
    "Brasil,América do Sul,Caribe e América Central,Mar Negro,Europa Oriental e Báltico,Rússia Ocidental,"
    "Oriente Médio e Golfo,Mar Vermelho e Chifre da África,Mar do Sul da China e Taiwan,Leste Asiático"
).split(",") if r.strip()}
ANOMALY_ALERT_Z = float(env("ANOMALY_ALERT_Z", "3.0"))
ANOMALY_MIN_DAYS = int(env("ANOMALY_MIN_DAYS", "14"))
ANOMALY_BASELINE_DAYS = int(env("ANOMALY_BASELINE_DAYS", "28"))
RAW_RETENTION_DAYS = int(env("RAW_RETENTION_DAYS", "30"))
DIGEST_HOUR = int(env("DIGEST_HOUR", "8"))                   # hora local; -1 desliga
TIMEZONE = env("TIMEZONE", "America/Sao_Paulo")

EVENT_LABELS = {
    "emergencia": "🚨 Código de emergência", "mudanca_rota": "↪️ Mudança brusca de rota",
    "orbita": "⭕ Entrou em órbita", "descida_rapida": "⬇️ Descida rápida",
    "sinal_perdido": "📡 Sinal perdido em área sensível", "anomalia": "📈 Atividade anômala",
}
EMERGENCY = {"7500": "interferência ilícita", "7600": "falha de rádio", "7700": "emergência geral"}


def local_now(ts=None):
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(ts or time.time(), ZoneInfo(TIMEZONE))
    except Exception:
        return datetime.fromtimestamp(ts or time.time(), timezone.utc)


# ---------------------------------------------------------------- coleta
def fetch_mil(sources=SOURCES, timeout=20):
    last_err = None
    for url in sources:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mil-watch-br/2.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
            return data.get("ac") or data.get("aircraft") or [], url
        except Exception as e:
            last_err = e
            logging.warning("Falha em %s: %s", url, e)
    raise RuntimeError(f"Todas as fontes falharam: {last_err}")


def normalize(ac):
    alt = ac.get("alt_baro")
    if alt == "ground":
        alt = 0
    if not isinstance(alt, (int, float)):
        alt = ac.get("alt_geom") if isinstance(ac.get("alt_geom"), (int, float)) else None
    hx = (ac.get("hex") or "").lower().lstrip("~")
    tp = (ac.get("t") or "").upper()
    vr = ac.get("baro_rate", ac.get("geom_rate"))
    return {
        "hex": hx, "reg": ac.get("r") or "", "type": tp,
        "callsign": (ac.get("flight") or "").strip(),
        "lat": ac.get("lat"), "lon": ac.get("lon"), "alt": alt,
        "gs": ac.get("gs"), "track": ac.get("track"),
        "vrate": vr if isinstance(vr, (int, float)) else None,
        "squawk": str(ac.get("squawk") or ""),
        "category": catalog.category_of(tp), "country": geo.country_of(hx),
    }


def in_brazil_scope(a):
    c = a["country"]
    if BR_SCOPE == "ALL":
        return True
    if BR_SCOPE == "NATO":
        return c in geo.NATO
    if BR_SCOPE == "FOREIGN":
        return c != "BR"
    return is_us_hex(a["hex"])


def filter_targets(aircraft, polygons=None):
    """Aeronaves dentro do Brasil e no escopo de alerta (padrão: EUA)."""
    out = []
    for ac in aircraft:
        a = normalize(ac)
        if a["lat"] is None or a["lon"] is None or not in_brazil_scope(a):
            continue
        if in_brazil(a["lon"], a["lat"], polygons):
            out.append(a)
    return out


def db_connect(path=DB_PATH):
    return store.connect(path)


# ---------------------------------------------------------------- texto
def describe(a):
    cat = catalog.CATEGORY_LABELS.get(a.get("category"), "")
    pais = geo.COUNTRY_NAMES.get(a.get("country"), a.get("country") or "?")
    return (f"{a.get('type') or 'n/d'} ({cat}) | Registro: {a.get('reg') or 'n/d'} | "
            f"Callsign: {a.get('callsign') or 'n/d'} | País: {pais}")


def fmt_alt(alt):
    if alt == 0:
        return "em solo"
    return f"{int(alt):,} ft".replace(",", ".") if alt else "n/d"


def format_alert(a, prev_count):
    hist = "primeira vez no banco" if prev_count == 0 else f"já visto {prev_count}x antes"
    return (
        "✈️ Aeronave militar no espaço brasileiro\n"
        f"Tipo: {a['type'] or 'n/d'} | Registro: {a['reg'] or 'n/d'} | Callsign: {a['callsign'] or 'n/d'}\n"
        f"Posição: {a['lat']:.3f}, {a['lon']:.3f} | Alt: {fmt_alt(a['alt'])} | "
        f"Vel: {a['gs'] or 'n/d'} kt | Rumo: {a['track'] or 'n/d'}°\n"
        f"Histórico: {hist}\n"
        f"Rastro: https://globe.adsbexchange.com/?icao={a['hex']}\n"
        "Obs.: presença no ADS-B não indica propósito da missão."
    )


def format_event(kind, a, region, detail):
    return (f"{EVENT_LABELS.get(kind, kind)} | {region}\n{describe(a)}\n{detail}\n"
            f"Posição: {a['lat']:.3f}, {a['lon']:.3f} | Alt: {fmt_alt(a.get('alt'))}\n"
            f"Rastro: https://globe.adsbexchange.com/?icao={a['hex']}")


def send_telegram(text):
    if not (TG_TOKEN and TG_CHAT):
        logging.info("Telegram não configurado. Mensagem:\n%s", text)
        return
    data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text[:4000],
                                   "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                               data=data, timeout=20).read()
    except Exception as e:
        logging.error("Falha ao enviar Telegram: %s", e)


# ---------------------------------------------------------------- alertas
def should_alert(con, key, now, kind="brazil", hours=None):
    hours = ALERT_COOLDOWN_H if hours is None else hours
    row = con.execute("SELECT MAX(ts) FROM alerts WHERE hex=? AND kind=?", (key, kind)).fetchone()
    return row[0] is None or now - row[0] >= hours * 3600


def mark_alert(con, key, now, kind="brazil"):
    con.execute("INSERT INTO alerts (hex, ts, kind) VALUES (?,?,?)", (key, now, kind))


def previous_sightings(con, hexcode, exclude_id):
    return con.execute("SELECT COUNT(*) FROM sightings WHERE hex=? AND id<>? AND entered_br=1",
                       (hexcode, exclude_id)).fetchone()[0]


def add_event(con, kind, severity, a, now, sighting_id, region, detail, outbox):
    con.execute("""INSERT INTO events (ts, hex, sighting_id, type, severity, region, lat, lon, detail)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (now, a["hex"], sighting_id, kind, severity, region, a["lat"], a["lon"], detail))
    if kind not in ALERT_EVENTS:
        return
    if kind != "emergencia" and ALERT_EVENT_REGIONS and region not in ALERT_EVENT_REGIONS:
        return
    if should_alert(con, a["hex"], now, kind=kind):
        msg = format_event(kind, a, region, detail)
        send_telegram(msg)
        mark_alert(con, a["hex"], now, kind=kind)
        outbox.append(msg)


def recent_event(con, hexcode, kind, now, seconds=1800):
    return con.execute("SELECT 1 FROM events WHERE hex=? AND type=? AND ts>=? LIMIT 1",
                       (hexcode, kind, now - seconds)).fetchone() is not None


# ---------------------------------------------------------------- processamento por aeronave
def process(con, a, now, outbox):
    hx = a["hex"]
    region = geo.region_of(a["lon"], a["lat"])
    inbr = region == "Brasil"
    con.execute("""INSERT INTO aircraft (hex, reg, type, category, country, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(hex) DO UPDATE SET last_seen=excluded.last_seen,
                     reg=COALESCE(NULLIF(excluded.reg,''), reg), type=COALESCE(NULLIF(excluded.type,''), type),
                     category=CASE WHEN excluded.type<>'' THEN excluded.category ELSE category END""",
                (hx, a["reg"], a["type"], a["category"], a["country"], now, now))
    st = con.execute("SELECT * FROM state WHERE hex=?", (hx,)).fetchone()
    cols = [d[0] for d in con.execute("SELECT * FROM state LIMIT 0").description]
    st = dict(zip(cols, st)) if st else None
    new_session = st is None or now - st["ts"] > SESSION_GAP

    if new_session:
        sid = con.execute("""INSERT INTO sightings (hex, reg, type, callsign, first_seen, last_seen,
                               first_lat, first_lon, last_lat, last_lon, category, country,
                               region_first, regions, entered_br) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                          (hx, a["reg"], a["type"], a["callsign"], now, now, a["lat"], a["lon"],
                           a["lat"], a["lon"], a["category"], a["country"], region, region)).lastrowid
    else:
        sid = st["sighting_id"]
        row = con.execute("SELECT regions FROM sightings WHERE id=?", (sid,)).fetchone()
        regs = (row[0] or "").split(",") if row else []
        if region not in regs:
            regs.append(region)
        con.execute("""UPDATE sightings SET last_seen=?, last_lat=?, last_lon=?, n_points=n_points+1,
                       callsign=COALESCE(NULLIF(?, ''), callsign), regions=? WHERE id=?""",
                    (now, a["lat"], a["lon"], a["callsign"], ",".join(r for r in regs if r), sid))

    # ponto bruto (aeronave parada em solo: no máximo um ponto a cada 10 min)
    parked = False
    if not new_session and a["alt"] == 0 and st["alt"] == 0 \
            and abs((st["lat"] or 0) - a["lat"]) < 0.01 and abs((st["lon"] or 0) - a["lon"]) < 0.01:
        last_pt = con.execute("SELECT MAX(ts) FROM points WHERE hex=?", (hx,)).fetchone()[0] or 0
        parked = now - last_pt < 600
    if not parked:
        con.execute("""INSERT INTO points (hex, reg, type, callsign, lat, lon, alt, gs, track, ts,
                       vrate, squawk, region) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (hx, a["reg"], a["type"], a["callsign"], a["lat"], a["lon"], a["alt"], a["gs"],
                     a["track"], now, a["vrate"], a["squawk"], region))

    orbit_since = None if new_session else st["orbit_since"]
    last_orbit_check = 0 if new_session else (st["last_orbit_check"] or 0)

    if not new_session:
        # emergência
        if a["squawk"] in EMERGENCY and st["squawk"] != a["squawk"]:
            add_event(con, "emergencia", "alta", a, now, sid, region,
                      f"Squawk {a['squawk']} ({EMERGENCY[a['squawk']]})", outbox)
        heavy = a["category"] in catalog.HEAVY
        airborne = (a["alt"] or 0) >= 8000 and (a["gs"] or 0) >= 180
        # descida rápida
        if heavy and (a["vrate"] or 0) <= -5000 and (a["alt"] or 0) >= 10000 \
                and not recent_event(con, hx, "descida_rapida", now):
            add_event(con, "descida_rapida", "média", a, now, sid, region,
                      f"Razão de descida {int(a['vrate'])} ft/min", outbox)
        # mudança de rota (rumo estável novo diferente do rumo estável anterior; fora de órbita)
        if heavy and a["category"] != "maritime" and (a["alt"] or 0) >= 15000 and airborne \
                and orbit_since is None and a["track"] is not None \
                and not recent_event(con, hx, "mudanca_rota", now):
            pts = con.execute("SELECT ts, lat, lon, track FROM points WHERE hex=? AND ts>=? ORDER BY ts",
                              (hx, now - 1200)).fetchall()
            rc = analysis.detect_route_change(pts, now)
            # meia-volta de reabastecedor/radar/ISR costuma ser hipódromo: fica para a detecção de órbita
            if rc and a["category"] in catalog.ORBIT_RELEVANT and geo.angle_diff(rc[0], rc[1]) >= 150:
                rc = None
            if rc:
                add_event(con, "mudanca_rota", "média", a, now, sid, region,
                          f"Rumo {rc[0]}° → {rc[1]}°", outbox)
        # órbita (a cada 5 min, só categorias em que órbita significa "em estação")
        if a["category"] in catalog.ORBIT_RELEVANT and now - last_orbit_check >= 300:
            last_orbit_check = now
            pts = con.execute("SELECT ts, lat, lon, track FROM points WHERE hex=? AND ts>=? ORDER BY ts",
                              (hx, now - 3600)).fetchall()
            orb = analysis.detect_orbit(pts)
            if orb and orbit_since is None:
                orbit_since = now
                add_event(con, "orbita", "média", a, now, sid, region,
                          f"Órbita centrada em {orb[0]:.2f}, {orb[1]:.2f} (raio ~{round(orb[2])} km)", outbox)
            elif not orb and orbit_since is not None and now - orbit_since > 1800:
                orbit_since = None
        # pouso
        if a["alt"] == 0 and (st["alt"] or 0) > 0:
            record_landing(con, hx, a["callsign"], a["lat"], a["lon"], now, sid)

    # entrada no Brasil (alerta original)
    if inbr and (new_session or not st["in_br"]) and in_brazil_scope(a):
        con.execute("UPDATE sightings SET entered_br=1 WHERE id=?", (sid,))
        if should_alert(con, hx, now):
            msg = format_alert(a, previous_sightings(con, hx, sid))
            send_telegram(msg)
            mark_alert(con, hx, now)
            outbox.append(msg)

    con.execute("""INSERT INTO state (hex, sighting_id, ts, lat, lon, alt, gs, track, squawk, region,
                     in_br, orbit_since, last_orbit_check, lost)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)
                   ON CONFLICT(hex) DO UPDATE SET sighting_id=excluded.sighting_id, ts=excluded.ts,
                     lat=excluded.lat, lon=excluded.lon, alt=excluded.alt, gs=excluded.gs,
                     track=excluded.track, squawk=excluded.squawk, region=excluded.region,
                     in_br=excluded.in_br, orbit_since=excluded.orbit_since,
                     last_orbit_check=excluded.last_orbit_check, lost=0""",
                (hx, sid, now, a["lat"], a["lon"], a["alt"], a["gs"], a["track"], a["squawk"],
                 region, int(inbr), orbit_since, last_orbit_check))


def record_landing(con, hx, callsign, lat, lon, now, sid):
    row = con.execute("SELECT landed_lat FROM sightings WHERE id=?", (sid,)).fetchone()
    if row and row[0] is not None:
        return
    con.execute("INSERT INTO landings (ts, hex, callsign, lat, lon) VALUES (?,?,?,?,?)",
                (now, hx, callsign, lat, lon))
    con.execute("UPDATE sightings SET landed_lat=?, landed_lon=? WHERE id=?", (lat, lon, sid))


def sweep_stale(con, now, outbox):
    """Aeronaves que sumiram: registra pouso provável ou perda de sinal em área sensível."""
    rows = con.execute("""SELECT s.hex, s.sighting_id, s.lat, s.lon, s.alt, s.gs, s.region,
                                 a.type, a.reg, a.category, a.country, sg.callsign
                          FROM state s LEFT JOIN aircraft a ON a.hex=s.hex
                          LEFT JOIN sightings sg ON sg.id=s.sighting_id
                          WHERE s.lost=0 AND s.ts < ? AND s.ts > ?""",
                       (now - 1200, now - 3 * 3600)).fetchall()
    for hx, sid, lat, lon, alt, gs, region, tp, reg, cat, country, cs in rows:
        a = {"hex": hx, "lat": lat, "lon": lon, "alt": alt, "type": tp, "reg": reg,
             "category": cat, "country": country, "callsign": cs}
        if alt is not None and 0 < alt <= 3000 and (gs or 0) < 220:
            record_landing(con, hx, cs, lat, lon, now, sid)
        elif (alt or 0) >= 15000 and region in SENSITIVE_REGIONS:
            add_event(con, "sinal_perdido", "baixa", a, now, sid, region,
                      f"Último sinal a {fmt_alt(alt)}; pode ser só falta de cobertura", outbox)
        con.execute("UPDATE state SET lost=1 WHERE hex=?", (hx,))


# ---------------------------------------------------------------- tarefas periódicas
def check_anomalies(con, now, outbox):
    last = int(store.meta_get(con, "last_anomaly_check", 0))
    if now - last < 3600:
        return
    store.meta_set(con, "last_anomaly_check", now)
    for item in analysis.anomaly_index(con, now, ANOMALY_BASELINE_DAYS, ANOMALY_MIN_DAYS):
        if item["status"] != "ok" or item["z"] is None or item["z"] < ANOMALY_ALERT_Z:
            continue
        reg = item["region"]
        con.execute("""INSERT INTO events (ts, hex, type, severity, region, detail)
                       VALUES (?,?,?,?,?,?)""",
                    (now, "", "anomalia", item["level"], reg,
                     f"Índice {item['z']:+.1f} desvios (atual {item['current']} vs média {item['mean']})"))
        if "anomalia" in ALERT_EVENTS and should_alert(con, reg, now, kind="anomalia", hours=12):
            tops = [c for c in item["categories"] if c["mean"] is not None and c["current"] > c["mean"]][:4]
            det = "\n".join(f"• {c['label']}: {c['current']} (média {c['mean']})" for c in tops)
            msg = (f"📈 Atividade anômala: {reg} — nível {item['level']}\n"
                   f"{item['z']:+.1f} desvios acima do normal das últimas {item['n_base']} janelas de 24h\n"
                   f"{det}\nObs.: indica movimento fora do padrão, não prevê ataque. Exercícios também disparam.")
            send_telegram(msg)
            mark_alert(con, reg, now, kind="anomalia")
            outbox.append(msg)


def weekly_report(con, now, days=REPORT_EVERY_DAYS):
    since = now - int(days * 86400)
    rows = con.execute("""SELECT type, reg, COUNT(*) FROM sightings WHERE first_seen>=? AND entered_br=1
                          GROUP BY hex ORDER BY COUNT(*) DESC""", (since,)).fetchall()
    total = sum(r[2] for r in rows)
    first = con.execute("SELECT MIN(first_seen) FROM sightings WHERE entered_br=1").fetchone()[0]
    avg_txt = "histórico ainda insuficiente para média"
    if first and now - first > 2 * days * 86400:
        n_all = con.execute("SELECT COUNT(*) FROM sightings WHERE first_seen<? AND entered_br=1",
                            (since,)).fetchone()[0]
        periods = (since - first) / (days * 86400)
        avg_txt = f"média histórica: {n_all / periods:.1f} avistamentos por período"
    lines = [f"📊 Relatório Brasil ({int(days)} dias): {total} avistamentos, {len(rows)} aeronaves distintas",
             avg_txt]
    lines += [f"• {t or '?'} {r or '?'}: {n}x" for t, r, n in rows[:20]]
    return "\n".join(lines)


def maybe_report(con, now):
    last = store.meta_get(con, "last_report")
    if last is None:
        store.meta_set(con, "last_report", now)
    elif now - int(last) >= REPORT_EVERY_DAYS * 86400:
        send_telegram(weekly_report(con, now))
        store.meta_set(con, "last_report", now)


def daily_digest(con, now):
    since = now - 86400
    total, nato = con.execute("""SELECT COUNT(DISTINCT p.hex),
                                   COUNT(DISTINCT CASE WHEN a.country IN ({}) THEN p.hex END)
                                 FROM points p LEFT JOIN aircraft a ON a.hex=p.hex WHERE p.ts>=?"""
                              .format(",".join("?" * len(geo.NATO))), (*geo.NATO, since)).fetchone()
    regs = con.execute("""SELECT region, COUNT(DISTINCT hex) n FROM points WHERE ts>=? AND region IS NOT NULL
                          GROUP BY region ORDER BY n DESC LIMIT 8""", (since,)).fetchall()
    evs = dict(con.execute("SELECT type, COUNT(*) FROM events WHERE ts>=? GROUP BY type", (since,)).fetchall())
    br = con.execute("SELECT COUNT(*) FROM sightings WHERE entered_br=1 AND first_seen>=?", (since,)).fetchone()[0]
    idx = analysis.anomaly_index(con, now, ANOMALY_BASELINE_DAYS, ANOMALY_MIN_DAYS)
    ok = [i for i in idx if i["status"] == "ok"]
    hot = [i for i in ok if i["level"] != "normal"]
    if not ok:
        n = max((i["n_base"] for i in idx), default=0)
        idx_txt = f"base histórica em formação ({n}/{ANOMALY_MIN_DAYS} dias)"
    elif hot:
        idx_txt = "; ".join(f"{i['region']}: {i['level']} ({i['z']:+.1f})" for i in hot[:5])
    else:
        idx_txt = "todas as regiões dentro do normal"
    lines = [f"🌍 Resumo das últimas 24h — {local_now(now):%d/%m %H:%M}",
             f"Aeronaves militares vistas: {total} (OTAN: {nato})",
             "Por região: " + ", ".join(f"{r} {n}" for r, n in regs),
             "Eventos: " + (", ".join(f"{EVENT_LABELS.get(k, k).split(' ', 1)[-1].lower()}: {v}"
                                      for k, v in evs.items()) or "nenhum"),
             f"Índice de atividade: {idx_txt}",
             f"Entradas no Brasil ({BR_SCOPE}): {br}"]
    return "\n".join(lines)


def maybe_digest(con, now):
    if DIGEST_HOUR < 0:
        return
    lt = local_now(now)
    key = lt.strftime("%Y-%m-%d")
    if lt.hour >= DIGEST_HOUR and store.meta_get(con, "last_digest_day") != key:
        if store.meta_get(con, "last_digest_day") is not None:
            send_telegram(daily_digest(con, now))
        store.meta_set(con, "last_digest_day", key)


def maybe_purge(con, now):
    last = int(store.meta_get(con, "last_purge", 0))
    if now - last >= 86400:
        n = store.purge_old_points(con, now - RAW_RETENTION_DAYS * 86400)
        store.meta_set(con, "last_purge", now)
        if n:
            logging.info("Retenção: %d pontos antigos removidos (rotas resumidas mantidas)", n)


def cycle(con, aircraft=None, now=None):
    now = now or int(time.time())
    if aircraft is None:
        aircraft, src = fetch_mil()
        logging.info("%d militares no mundo | %d no escopo sobre o Brasil (%s) | fonte %s",
                     len(aircraft), len(filter_targets(aircraft)), BR_SCOPE, src)
    outbox = []
    seen = set()
    for raw in aircraft:
        a = normalize(raw)
        if a["lat"] is None or a["lon"] is None or not a["hex"] or a["hex"] in seen:
            continue
        seen.add(a["hex"])
        process(con, a, now, outbox)
    sweep_stale(con, now, outbox)
    analysis.rollup(con, now)
    check_anomalies(con, now, outbox)
    maybe_report(con, now)
    maybe_digest(con, now)
    maybe_purge(con, now)
    con.commit()
    return outbox


def setup_logging():
    os.makedirs(os.path.dirname(LOG_PATH) or ".", exist_ok=True)
    handlers = [logging.StreamHandler(),
                RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=5, encoding="utf-8")]
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(message)s")


def main():
    setup_logging()
    con = db_connect()
    logging.info("mil-watch-br v2 iniciado | intervalo %ss | escopo Brasil %s | alertas %s",
                 INTERVAL, BR_SCOPE, ",".join(sorted(ALERT_EVENTS)))
    send_telegram("🟢 mil-watch-br v2 iniciado: monitoramento global ativo")
    backoff = INTERVAL
    while True:
        t0 = time.time()
        try:
            cycle(con)
            backoff = INTERVAL
        except Exception as e:
            logging.exception("Ciclo falhou: %s", e)
            try:
                con.rollback()
            except Exception:
                pass
            backoff = min(backoff * 2, 900)
        time.sleep(max(5, backoff - (time.time() - t0)))


if __name__ == "__main__":
    main()
