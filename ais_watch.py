"""Tráfego de navios no Estreito de Ormuz via AIS público (aisstream.io).

Conta travessias do estreito (entrada/saída do Golfo Pérsico) e navios presentes por hora.
É o termômetro mais factual da crise: quando o tráfego cai, a tensão subiu.

Limitações: depende de receptores AIS em terra na região; navios podem desligar o AIS
(comum em zona de conflito); navios de guerra quase nunca transmitem.

Requer: AISSTREAM_API_KEY no .env (chave gratuita em https://aisstream.io) e o pacote
`websockets` (python3-websockets no Ubuntu/Debian).
"""
import asyncio
import json
import logging
import os
import time

import geo
import store


def load_env(path=".env"):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env()
API_KEY = os.getenv("AISSTREAM_API_KEY", "").strip()
DB_PATH = os.getenv("DB_PATH", "data/milwatch.db")
URL = os.getenv("AISSTREAM_URL", "wss://stream.aisstream.io/v0/stream")
# Golfo Pérsico + Golfo de Omã: lat_min, lon_min, lat_max, lon_max
BBOX = [float(x) for x in os.getenv("AIS_BBOX", "22.0,47.5,30.6,60.5").split(",")]
STRAIT_CENTER = (26.45, 56.40)
FLUSH_SECONDS = 60

SHIP_TYPES = {"tanker": range(80, 90), "cargo": range(70, 80), "passenger": range(60, 70)}


def side_of(lat, lon):
    """Dentro do Golfo Pérsico ou fora (Golfo de Omã/mar aberto).

    A península de Musandam separa os dois: a oeste de 56,0°E é Golfo; entre 56,0° e 56,45°E,
    só é Golfo ao norte de 25,9°N (o estreito em si). Fujairah (56,35°E; 25,1°N) fica fora.
    """
    if lon < 56.0 or (lon < 56.45 and lat >= 25.9):
        return "golfo"
    return "oma"


def ship_class(t):
    if t is None:
        return "desconhecido"
    if t == 35:
        return "militar"
    for name, rng in SHIP_TYPES.items():
        if t in rng:
            return name
    return "outro"


class Tracker:
    """Estado em memória; grava no banco a cada minuto. Sem rede: fácil de testar."""

    def __init__(self):
        self.ships = {}          # mmsi -> dict(name, type, lat, lon, sog, cog, ts, side)
        self.transits = []       # pendentes de gravação
        self.hour = None
        self.hour_seen = {"golfo": set(), "oma": set()}
        self.last_msg = 0

    def on_message(self, msg, now=None):
        now = now or int(time.time())
        self.last_msg = now
        mtype = msg.get("MessageType")
        body = (msg.get("Message") or {}).get(mtype) or {}
        meta = msg.get("MetaData") or {}
        mmsi = str(body.get("UserID") or meta.get("MMSI") or "")
        if not mmsi:
            return None
        s = self.ships.setdefault(mmsi, {"name": "", "type": None, "lat": None, "lon": None,
                                         "sog": None, "cog": None, "ts": 0, "side": None})
        if mtype == "ShipStaticData" and (body.get("Name") or "").strip():
            s["name"] = body["Name"].strip()          # nome oficial (dados estáticos) tem prioridade
        elif not s["name"] and (meta.get("ShipName") or "").strip():
            s["name"] = meta["ShipName"].strip()
        if mtype == "ShipStaticData":
            if isinstance(body.get("Type"), int) and body["Type"] > 0:
                s["type"] = body["Type"]
            return None
        if mtype not in ("PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"):
            return None
        lat, lon = body.get("Latitude"), body.get("Longitude")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)) or abs(lat) > 90 or abs(lon) > 180:
            return None
        return self.on_position(mmsi, lat, lon, body.get("Sog"), body.get("Cog"), now)

    def on_position(self, mmsi, lat, lon, sog, cog, now):
        s = self.ships[mmsi]
        self._roll_hour(now)
        side = side_of(lat, lon)
        transit = None
        if (s["side"] and s["side"] != side and now - s["ts"] <= 3 * 3600
                and geo.haversine_km(lat, lon, *STRAIT_CENTER) <= 250):
            direction = "entrada" if side == "golfo" else "saida"
            transit = {"ts": now, "mmsi": mmsi, "name": s["name"], "type": s["type"],
                       "direction": direction, "lat": lat}
            self.transits.append(transit)
        s.update(lat=lat, lon=lon, sog=sog, cog=cog, ts=now, side=side)
        self.hour_seen[side].add(mmsi)
        return transit

    def _roll_hour(self, now):
        h = now // 3600 * 3600
        if self.hour is None:
            self.hour = h
        elif h != self.hour:
            self.pending_hour = (self.hour, {z: set(v) for z, v in self.hour_seen.items()})
            self.hour = h
            self.hour_seen = {"golfo": set(), "oma": set()}

    def flush(self, con, now=None):
        now = now or int(time.time())
        rows = [(m, s["name"], s["type"], s["lat"], s["lon"], s["sog"], s["cog"], s["ts"])
                for m, s in self.ships.items() if s["lat"] is not None and s["ts"] >= now - 120]
        con.executemany("""INSERT INTO ais_ships (mmsi, name, type, lat, lon, sog, cog, ts)
                           VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(mmsi) DO UPDATE SET
                           name=COALESCE(NULLIF(excluded.name,''), name), type=COALESCE(excluded.type, type),
                           lat=excluded.lat, lon=excluded.lon, sog=excluded.sog, cog=excluded.cog,
                           ts=excluded.ts""", rows)
        con.executemany("""INSERT INTO ais_transits (ts, mmsi, name, type, direction, lat)
                           VALUES (:ts,:mmsi,:name,:type,:direction,:lat)""", self.transits)
        n_tr = len(self.transits)
        self.transits = []
        ph = getattr(self, "pending_hour", None)
        if ph:
            hour, seen = ph
            for zone, ms in seen.items():
                tankers = sum(1 for m in ms if ship_class(self.ships.get(m, {}).get("type")) == "tanker")
                con.execute("INSERT OR REPLACE INTO ais_hourly VALUES (?,?,?,?)", (hour, zone, len(ms), tankers))
            self.pending_hour = None
        store.meta_set(con, "ais_last_msg", self.last_msg)
        con.execute("DELETE FROM ais_ships WHERE ts < ?", (now - 7 * 86400,))
        # navios que ficaram horas sem posição perdem o "lado" (evita travessia falsa ao reaparecer)
        for s in self.ships.values():
            if s["ts"] < now - 3 * 3600:
                s["side"] = None
        con.commit()
        return n_tr


async def run(tracker, con):
    import websockets
    sub = {"APIKey": API_KEY, "BoundingBoxes": [[[BBOX[0], BBOX[1]], [BBOX[2], BBOX[3]]]],
           "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport",
                                  "ExtendedClassBPositionReport", "ShipStaticData"]}
    backoff = 5
    last_flush = time.time()
    while True:
        try:
            async with websockets.connect(URL, compression="deflate", ping_interval=30,
                                          max_size=2 ** 22, open_timeout=30) as ws:
                await ws.send(json.dumps(sub))
                logging.info("AIS conectado; área %s", BBOX)
                backoff = 5
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=180)
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    if "error" in msg:
                        logging.error("aisstream recusou: %s", msg["error"])
                        await asyncio.sleep(300)
                        break
                    tr = tracker.on_message(msg)
                    if tr:
                        logging.info("Travessia: %s %s (%s)", tr["direction"], tr["name"] or tr["mmsi"],
                                     ship_class(tr["type"]))
                    if time.time() - last_flush >= FLUSH_SECONDS:
                        tracker.flush(con)
                        last_flush = time.time()
        except asyncio.TimeoutError:
            logging.warning("AIS: 3 min sem mensagens; reconectando")
        except Exception as e:
            logging.warning("AIS: conexão caiu (%s); nova tentativa em %ss", e, backoff)
        try:
            tracker.flush(con)
        except Exception as e:
            logging.error("AIS: falha ao gravar: %s", e)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not API_KEY:
        logging.info("AISSTREAM_API_KEY vazio: módulo de Ormuz desligado. "
                     "Crie uma chave grátis em https://aisstream.io e coloque no .env.")
        return
    con = store.connect(DB_PATH)
    asyncio.run(run(Tracker(), con))


if __name__ == "__main__":
    main()
