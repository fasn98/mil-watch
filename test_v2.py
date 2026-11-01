import json
import math
import os
import sqlite3
import tempfile
import unittest

os.environ["TELEGRAM_TOKEN"] = ""
import analysis
import catalog
import geo
import mil_watch as mw
import store

T0 = 1_800_000_000


def ac(hexcode, lat, lon, track, alt=25000, gs=300, t="K35R", r="", flight="", squawk="1234", vrate=0):
    return {"hex": hexcode, "lat": lat, "lon": lon, "track": track, "alt_baro": alt, "gs": gs,
            "t": t, "r": r, "flight": flight, "squawk": squawk, "baro_rate": vrate}


def fly(lat, lon, track, gs, seconds):
    return geo.project(lat, lon, track, gs * 1.852 * seconds / 3600)


def new_db():
    return mw.db_connect(os.path.join(tempfile.mkdtemp(), "t.db"))


class GeoCatalog(unittest.TestCase):
    def test_regions_countries(self):
        self.assertEqual(geo.region_of(33.5, 44.6), "Mar Negro")
        self.assertEqual(geo.region_of(51.32, 25.12), "Oriente Médio e Golfo")
        self.assertEqual(geo.region_of(-43.2, -22.9), "Brasil")
        self.assertEqual(geo.country_of("43c123"), "GB")
        self.assertIn("GB", geo.NATO)
        self.assertNotIn("BR", geo.NATO)

    def test_catalog(self):
        self.assertEqual(catalog.category_of("K35R"), "tanker")
        self.assertEqual(catalog.category_of("e3tf"), "aew")
        self.assertEqual(catalog.category_of("XXXX"), "other")

    def test_projection_roundtrip(self):
        la, lo = geo.project(0, 0, 90, 111.2)
        self.assertAlmostEqual(lo, 1.0, places=1)
        self.assertAlmostEqual(geo.haversine_km(0, 0, la, lo), 111.2, places=0)


class Analysis(unittest.TestCase):
    def racetrack(self, n=40, step=90):
        pts, lat, lon, trk = [], 44.0, 31.0, 90
        for i in range(n):
            if i % 8 == 0 and i:
                trk = (trk + 180) % 360
            lat, lon = fly(lat, lon, trk, 300, step)
            pts.append((T0 + i * step, lat, lon, trk))
        return pts

    def test_orbit_detected(self):
        self.assertIsNotNone(analysis.detect_orbit(self.racetrack()))

    def test_straight_is_not_orbit(self):
        pts, lat, lon = [], 40.0, 10.0,
        for i in range(40):
            lat, lon = fly(lat, lon, 45, 300, 90)
            pts.append((T0 + i * 90, lat, lon, 45))
        self.assertIsNone(analysis.detect_orbit(pts))

    def test_route_change(self):
        pts, lat, lon = [], 40.0, 10.0
        for i in range(10):
            lat, lon = fly(lat, lon, 0, 300, 90)
            pts.append((T0 + i * 90, lat, lon, 0))
        for i in range(10, 14):
            lat, lon = fly(lat, lon, 90, 300, 90)
            pts.append((T0 + i * 90, lat, lon, 90))
        now = T0 + 13 * 90
        self.assertEqual(analysis.detect_route_change(pts, now), (0, 90))
        self.assertIsNone(analysis.detect_route_change(pts[:10], T0 + 9 * 90))


class Flow(unittest.TestCase):
    def test_route_change_and_emergency_events(self):
        con = new_db()
        lat, lon, t = 50.0, 0.0, T0
        for i in range(10):
            lat, lon = fly(lat, lon, 0, 300, 90)
            mw.cycle(con, [ac("ae1111", lat, lon, 0)], t)
            t += 90
        for i in range(4):
            lat, lon = fly(lat, lon, 90, 300, 90)
            mw.cycle(con, [ac("ae1111", lat, lon, 90)], t)
            t += 90
        out = mw.cycle(con, [ac("ae1111", lat, lon, 90, squawk="7700")], t)
        types = [r[0] for r in con.execute("SELECT type FROM events ORDER BY id")]
        self.assertIn("mudanca_rota", types)
        self.assertIn("emergencia", types)
        self.assertTrue(any("emergência geral" in m for m in out))   # emergência alerta por padrão
        self.assertEqual(types.count("mudanca_rota"), 1)

    def test_us_domestic_emergency_not_sent(self):
        con = new_db()
        mw.cycle(con, [ac("ae4d3b", 32.4, -100.2, 270, alt=3825, t="H60")], T0)
        out = mw.cycle(con, [ac("ae4d3b", 32.4, -100.3, 270, alt=3825, t="H60", squawk="7700")], T0 + 90)
        self.assertEqual(out, [])
        self.assertEqual(con.execute("SELECT type, region FROM events").fetchall(),
                         [("emergencia", "América do Norte")])

    def test_orbit_event_once(self):
        con = new_db()
        lat, lon, trk, t = 44.0, 31.0, 90, T0
        for i in range(45):
            if i % 8 == 0 and i:
                trk = (trk + 180) % 360
            lat, lon = fly(lat, lon, trk, 300, 90)
            mw.cycle(con, [ac("ae2222", lat, lon, trk, t="E3TF")], t)
            t += 90
        n = con.execute("SELECT COUNT(*) FROM events WHERE type='orbita'").fetchone()[0]
        self.assertEqual(n, 1)
        self.assertEqual(con.execute("SELECT region FROM events WHERE type='orbita'").fetchone()[0], "Mar Negro")
        self.assertEqual(con.execute("SELECT COUNT(*) FROM events WHERE type='mudanca_rota'").fetchone()[0], 0)

    def test_fighters_do_not_trigger_route_events(self):
        con = new_db()
        lat, lon, t = 36.0, -115.0, T0
        for i in range(30):
            trk = (i * 70) % 360
            lat, lon = fly(lat, lon, trk, 400, 90)
            mw.cycle(con, [ac("ae3333", lat, lon, trk, t="F16")], t)
            t += 90
        self.assertEqual(con.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_brazil_scope(self):
        con = new_db()
        fab = ac("e48b01", -22.8, -43.2, 90, t="C390")
        raf = ac("43c123", -22.8, -43.2, 90, t="A400")
        self.assertEqual(mw.cycle(con, [fab, raf], T0), [])
        old = mw.BR_SCOPE
        try:
            mw.BR_SCOPE = "NATO"
            con2 = new_db()
            out = mw.cycle(con2, [fab, raf], T0)
            self.assertEqual(len(out), 1)
            self.assertIn("A400", out[0])
        finally:
            mw.BR_SCOPE = old

    def test_landing_learning_and_prediction(self):
        con = new_db()
        base = ("Ramstein (DE)", 49.44, 7.60)
        # dois pousos do mesmo avião em Ramstein
        t = T0
        for k in range(2):
            mw.cycle(con, [ac("ae4444", 49.5, 7.5, 120, alt=2000, gs=150)], t)
            mw.cycle(con, [ac("ae4444", 49.44, 7.60, 120, alt="ground", gs=20)], t + 90)
            t += 5 * 3600
        self.assertEqual(con.execute("SELECT COUNT(*) FROM landings").fetchone()[0], 2)
        # agora voando em direção a Ramstein, vindo do oeste
        a = mw.normalize(ac("ae4444", 49.0, 2.0, 0, alt=30000, gs=420))
        a["track"] = geo.bearing_deg(49.0, 2.0, base[1], base[2])
        preds = analysis.predict_destination(con, a, analysis.learned_bases(con))
        self.assertTrue(preds)
        self.assertEqual(preds[0]["name"], base[0])
        self.assertEqual(preds[0]["history"], 2)
        self.assertEqual(preds[0]["confidence"], "alta")
        self.assertEqual(len(analysis.projection(49.0, 2.0, 90, 420)), 3)

    def test_signal_lost_in_sensitive_region(self):
        con = new_db()
        mw.cycle(con, [ac("ae5555", 44.0, 33.0, 90, alt=30000, t="R135")], T0)
        mw.cycle(con, [], T0 + 1500)
        ev = con.execute("SELECT type, region FROM events").fetchall()
        self.assertEqual(ev, [("sinal_perdido", "Mar Negro")])
        mw.cycle(con, [], T0 + 3000)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)


class Anomaly(unittest.TestCase):
    def seed(self, con, days, spike=1.0):
        now = T0 // 3600 * 3600
        for h in range(now - (days + 1) * 86400, now, 3600):
            con.execute("INSERT INTO collected_hours VALUES (?, 50)", (h,))
            day = (now - h - 1) // 86400
            base_tankers = 4 + (day % 3)
            n = int(base_tankers * (spike if h >= now - 86400 else 1))
            con.execute("INSERT INTO region_hourly VALUES (?,?,?,?)", (h, "Oriente Médio e Golfo", "tanker", n))
            con.execute("INSERT INTO region_hourly VALUES (?,?,?,?)", (h, "Oriente Médio e Golfo", "fighter", 6))
        con.commit()
        return now

    def test_collecting_status(self):
        con = new_db()
        now = self.seed(con, 5)
        idx = analysis.anomaly_index(con, now)
        self.assertEqual(idx[0]["status"], "coletando")
        self.assertEqual(idx[0]["n_base"], 5)

    def test_normal_and_spike(self):
        con = new_db()
        now = self.seed(con, 28)
        self.assertEqual(analysis.anomaly_index(con, now)[0]["level"], "normal")
        con2 = new_db()
        now = self.seed(con2, 28, spike=3.0)
        item = analysis.anomaly_index(con2, now)[0]
        self.assertIn(item["level"], ("alto", "muito alto"))
        tank = next(c for c in item["categories"] if c["category"] == "tanker")
        self.assertGreater(tank["current"], tank["mean"] * 2)

    def test_anomaly_alert_sent_once(self):
        con = new_db()
        now = self.seed(con, 28, spike=3.0)
        out1 = mw.cycle(con, [], now + 10)
        out2 = mw.cycle(con, [], now + 3700)
        self.assertTrue(any("Atividade anômala" in m for m in out1))
        self.assertFalse(any("Atividade anômala" in m for m in out2))   # cooldown 12h

    def test_rollup(self):
        con = new_db()
        h = T0 // 3600 * 3600
        store.meta_set(con, "last_rollup_hour", h - 3600)
        mw.cycle(con, [ac("ae6666", 44.0, 33.0, 90), ac("ae7777", 44.1, 33.1, 90, t="E3TF")], h + 60)
        analysis.rollup(con, h + 3600 + 60)
        rows = con.execute("SELECT region, category, aircraft FROM region_hourly ORDER BY category").fetchall()
        self.assertEqual(rows, [("Mar Negro", "aew", 1), ("Mar Negro", "tanker", 1)])


class Storage(unittest.TestCase):
    def test_migration_from_v1(self):
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        old = sqlite3.connect(path)
        old.executescript("""
          CREATE TABLE points (hex TEXT, reg TEXT, type TEXT, callsign TEXT, lat REAL, lon REAL,
            alt REAL, gs REAL, track REAL, ts INTEGER);
          CREATE TABLE sightings (id INTEGER PRIMARY KEY AUTOINCREMENT, hex TEXT, reg TEXT, type TEXT,
            callsign TEXT, first_seen INTEGER, last_seen INTEGER, first_lat REAL, first_lon REAL,
            last_lat REAL, last_lon REAL, n_points INTEGER DEFAULT 1);
          CREATE TABLE alerts (hex TEXT, ts INTEGER);
          CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
          INSERT INTO sightings (hex, reg, type, callsign, first_seen, last_seen, first_lat, first_lon, last_lat, last_lon)
            VALUES ('ae03de','164762','C130','CNV3370',1,2,-22.7,-43.1,-22.6,-43.2),
                   ('ae68bd','12-3047','D328','',3,4,-28.8,-56.4,-28.7,-56.0);
          INSERT INTO alerts VALUES ('ae03de', 1);
        """)
        old.commit()
        old.close()
        con = mw.db_connect(path)
        rows = con.execute("SELECT hex, entered_br, category, country FROM sightings ORDER BY id").fetchall()
        self.assertEqual(rows, [("ae03de", 1, "airlift", "US"), ("ae68bd", 1, "other", "US")])
        self.assertEqual(con.execute("SELECT kind FROM alerts").fetchone()[0], "brazil")
        out = mw.cycle(con, [ac("ae03de", -22.8, -43.2, 320, t="C130", r="164762")], T0)
        self.assertIn("já visto 1x antes", out[0])
        con.close()
        store.connect(path).close()   # migração idempotente

    def test_purge_keeps_summary_track(self):
        con = new_db()
        lat, lon, t = 40.0, 10.0, T0
        for i in range(5):
            lat, lon = fly(lat, lon, 90, 300, 90)
            mw.cycle(con, [ac("ae8888", lat, lon, 90)], t)
            t += 90
        n = store.purge_old_points(con, T0 + 10 * 86400)
        self.assertEqual(n, 5)
        tj = json.loads(con.execute("SELECT track_json FROM sightings").fetchone()[0])
        self.assertEqual(len(tj), 5)

    def test_digest_text(self):
        con = new_db()
        mw.cycle(con, [ac("ae9999", 44.0, 33.0, 90), ac("43c123", 51.0, 0.0, 90, t="A332")], T0)
        txt = mw.daily_digest(con, T0 + 60)
        self.assertIn("Aeronaves militares vistas: 2 (OTAN: 2)", txt)
        self.assertIn("base histórica em formação", txt)


if __name__ == "__main__":
    unittest.main()
