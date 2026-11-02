import asyncio
import json
import os
import tempfile
import threading
import unittest

os.environ["TELEGRAM_TOKEN"] = ""
import ais_watch as aw
import store

T0 = 1_800_000_000


def pos(mmsi, lat, lon, kind="PositionReport"):
    return {"MessageType": kind, "MetaData": {"MMSI": int(mmsi), "ShipName": f"SHIP {mmsi}"},
            "Message": {kind: {"UserID": int(mmsi), "Latitude": lat, "Longitude": lon, "Sog": 12.0, "Cog": 120}}}


def static(mmsi, typ, name):
    return {"MessageType": "ShipStaticData", "MetaData": {"MMSI": int(mmsi)},
            "Message": {"ShipStaticData": {"UserID": int(mmsi), "Type": typ, "Name": name}}}


def new_db():
    return store.connect(os.path.join(tempfile.mkdtemp(), "t.db"))


class TrackerTests(unittest.TestCase):
    def test_outbound_and_inbound_transits(self):
        t = aw.Tracker()
        t.on_message(static("111111111", 84, "OIL QUEEN"), T0)
        self.assertIsNone(t.on_message(pos("111111111", 26.55, 56.20), T0))          # dentro do Golfo
        tr = t.on_message(pos("111111111", 26.40, 56.60), T0 + 1800)                  # saiu
        self.assertEqual((tr["direction"], tr["name"], aw.ship_class(tr["type"])), ("saida", "OIL QUEEN", "tanker"))
        tr2 = t.on_message(pos("111111111", 26.60, 56.30), T0 + 5400)                 # voltou
        self.assertEqual(tr2["direction"], "entrada")

    def test_fujairah_anchorage_is_not_transit(self):
        t = aw.Tracker()
        t.on_message(pos("222222222", 25.10, 56.30), T0)
        self.assertIsNone(t.on_message(pos("222222222", 25.15, 56.55), T0 + 600))

    def test_long_gap_is_not_transit(self):
        t = aw.Tracker()
        t.on_message(pos("333333333", 26.55, 56.20), T0)
        self.assertIsNone(t.on_message(pos("333333333", 26.40, 56.60), T0 + 5 * 3600))

    def test_flush_and_hourly(self):
        con = new_db()
        t = aw.Tracker()
        t.on_message(static("111111111", 84, "OIL QUEEN"), T0)
        t.on_message(pos("111111111", 26.55, 56.20), T0)
        t.on_message(pos("444444444", 25.20, 55.30), T0 + 60)                        # Dubai
        t.on_message(pos("111111111", 26.40, 56.60), T0 + 1800)
        t.on_message(pos("444444444", 25.21, 55.31), T0 + 3700)                      # vira a hora
        self.assertEqual(t.flush(con, T0 + 3700), 1)
        self.assertEqual(con.execute("SELECT direction, name FROM ais_transits").fetchall(), [("saida", "OIL QUEEN")])
        rows = dict((z, (s, k)) for _, z, s, k in con.execute("SELECT * FROM ais_hourly"))
        self.assertEqual(rows["golfo"], (2, 1))
        self.assertEqual(rows["oma"], (1, 1))
        self.assertEqual(con.execute("SELECT COUNT(*) FROM ais_ships").fetchone()[0], 1)  # só os recentes

    def test_bad_messages_ignored(self):
        t = aw.Tracker()
        self.assertIsNone(t.on_message({"MessageType": "PositionReport", "Message": {}}))
        self.assertIsNone(t.on_message(pos("555555555", 91.0, 56.0)))


class WebsocketIntegration(unittest.TestCase):
    """Servidor websocket local imitando o aisstream: confere assinatura e fluxo completo."""

    def test_end_to_end(self):
        import websockets
        received = {}
        msgs = [static("111111111", 84, "OIL QUEEN"), pos("111111111", 26.55, 56.20),
                pos("111111111", 26.40, 56.60)]

        async def handler(ws, path=None):
            received["sub"] = json.loads(await ws.recv())
            for m in msgs:
                await ws.send(json.dumps(m))
            await ws.close()

        loop = asyncio.new_event_loop()
        ready = threading.Event()
        holder = {}

        def serve():
            asyncio.set_event_loop(loop)
            holder["srv"] = loop.run_until_complete(websockets.serve(handler, "127.0.0.1", 0))
            holder["port"] = holder["srv"].sockets[0].getsockname()[1]
            ready.set()
            loop.run_forever()

        th = threading.Thread(target=serve, daemon=True)
        th.start()
        ready.wait(5)
        aw.URL = f"ws://127.0.0.1:{holder['port']}"
        aw.API_KEY = "test-key"
        con = new_db()
        tracker = aw.Tracker()

        async def client():
            try:
                await asyncio.wait_for(aw.run(tracker, con), timeout=3)
            except asyncio.TimeoutError:
                pass
        asyncio.run(client())
        loop.call_soon_threadsafe(loop.stop)
        self.assertEqual(received["sub"]["APIKey"], "test-key")
        self.assertEqual(received["sub"]["BoundingBoxes"], [[[22.0, 47.5], [30.6, 60.5]]])
        self.assertEqual(con.execute("SELECT direction, name FROM ais_transits").fetchall(), [("saida", "OIL QUEEN")])

    def test_backoff_grows_when_server_drops_without_reply(self):
        """Chave inválida: o aisstream aceita a conexão e derruba sem mandar nada.
        O backoff tem que crescer (5, 10, 20...) em vez de voltar a 5 a cada conexão."""
        import websockets
        from unittest import mock

        async def handler(ws, path=None):
            await ws.recv()
            ws.transport.abort()  # cai sem close frame, como o aisstream faz

        loop = asyncio.new_event_loop()
        ready = threading.Event()
        holder = {}

        def serve():
            asyncio.set_event_loop(loop)
            holder["srv"] = loop.run_until_complete(websockets.serve(handler, "127.0.0.1", 0))
            holder["port"] = holder["srv"].sockets[0].getsockname()[1]
            ready.set()
            loop.run_forever()

        threading.Thread(target=serve, daemon=True).start()
        ready.wait(5)
        aw.URL = f"ws://127.0.0.1:{holder['port']}"
        aw.API_KEY = "bad-key"
        delays = []
        real_sleep = asyncio.sleep

        class Stop(Exception):
            pass

        async def fake_sleep(d, *a, **k):
            import sys
            if not sys._getframe(1).f_code.co_filename.endswith("ais_watch.py"):
                return await real_sleep(d, *a, **k)  # sleeps internos do websockets
            delays.append(d)
            if len(delays) >= 4:
                raise Stop
            await real_sleep(0)

        async def client():
            with mock.patch.object(aw.asyncio, "sleep", fake_sleep):
                await aw.run(aw.Tracker(), new_db())
        with self.assertRaises(Stop):
            asyncio.run(asyncio.wait_for(client(), timeout=10))
        loop.call_soon_threadsafe(loop.stop)
        self.assertEqual(delays, [5, 10, 20, 40])


if __name__ == "__main__":
    unittest.main()


class SummaryAndApi(unittest.TestCase):
    def test_digest_line_and_api(self):
        import importlib
        import mil_watch as mw
        con = new_db()
        t = aw.Tracker()
        t.on_message(static("111111111", 84, "OIL QUEEN"), T0)
        t.on_message(pos("111111111", 26.55, 56.20), T0)
        t.on_message(pos("111111111", 26.40, 56.60), T0 + 1800)
        t.flush(con, T0 + 1800)
        self.assertIn("Ormuz (AIS): 1 travessias em 24h (1 petroleiros)", mw.hormuz_summary(con, T0 + 1900))
        empty = new_db()
        self.assertIsNone(mw.hormuz_summary(empty, T0))
