import os
import tempfile
import unittest

os.environ["TELEGRAM_TOKEN"] = ""
import mil_watch as mw


class GeoTests(unittest.TestCase):
    def test_inside(self):
        for name, lon, lat in [("Rio", -43.2, -22.9), ("Valença (rastro 27/10)", -43.688, -22.32),
                               ("Brasília", -47.9, -15.8), ("Manaus", -60.0, -3.1),
                               ("Belém", -48.5, -1.45), ("Porto Alegre", -51.2, -30.0),
                               ("Noronha", -32.4, -3.85), ("Mar ao largo de Santos", -45.0, -25.5)]:
            self.assertTrue(mw.in_brazil(lon, lat), name)

    def test_outside(self):
        for name, lon, lat in [("Barbados", -59.49, 13.07), ("Buenos Aires", -58.4, -34.6),
                               ("Assunção", -57.6, -25.3), ("Lima", -77.0, -12.0),
                               ("Caracas", -66.9, 10.5), ("Montevidéu", -56.2, -34.9),
                               ("Santa Cruz (BO)", -63.2, -17.8), ("Atlântico central", -15.0, -10.0)]:
            self.assertFalse(mw.in_brazil(lon, lat), name)

    def test_us_hex(self):
        self.assertTrue(mw.is_us_hex("ae03de"))
        self.assertTrue(mw.is_us_hex("~AE03DE"))
        self.assertFalse(mw.is_us_hex("e48b01"))  # Brasil
        self.assertFalse(mw.is_us_hex("43c123"))  # Reino Unido
        self.assertFalse(mw.is_us_hex(None))


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.con = mw.db_connect(os.path.join(self.tmp, "t.db"))
        self.herc = {"hex": "ae03de", "r": "164762", "t": "C130", "flight": "CNV3370 ",
                     "lat": -22.32, "lon": -43.688, "alt_baro": 12750, "gs": 309, "track": 154.1}
        self.fab = {"hex": "e48b01", "t": "C390", "lat": -22.8, "lon": -43.2, "alt_baro": 5000}
        self.over_barbados = dict(self.herc, lat=13.07, lon=-59.49)

    def test_filters(self):
        hits = mw.filter_targets([self.herc, self.fab, self.over_barbados])
        self.assertEqual([h["hex"] for h in hits], ["ae03de"])

    def test_dedup_and_history(self):
        t0 = 1_800_000_000
        self.assertEqual(len(mw.cycle(self.con, [self.herc], t0)), 1)          # alerta
        self.assertEqual(len(mw.cycle(self.con, [self.herc], t0 + 90)), 0)     # mesma sessão
        self.assertEqual(len(mw.cycle(self.con, [self.herc], t0 + 3600)), 0)   # nova sessão, em cooldown
        alerts = mw.cycle(self.con, [self.herc], t0 + 7 * 3600)                # nova sessão, fora do cooldown
        self.assertEqual(len(alerts), 1)
        self.assertIn("já visto 2x antes", alerts[0])
        n = self.con.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
        self.assertEqual(n, 3)

    def test_alert_text(self):
        msg = mw.cycle(self.con, [self.herc], 1_800_000_000)[0]
        self.assertIn("164762", msg)
        self.assertIn("icao=ae03de", msg)
        self.assertIn("primeira vez", msg)

    def test_report(self):
        mw.cycle(self.con, [self.herc], 1_800_000_000)
        rep = mw.weekly_report(self.con, 1_800_000_100)
        self.assertIn("1 avistamentos", rep)


if __name__ == "__main__":
    unittest.main()
