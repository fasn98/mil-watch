"""Banco SQLite do mil-watch-br: esquema, migração do formato antigo e conexão."""
import json
import os
import sqlite3

import catalog
import geo

SCHEMA = """
CREATE TABLE IF NOT EXISTS points (
  hex TEXT, reg TEXT, type TEXT, callsign TEXT, lat REAL, lon REAL,
  alt REAL, gs REAL, track REAL, ts INTEGER, vrate REAL, squawk TEXT, region TEXT);
CREATE TABLE IF NOT EXISTS sightings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, hex TEXT, reg TEXT, type TEXT, callsign TEXT,
  first_seen INTEGER, last_seen INTEGER, first_lat REAL, first_lon REAL,
  last_lat REAL, last_lon REAL, n_points INTEGER DEFAULT 1,
  category TEXT, country TEXT, region_first TEXT, regions TEXT, entered_br INTEGER DEFAULT 0,
  landed_lat REAL, landed_lon REAL, track_json TEXT);
CREATE TABLE IF NOT EXISTS alerts (hex TEXT, ts INTEGER, kind TEXT DEFAULT 'brazil');
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS aircraft (
  hex TEXT PRIMARY KEY, reg TEXT, type TEXT, category TEXT, country TEXT,
  first_seen INTEGER, last_seen INTEGER);
CREATE TABLE IF NOT EXISTS state (
  hex TEXT PRIMARY KEY, sighting_id INTEGER, ts INTEGER, lat REAL, lon REAL, alt REAL,
  gs REAL, track REAL, squawk TEXT, region TEXT, in_br INTEGER DEFAULT 0,
  orbit_since INTEGER, last_orbit_check INTEGER DEFAULT 0, lost INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, hex TEXT, sighting_id INTEGER,
  type TEXT, severity TEXT, region TEXT, lat REAL, lon REAL, detail TEXT);
CREATE TABLE IF NOT EXISTS region_hourly (
  hour INTEGER, region TEXT, category TEXT, aircraft INTEGER,
  PRIMARY KEY (hour, region, category));
CREATE TABLE IF NOT EXISTS collected_hours (hour INTEGER PRIMARY KEY, aircraft INTEGER);
CREATE TABLE IF NOT EXISTS landings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, hex TEXT, callsign TEXT, lat REAL, lon REAL);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_points_hex_ts ON points(hex, ts);
CREATE INDEX IF NOT EXISTS idx_points_ts ON points(ts);
CREATE INDEX IF NOT EXISTS idx_sight_hex ON sightings(hex, last_seen);
CREATE INDEX IF NOT EXISTS idx_sight_first ON sightings(first_seen);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_hex ON events(hex, type, ts);
CREATE INDEX IF NOT EXISTS idx_landings_hex ON landings(hex);
CREATE INDEX IF NOT EXISTS idx_alerts ON alerts(hex, kind, ts);
"""

# Colunas que bancos da versão 1 (só Brasil) não têm.
NEW_COLUMNS = {
    "points": [("vrate", "REAL"), ("squawk", "TEXT"), ("region", "TEXT")],
    "sightings": [("category", "TEXT"), ("country", "TEXT"), ("region_first", "TEXT"),
                  ("regions", "TEXT"), ("entered_br", "INTEGER DEFAULT 0"),
                  ("landed_lat", "REAL"), ("landed_lon", "REAL"), ("track_json", "TEXT")],
    "alerts": [("kind", "TEXT DEFAULT 'brazil'")],
}


def _columns(con, table):
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def migrate(con):
    con.executescript(SCHEMA)
    upgraded_sightings = False
    for table, cols in NEW_COLUMNS.items():
        have = _columns(con, table)
        for name, decl in cols:
            if name not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                if table == "sightings" and name == "entered_br":
                    upgraded_sightings = True
    if upgraded_sightings:
        # Na versão 1 todo avistamento era dentro do Brasil.
        con.execute("UPDATE sightings SET entered_br=1, region_first='Brasil', regions='Brasil'")
    for sid, hx, tp in con.execute(
            "SELECT id, hex, type FROM sightings WHERE category IS NULL OR country IS NULL").fetchall():
        con.execute("UPDATE sightings SET category=?, country=? WHERE id=?",
                    (catalog.category_of(tp), geo.country_of(hx), sid))
    con.executescript(INDEXES)
    con.commit()


def connect(path, readonly=False):
    if readonly:
        con = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True, timeout=15)
        con.row_factory = sqlite3.Row
        return con
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    migrate(con)
    return con


def meta_get(con, key, default=None):
    row = con.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return row[0] if row else default


def meta_set(con, key, value):
    con.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (key, str(value)))


def downsample(points, max_points=120):
    if len(points) <= max_points:
        return points
    step = (len(points) - 1) / (max_points - 1)
    return [points[round(i * step)] for i in range(max_points)]


def purge_old_points(con, cutoff):
    """Antes de apagar pontos antigos, guarda uma rota resumida em cada avistamento."""
    rows = con.execute("""SELECT id, hex, first_seen, last_seen FROM sightings
                          WHERE last_seen < ? AND track_json IS NULL""", (cutoff,)).fetchall()
    for sid, hx, t0, t1 in rows:
        pts = con.execute("""SELECT lat, lon, alt, ts FROM points WHERE hex=? AND ts BETWEEN ? AND ?
                             ORDER BY ts""", (hx, t0, t1)).fetchall()
        con.execute("UPDATE sightings SET track_json=? WHERE id=?",
                    (json.dumps([list(p) for p in downsample(pts)]), sid))
    n = con.execute("DELETE FROM points WHERE ts < ?", (cutoff,)).rowcount
    con.execute("DELETE FROM events WHERE ts < ?", (cutoff - 335 * 86400,))  # eventos: ~1 ano
    return n
