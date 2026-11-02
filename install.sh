#!/usr/bin/env bash
# Instalador do mil-watch v2 (coleta global + painel) para VPS Linux com systemd.
# Uso:     sudo bash install.sh            (instala ou atualiza; mantém .env e histórico)
#          sudo bash install.sh --uninstall
set -euo pipefail

APP_DIR=/opt/mil-watch-br
APP_USER=milwatch
SERVICE=mil-watch-br

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERRO:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "rode como root: sudo bash install.sh"
command -v systemctl >/dev/null || die "systemd não encontrado nesta VM"

if [ "${1:-}" = "--uninstall" ]; then
  say "Removendo serviços (os dados em $APP_DIR/data são mantidos)"
  systemctl disable --now "$SERVICE" "$SERVICE-dashboard" "$SERVICE-ais" 2>/dev/null || true
  rm -f "/etc/systemd/system/$SERVICE.service" "/etc/systemd/system/$SERVICE-dashboard.service" \
        "/etc/systemd/system/$SERVICE-ais.service"
  systemctl daemon-reload
  say "Removido. Para apagar tudo: rm -rf $APP_DIR && userdel $APP_USER"
  exit 0
fi

if ! command -v python3 >/dev/null || ! python3 -c 'import sys; sys.exit(sys.version_info < (3,9))'; then
  say "Instalando Python 3"
  if command -v apt-get >/dev/null; then apt-get update -qq && apt-get install -y -qq python3
  elif command -v dnf >/dev/null; then dnf install -y -q python3
  else die "instale python3 (3.9+) manualmente e rode de novo"; fi
fi
say "Python: $(python3 --version)"
if ! python3 -c 'import websockets' 2>/dev/null; then
  say "Instalando python3-websockets (módulo AIS de Ormuz)"
  if command -v apt-get >/dev/null; then apt-get install -y -qq python3-websockets >/dev/null 2>&1 || true; fi
  python3 -c 'import websockets' 2>/dev/null || python3 -m pip install -q --break-system-packages websockets 2>/dev/null || \
    warn "não consegui instalar websockets; o módulo de Ormuz ficará desligado"
fi

id "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR/data"

# Backup do banco antes de atualizar (a migração para v2 é automática)
if [ -f "$APP_DIR/data/milwatch.db" ]; then
  systemctl stop "$SERVICE" "$SERVICE-dashboard" "$SERVICE-ais" 2>/dev/null || true
  cp "$APP_DIR/data/milwatch.db" "$APP_DIR/data/milwatch.db.bak-$(date +%Y%m%d-%H%M%S)"
  ls -1t "$APP_DIR"/data/milwatch.db.bak-* 2>/dev/null | tail -n +4 | xargs -r rm -f
  say "Backup do banco feito em $APP_DIR/data/ (mantidos os 3 mais recentes)"
fi
say "Gravando $APP_DIR/geo.py"
cat > "$APP_DIR/geo.py" <<'MILWATCH_EOF_GEO_PY'
"""Geografia: polígono do Brasil, regiões do mundo, países por código hex, bases conhecidas.

Tudo aproximado e pensado para agrupamento estatístico, não para limites legais.
"""
import math

# ------------------------------------------------------------------ Brasil
# Polígono APROXIMADO (lon, lat) do Brasil + zona econômica exclusiva.
BRAZIL_EEZ = [
    (-60.0, 5.3), (-59.8, 4.4), (-58.0, 1.3), (-56.0, 1.9), (-54.0, 2.2), (-52.5, 2.3),
    (-51.6, 4.2), (-51.0, 5.5), (-47.5, 5.5), (-42.0, 2.5), (-36.0, 0.5), (-30.5, 2.0),
    (-28.0, 1.0), (-28.0, -6.0), (-31.0, -10.0), (-34.0, -15.0), (-27.5, -18.5),
    (-27.5, -22.0), (-33.0, -23.5), (-40.0, -27.5), (-46.0, -32.0), (-50.5, -35.5),
    (-53.4, -33.75), (-57.6, -30.2), (-55.7, -28.0), (-53.8, -27.1), (-54.6, -25.6),
    (-54.3, -24.0), (-55.6, -22.6), (-57.9, -22.1), (-58.1, -20.2), (-57.5, -18.2),
    (-58.4, -16.3), (-60.2, -16.3), (-60.5, -13.8), (-62.0, -13.5), (-64.5, -12.4),
    (-65.4, -10.9), (-66.6, -9.9), (-69.6, -10.95), (-70.6, -11.0), (-70.5, -9.5),
    (-72.4, -9.4), (-73.8, -7.4), (-72.9, -5.2), (-70.0, -4.3), (-69.9, -1.1),
    (-69.4, 1.1), (-66.9, 1.2), (-64.0, 4.1), (-62.8, 4.0), (-61.0, 4.5),
]


def point_in_polygon(lon, lat, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def in_brazil(lon, lat, polygons=None):
    return any(point_in_polygon(lon, lat, p) for p in (polygons or [BRAZIL_EEZ]))


# ------------------------------------------------------------------ regiões
# (nome, (lon_min, lat_min, lon_max, lat_max)) — a primeira que contém o ponto vence.
REGIONS = [
    ("Ártico", (-180, 75, 180, 90)),
    ("Antártida", (-180, -90, 180, -60)),
    ("Caribe e América Central", (-92, 7, -58, 24)),
    ("América do Sul", (-95, -60, -30, 13)),
    ("Alasca", (-170, 50, -130, 75)),
    ("América do Norte", (-130, 13, -50, 75)),
    ("Mar Negro", (27, 40.5, 42, 47.5)),
    ("Rússia Ocidental", (32, 52.5, 60, 75)),
    ("Europa Oriental e Báltico", (14, 43, 40, 60)),
    ("Escandinávia", (4, 55, 32, 75)),
    ("Mediterrâneo", (-6, 30, 33, 43)),
    ("Europa Ocidental", (-25, 36, 14, 75)),
    ("Mar Vermelho e Chifre da África", (32, -5, 52, 16)),
    ("Oriente Médio e Golfo", (33, 16, 63, 42)),
    ("Sul da Ásia e Índico", (52, -40, 100, 35)),
    ("Mar do Sul da China e Taiwan", (100, -5, 125, 26)),
    ("Leste Asiático", (100, 26, 150, 55)),
    ("Rússia e Ásia Central", (40, 35, 180, 75)),
    ("Oceania", (110, -60, 180, -5)),
    ("África", (-20, -36, 52, 30)),
    ("Atlântico Norte", (-80, 0, 0, 75)),
    ("Atlântico Sul", (-70, -60, 20, 0)),
    ("Pacífico", (125, -60, 180, 75)),
    ("Pacífico", (-180, -60, -70, 75)),
]
REGION_NAMES = list(dict.fromkeys(n for n, _ in REGIONS)) + ["Brasil", "Outros"]
REGION_BOXES = {}
for _n, _b in REGIONS:
    REGION_BOXES.setdefault(_n, _b)
REGION_BOXES["Brasil"] = (-74, -34, -28, 6)


def region_of(lon, lat):
    if in_brazil(lon, lat):
        return "Brasil"
    for name, (x0, y0, x1, y1) in REGIONS:
        if x0 <= lon <= x1 and y0 <= lat <= y1:
            return name
    return "Outros"


# ------------------------------------------------------------------ países (faixas ICAO 24 bits)
_COUNTRIES = [
    (0xA00000, 0xAFFFFF, "US", "EUA"), (0xC00000, 0xC3FFFF, "CA", "Canadá"),
    (0x400000, 0x43FFFF, "GB", "Reino Unido"), (0x380000, 0x3BFFFF, "FR", "França"),
    (0x3C0000, 0x3FFFFF, "DE", "Alemanha"), (0x300000, 0x33FFFF, "IT", "Itália"),
    (0x340000, 0x37FFFF, "ES", "Espanha"), (0x490000, 0x497FFF, "PT", "Portugal"),
    (0x480000, 0x487FFF, "NL", "Países Baixos"), (0x448000, 0x44FFFF, "BE", "Bélgica"),
    (0x4D0000, 0x4D03FF, "LU", "Luxemburgo"), (0x458000, 0x45FFFF, "DK", "Dinamarca"),
    (0x478000, 0x47FFFF, "NO", "Noruega"), (0x4CC000, 0x4CCFFF, "IS", "Islândia"),
    (0x488000, 0x48FFFF, "PL", "Polônia"), (0x498000, 0x49FFFF, "CZ", "Tchéquia"),
    (0x505C00, 0x505FFF, "SK", "Eslováquia"), (0x470000, 0x477FFF, "HU", "Hungria"),
    (0x4A0000, 0x4A7FFF, "RO", "Romênia"), (0x450000, 0x457FFF, "BG", "Bulgária"),
    (0x468000, 0x46FFFF, "GR", "Grécia"), (0x4B8000, 0x4BFFFF, "TR", "Turquia"),
    (0x501C00, 0x501FFF, "HR", "Croácia"), (0x506C00, 0x506FFF, "SI", "Eslovênia"),
    (0x501000, 0x5013FF, "AL", "Albânia"), (0x512000, 0x5123FF, "MK", "Macedônia do Norte"),
    (0x516000, 0x5163FF, "ME", "Montenegro"), (0x511000, 0x5113FF, "EE", "Estônia"),
    (0x502C00, 0x502FFF, "LV", "Letônia"), (0x503C00, 0x503FFF, "LT", "Lituânia"),
    (0x460000, 0x467FFF, "FI", "Finlândia"), (0x4A8000, 0x4AFFFF, "SE", "Suécia"),
    (0x440000, 0x447FFF, "AT", "Áustria"), (0x4B0000, 0x4B7FFF, "CH", "Suíça"),
    (0x4CA000, 0x4CAFFF, "IE", "Irlanda"), (0x508000, 0x50FFFF, "UA", "Ucrânia"),
    (0x510000, 0x5103FF, "BY", "Belarus"), (0x100000, 0x1FFFFF, "RU", "Rússia"),
    (0x780000, 0x7BFFFF, "CN", "China"), (0x899000, 0x8993FF, "TW", "Taiwan"),
    (0x840000, 0x87FFFF, "JP", "Japão"), (0x718000, 0x71FFFF, "KR", "Coreia do Sul"),
    (0x800000, 0x83FFFF, "IN", "Índia"), (0x760000, 0x767FFF, "PK", "Paquistão"),
    (0x7C0000, 0x7FFFFF, "AU", "Austrália"), (0xC80000, 0xC87FFF, "NZ", "Nova Zelândia"),
    (0x738000, 0x73FFFF, "IL", "Israel"), (0x730000, 0x737FFF, "IR", "Irã"),
    (0x710000, 0x717FFF, "SA", "Arábia Saudita"), (0x896000, 0x896FFF, "AE", "Emirados"),
    (0x06A000, 0x06A3FF, "QA", "Catar"), (0x010000, 0x017FFF, "EG", "Egito"),
    (0x008000, 0x00FFFF, "ZA", "África do Sul"), (0x758000, 0x75FFFF, "PH", "Filipinas"),
    (0x768000, 0x76FFFF, "SG", "Singapura"), (0x8A0000, 0x8A7FFF, "ID", "Indonésia"),
    (0x683000, 0x6833FF, "KZ", "Cazaquistão"),
    (0xE40000, 0xE7FFFF, "BR", "Brasil"), (0xE00000, 0xE3FFFF, "AR", "Argentina"),
    (0xE80000, 0xE80FFF, "CL", "Chile"), (0x0AC000, 0x0ACFFF, "CO", "Colômbia"),
    (0x0D8000, 0x0DFFFF, "VE", "Venezuela"), (0x0D0000, 0x0D7FFF, "MX", "México"),
]
NATO = {"US", "CA", "GB", "FR", "DE", "IT", "ES", "PT", "NL", "BE", "LU", "DK", "NO", "IS",
        "PL", "CZ", "SK", "HU", "RO", "BG", "GR", "TR", "HR", "SI", "AL", "MK", "ME", "EE",
        "LV", "LT", "FI", "SE"}
COUNTRY_NAMES = {c: n for _, _, c, n in _COUNTRIES}


def hex_int(hexcode):
    try:
        return int(str(hexcode).strip().lstrip("~"), 16)
    except (ValueError, AttributeError, TypeError):
        return None


def country_of(hexcode):
    v = hex_int(hexcode)
    if v is None:
        return ""
    for lo, hi, code, _ in _COUNTRIES:
        if lo <= v <= hi:
            return code
    return ""


def is_us_hex(hexcode):
    v = hex_int(hexcode)
    return v is not None and 0xA00000 <= v <= 0xAFFFFF


# ------------------------------------------------------------------ bases conhecidas (aprox.)
KNOWN_BASES = [
    ("Ramstein (DE)", 49.44, 7.60), ("Spangdahlem (DE)", 49.97, 6.69),
    ("Geilenkirchen (DE)", 50.96, 6.04), ("Mildenhall (GB)", 52.36, 0.49),
    ("Lakenheath (GB)", 52.41, 0.56), ("Fairford (GB)", 51.68, -1.79),
    ("Brize Norton (GB)", 51.75, -1.58), ("Waddington (GB)", 53.17, -0.52),
    ("Rota (ES)", 36.64, -6.35), ("Morón (ES)", 37.17, -5.62),
    ("Aviano (IT)", 46.03, 12.60), ("Sigonella (IT)", 37.40, 14.92),
    ("Souda Bay (GR)", 35.53, 24.15), ("Akrotiri (CY)", 34.59, 32.99),
    ("Incirlik (TR)", 37.00, 35.43), ("Rzeszów (PL)", 50.11, 22.02),
    ("Mihail Kogălniceanu (RO)", 44.36, 28.49), ("Keflavík (IS)", 63.98, -22.61),
    ("Lajes (PT)", 38.76, -27.09), ("Pituffik/Thule (GL)", 76.53, -68.70),
    ("Al Udeid (QA)", 25.12, 51.32), ("Al Dhafra (AE)", 24.25, 54.55),
    ("Prince Sultan (SA)", 24.06, 47.58), ("Muwaffaq Salti (JO)", 31.83, 36.78),
    ("Ali Al Salem (KW)", 29.35, 47.52), ("Isa (BH)", 25.92, 50.59),
    ("Camp Lemonnier (DJ)", 11.55, 43.16), ("Diego Garcia (IO)", -7.31, 72.41),
    ("Kadena (JP)", 26.36, 127.77), ("Yokota (JP)", 35.75, 139.35),
    ("Misawa (JP)", 40.70, 141.37), ("Iwakuni (JP)", 34.14, 132.24),
    ("Osan (KR)", 37.09, 127.03), ("Kunsan (KR)", 35.90, 126.62),
    ("Andersen (GU)", 13.58, 144.93), ("Hickam (HI)", 21.32, -157.92),
    ("Elmendorf (AK)", 61.25, -149.81), ("Dover (US)", 39.13, -75.47),
    ("McGuire (US)", 40.02, -74.59), ("Charleston (US)", 32.90, -80.04),
    ("Andrews (US)", 38.81, -76.87), ("Travis (US)", 38.26, -121.93),
    ("NAS Jacksonville (US)", 30.24, -81.68), ("NAS Norfolk (US)", 36.94, -76.29),
    ("MacDill (US)", 27.85, -82.52), ("Tinker (US)", 35.41, -97.39),
    ("Barksdale (US)", 32.50, -93.66), ("Whiteman (US)", 38.73, -93.55),
    ("Minot (US)", 48.42, -101.36), ("Fairchild (US)", 47.62, -117.66),
    ("Guantánamo (CU)", 19.91, -75.21), ("Soto Cano (HN)", 14.38, -87.62),
    ("Comalapa (SV)", 13.44, -89.06), ("Roosevelt Roads (PR)", 18.25, -65.64),
    ("Barbados (BB)", 13.07, -59.49), ("Curaçao (CW)", 12.19, -68.96),
    ("Galeão (BR)", -22.81, -43.25), ("Brasília (BR)", -15.87, -47.92),
    ("Anápolis (BR)", -16.23, -48.96), ("Natal (BR)", -5.91, -35.25),
    ("Canoas (BR)", -29.95, -51.14),
]

# ------------------------------------------------------------------ cálculos geodésicos
R_KM = 6371.0


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_KM * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def angle_diff(a, b):
    """Diferença absoluta entre dois rumos, 0–180."""
    d = abs((a - b) % 360)
    return 360 - d if d > 180 else d


def signed_turn(a, b):
    """Curva de a para b, -180..180 (positivo = direita)."""
    return (b - a + 540) % 360 - 180


def project(lat, lon, track_deg, dist_km):
    """Ponto a dist_km seguindo o rumo inicial (grande círculo)."""
    d = dist_km / R_KM
    th = math.radians(track_deg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(th))
    l2 = l1 + math.atan2(math.sin(th) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180
MILWATCH_EOF_GEO_PY
say "Gravando $APP_DIR/catalog.py"
cat > "$APP_DIR/catalog.py" <<'MILWATCH_EOF_CATALOG_PY'
"""Categorias de aeronaves militares a partir do código de tipo ICAO.

Aproximado: alguns tipos têm usos múltiplos (ex.: A332 pode ser reabastecedor ou VIP;
GLF5/CL60 podem ser VIP ou inteligência). Ajuste o dicionário conforme aprender com os dados.
"""

CATEGORY_LABELS = {
    "tanker": "Reabastecedor", "aew": "Alerta aéreo/radar", "isr": "Reconhecimento/ISR",
    "maritime": "Patrulha marítima", "airlift": "Transporte pesado", "bomber": "Bombardeiro",
    "fighter": "Caça/ataque", "trainer": "Treinamento", "helo": "Helicóptero/tiltrotor",
    "vip": "Transporte executivo/VIP", "utility": "Utilitário", "other": "Outros",
}

# Peso de cada categoria no Índice de Atividade Anômala (o que costuma indicar preparação).
CATEGORY_WEIGHTS = {
    "tanker": 3.0, "aew": 3.0, "isr": 3.0, "bomber": 4.0, "airlift": 2.0, "maritime": 2.0,
    "fighter": 1.5, "vip": 1.0, "helo": 0.5, "utility": 0.5, "trainer": 0.2, "other": 0.5,
}

# Categorias "pesadas": onde mudança brusca de rota ou órbita tem significado.
HEAVY = {"tanker", "aew", "isr", "maritime", "airlift", "bomber", "vip"}
ORBIT_RELEVANT = {"tanker", "aew", "isr", "maritime", "bomber"}

_TYPES = {
    "tanker": "K35R K35E K35A KC46 K46 KC10 DC10 A332 IL78 B762 KC30",
    "aew": "E3TF E3CF E3 E767 E737 E2 E2C E2D E6 A50 KJ2K",
    "isr": "R135 C135 E8 U2 Q4 RQ4 GLHK Q9 MQ9 Q1 E11 EP3",
    "maritime": "P8 P3 P1 ATL2 CN35M",
    "airlift": "C17 C5 C5M C130 C30J A400 C27J C295 CN35 C160 IL76 Y20 Y9 Y8 AN12 AN26 C2 E390 C390 A310",
    "bomber": "B52 B1 B2 TU95 T160 TU22 H6",
    "fighter": "F16 F15 F18 F18S F35 F22 EUFI RFAL GRIF JS39 TOR F5 MIR2 SU27 SU30 SU35 SU34 MG29 J10 J11 F4 A10 AV8B E18",
    "trainer": "T38 TEX2 T6 HAWK T45 M346 PC21 PC9 PC7 TUCA L39 K8 T50 T7",
    "helo": "H60 H47 H53 H64 V22 EC35 EC45 NH90 LYNX AS32 EH10 UH1 AS65 A139 A149 H1 S61 S70",
    "vip": "B752 B737 B738 B739 GLF4 GLF5 GLF6 GLEX G280 C560 LJ35 CL60 FA7X F900 F2TH A319 A320 A321 B744 B748 E135 E145 E35L",
    "utility": "B350 BE20 PC12 DHC6 C208 BE9L DH8A DH8B BE40 C441",
}
TYPE_CATEGORY = {t: cat for cat, types in _TYPES.items() for t in types.split()}


def category_of(icao_type):
    return TYPE_CATEGORY.get((icao_type or "").strip().upper(), "other")
MILWATCH_EOF_CATALOG_PY
say "Gravando $APP_DIR/store.py"
cat > "$APP_DIR/store.py" <<'MILWATCH_EOF_STORE_PY'
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
CREATE TABLE IF NOT EXISTS ais_ships (
  mmsi TEXT PRIMARY KEY, name TEXT, type INTEGER, lat REAL, lon REAL, sog REAL, cog REAL, ts INTEGER);
CREATE TABLE IF NOT EXISTS ais_transits (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, mmsi TEXT, name TEXT, type INTEGER,
  direction TEXT, lat REAL);
CREATE TABLE IF NOT EXISTS ais_hourly (
  hour INTEGER, zone TEXT, ships INTEGER, tankers INTEGER, PRIMARY KEY (hour, zone));
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
MILWATCH_EOF_STORE_PY
say "Gravando $APP_DIR/analysis.py"
cat > "$APP_DIR/analysis.py" <<'MILWATCH_EOF_ANALYSIS_PY'
"""Análises: órbita, mudança de rota, previsão de destino, agregação horária e índice de anomalia."""
import math
import re
import statistics

import catalog
import geo

# ------------------------------------------------------------------ utilidades
def circular_mean(angles):
    if not angles:
        return None
    s = sum(math.sin(math.radians(a)) for a in angles)
    c = sum(math.cos(math.radians(a)) for a in angles)
    return (math.degrees(math.atan2(s, c)) + 360) % 360


def tracks_from_points(pts):
    """pts: lista de (ts, lat, lon, track). Usa o rumo informado ou calcula pela posição."""
    out = []
    for i, (ts, lat, lon, trk) in enumerate(pts):
        if trk is None and i > 0:
            _, la0, lo0, _ = pts[i - 1]
            trk = geo.bearing_deg(la0, lo0, lat, lon)
        out.append((ts, lat, lon, trk))
    return out


# ------------------------------------------------------------------ órbita
def detect_orbit(pts, min_points=12, min_path_km=150, max_net_ratio=0.3, min_turn_deg=450):
    """Detecta voo em órbita/hipódromo (padrão de reabastecedor, AWACS, ISR em estação).

    Retorna (lat_centro, lon_centro, raio_km) ou None.
    """
    pts = tracks_from_points(pts)
    if len(pts) < min_points:
        return None
    path = sum(geo.haversine_km(pts[i - 1][1], pts[i - 1][2], pts[i][1], pts[i][2])
               for i in range(1, len(pts)))
    if path < min_path_km:
        return None
    net = geo.haversine_km(pts[0][1], pts[0][2], pts[-1][1], pts[-1][2])
    if net > max_net_ratio * path:
        return None
    trks = [p[3] for p in pts if p[3] is not None]
    turning = sum(abs(geo.signed_turn(trks[i - 1], trks[i])) for i in range(1, len(trks)))
    if turning < min_turn_deg:
        return None
    clat = sum(p[1] for p in pts) / len(pts)
    clon = sum(p[2] for p in pts) / len(pts)
    radius = max(geo.haversine_km(clat, clon, p[1], p[2]) for p in pts)
    return clat, clon, radius


# ------------------------------------------------------------------ mudança de rota
def detect_route_change(pts, now, min_change=60, old_spread=25, new_spread=20):
    """Compara o rumo estável de 6–20 min atrás com o rumo estável dos últimos ~4 min.

    Retorna (rumo_antigo, rumo_novo) ou None.
    """
    pts = tracks_from_points(pts)
    old = [p[3] for p in pts if p[3] is not None and now - 1200 <= p[0] <= now - 360]
    new = [p[3] for p in pts if p[3] is not None and p[0] >= now - 240]
    if len(old) < 4 or len(new) < 2:
        return None
    m_old, m_new = circular_mean(old), circular_mean(new)
    if any(geo.angle_diff(t, m_old) > old_spread for t in old):
        return None
    if any(geo.angle_diff(t, m_new) > new_spread for t in new):
        return None
    if geo.angle_diff(m_old, m_new) < min_change:
        return None
    return round(m_old), round(m_new)


# ------------------------------------------------------------------ previsão
def projection(lat, lon, track, gs_kt, minutes=(15, 30, 60)):
    if None in (lat, lon, track, gs_kt) or gs_kt < 50:
        return []
    out = []
    for m in minutes:
        la, lo = geo.project(lat, lon, track, gs_kt * 1.852 * m / 60)
        out.append([round(la, 4), round(lo, 4), m])
    return out


def callsign_prefix(cs):
    m = re.match(r"[A-Z]+", (cs or "").strip().upper())
    return m.group(0) if m and len(m.group(0)) >= 2 else ""


def learned_bases(con, min_count=2, merge_km=25):
    """Bases conhecidas + pontos de pouso aprendidos com os dados (agrupados em ~25 km)."""
    bases = [{"name": n, "lat": la, "lon": lo, "landings": 0, "known": True}
             for n, la, lo in geo.KNOWN_BASES]
    clusters = {}
    for la, lo in con.execute("SELECT lat, lon FROM landings").fetchall():
        key = (round(la * 4) / 4, round(lo * 4) / 4)
        c = clusters.setdefault(key, [0, 0.0, 0.0])
        c[0] += 1
        c[1] += la
        c[2] += lo
    for n, sla, slo in clusters.values():
        la, lo = sla / n, slo / n
        near = next((b for b in bases if geo.haversine_km(la, lo, b["lat"], b["lon"]) <= merge_km), None)
        if near:
            near["landings"] += n
        elif n >= min_count:
            bases.append({"name": f"Pouso recorrente ({la:.2f}, {lo:.2f})", "lat": la, "lon": lo,
                          "landings": n, "known": False})
    return bases


def history_hits(con, hexcode, callsign, base, radius_km=25):
    hx = cs = 0
    pref = callsign_prefix(callsign)
    for h, c, la, lo in con.execute(
            "SELECT hex, callsign, lat, lon FROM landings WHERE hex=? OR callsign LIKE ?",
            (hexcode, (pref + "%") if pref else "\x00")).fetchall():
        if geo.haversine_km(la, lo, base["lat"], base["lon"]) <= radius_km:
            if h == hexcode:
                hx += 1
            elif pref and callsign_prefix(c) == pref:
                cs += 1
    return hx, cs


def predict_destination(con, a, bases, top=3):
    """Destinos prováveis: bases na direção do voo, ponderadas pelo histórico do avião/callsign."""
    lat, lon, trk, gs = a.get("lat"), a.get("lon"), a.get("track"), a.get("gs")
    alt, vrate = a.get("alt") or 0, a.get("vrate") or 0
    if None in (lat, lon, trk, gs) or gs < 80 or alt == 0:
        return []
    descending = vrate < -800 and alt < 20000
    max_km = gs * 1.852 * 8
    cands = []
    for b in bases:
        dist = geo.haversine_km(lat, lon, b["lat"], b["lon"])
        if dist < 20 or dist > max_km:
            continue
        diff = geo.angle_diff(geo.bearing_deg(lat, lon, b["lat"], b["lon"]), trk)
        limit = 60 if (descending and dist < 120) else 35
        if diff > limit:
            continue
        hx, cs = history_hits(con, a.get("hex"), a.get("callsign"), b)
        score = diff / 30 + 0.4 * dist / 1000 - 0.5 * min(hx, 4) / 4 - 0.25 * min(cs, 4) / 4
        if descending:
            score += dist / 300
        if (hx >= 2 and diff < 12) or (descending and dist < 120 and diff < 20):
            conf = "alta"
        elif hx >= 1 or (diff < 15 and dist / (gs * 1.852) <= 3):
            conf = "média"
        else:
            conf = "baixa"
        cands.append({"name": b["name"], "lat": b["lat"], "lon": b["lon"], "dist_km": round(dist),
                      "eta_min": round(dist / (gs * 1.852) * 60), "confidence": conf,
                      "history": hx, "score": score})
    cands.sort(key=lambda c: c["score"])
    for c in cands:
        c.pop("score")
    return cands[:top]


# ------------------------------------------------------------------ agregação horária
def rollup(con, now, max_hours=72):
    cur_h = now // 3600 * 3600
    last = con.execute("SELECT v FROM meta WHERE k='last_rollup_hour'").fetchone()
    h = int(last[0]) + 3600 if last else cur_h - 3600
    done = 0
    while h < cur_h and done < max_hours:
        rows = con.execute("""SELECT p.region, COALESCE(a.category, 'other'), COUNT(DISTINCT p.hex)
                              FROM points p LEFT JOIN aircraft a ON a.hex = p.hex
                              WHERE p.ts >= ? AND p.ts < ? AND p.region IS NOT NULL
                              GROUP BY 1, 2""", (h, h + 3600)).fetchall()
        con.executemany("INSERT OR REPLACE INTO region_hourly VALUES (?,?,?,?)",
                        [(h, r, c, n) for r, c, n in rows])
        total = con.execute("SELECT COUNT(DISTINCT hex) FROM points WHERE ts >= ? AND ts < ?",
                            (h, h + 3600)).fetchone()[0]
        if total:
            con.execute("INSERT OR REPLACE INTO collected_hours VALUES (?, ?)", (h, total))
        con.execute("INSERT INTO meta (k, v) VALUES ('last_rollup_hour', ?) "
                    "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(h),))
        h += 3600
        done += 1
    return done


# ------------------------------------------------------------------ índice de atividade anômala
LEVELS = [(1.5, "normal"), (2.5, "elevado"), (3.5, "alto"), (float("inf"), "muito alto")]


def anomaly_index(con, now, baseline_days=28, min_days=14, min_coverage=20, min_increase=30):
    """Atividade ponderada das últimas 24 h por região contra as mesmas janelas dos dias anteriores.

    Mede "aeronave-horas" ponderadas por categoria (reabastecedor, radar, ISR e bombardeiro
    pesam mais). Não prevê ataques: indica movimento fora do padrão histórico da região.
    """
    cur_h = now // 3600 * 3600
    start = cur_h - (baseline_days + 1) * 86400
    collected = {r[0] for r in con.execute("SELECT hour FROM collected_hours WHERE hour >= ?", (start,))}
    data = {}
    for hour, region, cat, n in con.execute(
            "SELECT hour, region, category, aircraft FROM region_hourly WHERE hour >= ?", (start,)):
        data.setdefault(region, {}).setdefault(hour, {})[cat] = n

    def coverage(end):
        return sum(1 for h in range(end - 86400, end, 3600) if h in collected)

    def window(region, end):
        tot, cats = 0.0, {}
        for h in range(end - 86400, end, 3600):
            for cat, n in data.get(region, {}).get(h, {}).items():
                w = catalog.CATEGORY_WEIGHTS.get(cat, 0.5)
                tot += w * n
                cats[cat] = cats.get(cat, 0) + n
        return tot, cats

    valid_ends = [cur_h - d * 86400 for d in range(1, baseline_days + 1)
                  if coverage(cur_h - d * 86400) >= min_coverage]
    cur_ok = coverage(cur_h) >= min_coverage
    out = []
    for region in sorted(data):
        cur, cur_cats = window(region, cur_h)
        base = [window(region, e) for e in valid_ends]
        vals = [b[0] for b in base]
        item = {"region": region, "current": round(cur, 1), "n_base": len(vals),
                "min_days": min_days, "status": "ok", "level": None, "z": None, "mean": None,
                "categories": []}
        if not cur_ok:
            item["status"] = "sem_dados"
        elif len(vals) < min_days:
            item["status"] = "coletando"
        else:
            mean = statistics.fmean(vals)
            std = statistics.pstdev(vals)
            # piso no desvio: evita que regiões com pouquíssimo tráfego disparem por um único avião
            std_eff = max(std, 0.2 * mean, 1.5 * math.sqrt(max(mean, 1.0)), 3.0)
            z = (cur - mean) / std_eff
            level = next(name for lim, name in LEVELS if z < lim)
            if cur - mean < max(min_increase, 0.3 * mean):
                level = "normal"
            item.update(mean=round(mean, 1), z=round(z, 2), level=level)
        cats = set(cur_cats) | {c for b in base for c in b[1]}
        for c in sorted(cats, key=lambda c: -catalog.CATEGORY_WEIGHTS.get(c, 0.5)):
            m = statistics.fmean([b[1].get(c, 0) for b in base]) if base else None
            item["categories"].append({"category": c, "label": catalog.CATEGORY_LABELS.get(c, c),
                                       "current": cur_cats.get(c, 0),
                                       "mean": round(m, 1) if m is not None else None})
        out.append(item)
    order = {"muito alto": 0, "alto": 1, "elevado": 2, "normal": 3, None: 4}
    out.sort(key=lambda i: (order[i["level"]], -(i["z"] or 0), -i["current"]))
    return out
MILWATCH_EOF_ANALYSIS_PY
say "Gravando $APP_DIR/mil_watch.py"
cat > "$APP_DIR/mil_watch.py" <<'MILWATCH_EOF_MIL_WATCH_PY'
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
# Regiões cujos eventos ficam só no painel, sem Telegram (ex.: treinos domésticos dos EUA)
ALERT_IGNORE_REGIONS = {r.strip() for r in env("ALERT_IGNORE_REGIONS", "América do Norte,Alasca").split(",")
                        if r.strip()}
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
    if kind not in ALERT_EVENTS or region in ALERT_IGNORE_REGIONS:
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
                                 a.type, a.reg, a.category, a.country, sg.callsign, sg.n_points
                          FROM state s LEFT JOIN aircraft a ON a.hex=s.hex
                          LEFT JOIN sightings sg ON sg.id=s.sighting_id
                          WHERE s.lost=0 AND s.ts < ? AND s.ts > ?""",
                       (now - 1200, now - 3 * 3600)).fetchall()
    for hx, sid, lat, lon, alt, gs, region, tp, reg, cat, country, cs, npts in rows:
        a = {"hex": hx, "lat": lat, "lon": lon, "alt": alt, "type": tp, "reg": reg,
             "category": cat, "country": country, "callsign": cs}
        if alt is not None and 0 < alt <= 3000 and (gs or 0) < 220:
            record_landing(con, hx, cs, lat, lon, now, sid)
        elif ((alt or 0) >= 15000 and region in SENSITIVE_REGIONS and cat in catalog.HEAVY
              and (npts or 0) >= 10 and area_has_coverage(con, hx, lat, lon, now)):
            add_event(con, "sinal_perdido", "baixa", a, now, sid, region,
                      f"Último sinal a {fmt_alt(alt)}; havia cobertura na área (outras aeronaves "
                      f"seguem sendo captadas por perto)", outbox)
        con.execute("UPDATE state SET lost=1 WHERE hex=?", (hx,))


def area_has_coverage(con, hx, lat, lon, now, radius_km=300):
    """Há receptores na área? Prova: outra aeronave foi captada perto dali nos últimos 10 min."""
    d = radius_km / 111.0
    for la, lo in con.execute("""SELECT lat, lon FROM state WHERE hex<>? AND ts>=? AND lat BETWEEN ? AND ?""",
                              (hx, now - 600, lat - d, lat + d)).fetchall():
        if geo.haversine_km(lat, lon, la, lo) <= radius_km:
            return True
    return False


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
        if "anomalia" in ALERT_EVENTS and reg not in ALERT_IGNORE_REGIONS \
                and should_alert(con, reg, now, kind="anomalia", hours=12):
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
    hz = hormuz_summary(con, now)
    if hz:
        lines.append(hz)
    return "\n".join(lines)


def hormuz_summary(con, now):
    last = store.meta_get(con, "ais_last_msg")
    if not last:
        return None
    t24 = con.execute("""SELECT COUNT(*), SUM(CASE WHEN type BETWEEN 80 AND 89 THEN 1 ELSE 0 END)
                         FROM ais_transits WHERE ts>=?""", (now - 86400,)).fetchone()
    prev = con.execute("SELECT COUNT(*) FROM ais_transits WHERE ts>=? AND ts<?",
                       (now - 8 * 86400, now - 86400)).fetchone()[0]
    first = con.execute("SELECT MIN(ts) FROM ais_transits").fetchone()[0]
    txt = f"Ormuz (AIS): {t24[0]} travessias em 24h ({t24[1] or 0} petroleiros)"
    if first and now - first >= 8 * 86400:
        txt += f"; média dos 7 dias anteriores: {prev / 7:.1f}/dia"
    if now - int(last) > 3600:
        txt += " ⚠️ dados AIS atrasados"
    return txt


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
MILWATCH_EOF_MIL_WATCH_PY
say "Gravando $APP_DIR/dashboard.py"
cat > "$APP_DIR/dashboard.py" <<'MILWATCH_EOF_DASHBOARD_PY'
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


def api_hormuz(q):
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(os.getenv("TIMEZONE", "America/Sao_Paulo"))
    except Exception:
        tz = None
    days = max(1, min(int(q.get("days", 30)), 120))
    now = int(time.time())
    with db() as con:
        last = con.execute("SELECT v FROM meta WHERE k='ais_last_msg'").fetchone()
        tr = rows(con, "SELECT ts, mmsi, name, type, direction FROM ais_transits WHERE ts>=? ORDER BY ts DESC",
                  (now - days * 86400,))
        hourly = con.execute("SELECT hour, zone, ships, tankers FROM ais_hourly WHERE hour>=?",
                             (now - days * 86400,)).fetchall()
        ships = rows(con, "SELECT mmsi, name, type, lat, lon, sog, cog, ts FROM ais_ships WHERE ts>=?",
                     (now - 1800,))
    day = lambda ts: datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")
    daily = {}
    for t in tr:
        d = daily.setdefault(day(t["ts"]), {"entrada": 0, "saida": 0, "tankers": 0, "golfo_max": 0})
        d[t["direction"]] += 1
        if t["type"] is not None and 80 <= t["type"] <= 89:
            d["tankers"] += 1
    for h, zone, n, _ in hourly:
        if zone == "golfo":
            d = daily.setdefault(day(h), {"entrada": 0, "saida": 0, "tankers": 0, "golfo_max": 0})
            d["golfo_max"] = max(d["golfo_max"], n)
    last24 = [t for t in tr if t["ts"] >= now - 86400]
    prev7 = [t for t in tr if now - 8 * 86400 <= t["ts"] < now - 86400]
    return {"configured": bool(os.getenv("AISSTREAM_API_KEY", "").strip()),
            "last_msg": int(last[0]) if last else None,
            "t24": len(last24), "t24_tankers": sum(1 for t in last24 if t["type"] and 80 <= t["type"] <= 89),
            "avg7": round(len(prev7) / 7, 1) if prev7 else None,
            "daily": [{"day": k, **v} for k, v in sorted(daily.items(), reverse=True)],
            "recent": tr[:40], "ships": ships}


ROUTES = {"/api/hormuz": api_hormuz, "/api/meta": api_meta, "/api/live": api_live, "/api/aircraft": api_aircraft,
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
.leaflet-container{background:#0b0f13}.darktiles{filter:invert(1) hue-rotate(180deg) brightness(.85) contrast(.9) saturate(.6)}.arr{background:none;border:none}
</style></head><body>
<header><h1>mil-watch</h1>
<nav id="tabs"><button data-t="live" class="on">Agora</button><button data-t="regions">Regiões</button>
<button data-t="events">Eventos</button><button data-t="history">Histórico</button><button data-t="hormuz">Ormuz</button></nav>
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
 L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:12,className:"darktiles",attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'}).addTo(map);
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

// ---------------- Ormuz (AIS)
const SC=t=>t==null?"desconhecido":t===35?"militar":t>=80&&t<=89?"petroleiro":t>=70&&t<=79?"carga":t>=60&&t<=69?"passageiros":"outro";
const SCC={petroleiro:"#ffb547",carga:"#4fb3ff",passageiros:"#9be564",militar:"#ff5a5a",outro:"#8b96a3",desconhecido:"#56606b"};
async function loadHormuz(){
 $("mapTitle").textContent="Navios na região do Estreito de Ormuz (AIS, últimos 30 min)";selHex=null;
 const d=await j("api/hormuz?days=30");
 if(map){layer.clearLayers();overlay.clearLayers();
  d.ships.forEach(s=>L.circleMarker([s.lat,s.lon],{radius:4,color:SCC[SC(s.type)],weight:1,fillOpacity:.8})
   .bindTooltip(`${esc(s.name||s.mmsi)} · ${SC(s.type)} · ${s.sog??"–"} nós`).addTo(layer));
  L.polyline([[25.9,56.0],[25.9,56.45],[27.1,56.45]],{color:"#ffffff",weight:1,dashArray:"4 4",opacity:.6}).bindTooltip("Linha de travessia do estreito").addTo(layer);
  if(fitNext){fitNext=false;map.fitBounds([[24.0,53.5],[27.8,59.0]])}}
 const ago=d.last_msg?Math.round((Date.now()/1000-d.last_msg)/60):null;
 let status=!d.configured?'<p class="note" style="color:var(--warn)">Módulo desligado: falta AISSTREAM_API_KEY no .env da VM (chave grátis em aisstream.io).</p>':
  ago==null?'<p class="note">Aguardando os primeiros dados AIS…</p>':ago>15?`<p class="note" style="color:var(--warn)">Último dado AIS há ${ago} min: conexão ou cobertura com problema.</p>`:`<p class="note">Último dado AIS há ${ago} min.</p>`;
 $("sideTitle").textContent="Tráfego no estreito";
 $("side").innerHTML=`<div class="pad"><dl><dt>Travessias 24 h</dt><dd><b>${d.t24}</b></dd><dt>Petroleiros 24 h</dt><dd>${d.t24_tankers}</dd>
 <dt>Média diária (7 dias antes)</dt><dd>${d.avg7??"–"}</dd><dt>Navios no mapa agora</dt><dd>${d.ships.length}</dd></dl>${status}
 <p class="note">Travessia = navio que cruzou a linha do estreito (entrada ou saída do Golfo Pérsico). Conta só quem transmite AIS e é captado por receptores em terra; em zona de conflito muitos navios desligam o AIS, e navios de guerra quase nunca aparecem. Use como tendência, não como número absoluto.</p></div>`;
 $("tblTitle").textContent="Travessias por dia (horário de Brasília)";
 const mx=Math.max(1,...d.daily.map(x=>x.entrada+x.saida));
 table(["Dia","Entradas","Saídas","Petroleiros","Navios no Golfo (pico/hora)",""],d.daily.map(x=>`<tr><td>${x.day.split("-").reverse().join("/")}</td><td>${x.entrada}</td><td>${x.saida}</td><td>${x.tankers}</td><td>${x.golfo_max||"–"}</td><td style="width:35%"><div class="bar" style="margin:0"><i style="width:${Math.round((x.entrada+x.saida)/mx*100)}%"></i></div></td></tr>`),
  d.configured?"Ainda sem travessias registradas.":"Módulo AIS desligado.");
}

const LOAD={live:loadLive,regions:loadRegions,events:loadEvents,history:loadHistory,hormuz:loadHormuz};
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
MILWATCH_EOF_DASHBOARD_PY
say "Gravando $APP_DIR/ais_watch.py"
cat > "$APP_DIR/ais_watch.py" <<'MILWATCH_EOF_AIS_WATCH_PY'
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
MILWATCH_EOF_AIS_WATCH_PY
python3 -m py_compile "$APP_DIR"/*.py || die "falha ao validar o programa"
rm -rf "$APP_DIR/__pycache__"

# ---------------------------------------------------------------- configuração
if [ ! -f "$APP_DIR/.env" ]; then
  TOKEN="${TELEGRAM_TOKEN:-}"; CHAT="${TELEGRAM_CHAT_ID:-}"
  if [ -z "$TOKEN" ] && [ -r /dev/tty ]; then read -r -p "Token do bot do Telegram (Enter para pular): " TOKEN < /dev/tty || true; fi
  if [ -n "$TOKEN" ] && [ -z "$CHAT" ] && [ -r /dev/tty ]; then read -r -p "Chat ID do Telegram: " CHAT < /dev/tty || true; fi
  [ -n "$TOKEN" ] || warn "Sem Telegram: alertas só no log. Edite $APP_DIR/.env depois."
  printf 'TELEGRAM_TOKEN=%s\nTELEGRAM_CHAT_ID=%s\n' "$TOKEN" "$CHAT" > "$APP_DIR/.env"
else
  say "Mantendo configuração existente em $APP_DIR/.env"
fi
NEW_PASS=""
add_default() {  # chave, valor, comentário
  if ! grep -q "^$1=" "$APP_DIR/.env"; then
    [ -n "${3:-}" ] && printf '\n# %s\n' "$3" >> "$APP_DIR/.env"
    printf '%s=%s\n' "$1" "$2" >> "$APP_DIR/.env"
  fi
}
add_default INTERVAL_SECONDS 90 "Coleta"
add_default API_SOURCES "https://api.adsb.lol/v2/mil,https://api.airplanes.live/v2/mil"
add_default DB_PATH data/milwatch.db
add_default LOG_PATH data/milwatch.log
add_default SESSION_GAP_MINUTES 30
add_default RAW_RETENTION_DAYS 30 "Dias de pontos brutos guardados (rotas resumidas e estatísticas ficam para sempre)"
add_default BRAZIL_ALERT_SCOPE US "Alerta de entrada no Brasil: US | NATO | FOREIGN (todos menos BR) | ALL"
add_default ALERT_COOLDOWN_HOURS 6
add_default ALERT_EVENTS emergencia,anomalia "Eventos que vão para o Telegram: emergencia,anomalia,orbita,mudanca_rota,descida_rapida,sinal_perdido"
add_default ALERT_EVENT_REGIONS "" "Limita alertas de evento (exceto emergência) a estas regiões; vazio = todas"
add_default ALERT_IGNORE_REGIONS "América do Norte,Alasca" "Regiões cujos eventos e anomalias ficam só no painel (sem Telegram)"
add_default ANOMALY_ALERT_Z 3.0 "Índice de Atividade Anômala"
add_default ANOMALY_MIN_DAYS 14
add_default ANOMALY_BASELINE_DAYS 28
add_default AISSTREAM_API_KEY "" "Tráfego de navios em Ormuz: chave grátis em https://aisstream.io (vazio = módulo desligado)"
add_default DIGEST_HOUR 8 "Resumo diário no Telegram (hora local; -1 desliga)"
add_default TIMEZONE America/Sao_Paulo
add_default REPORT_EVERY_DAYS 7
if ! grep -q '^DASHBOARD_PASSWORD=' "$APP_DIR/.env"; then
  NEW_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
fi
add_default DASHBOARD_PORT "${DASHBOARD_PORT:-8088}" "Painel web"
add_default DASHBOARD_BIND 0.0.0.0
add_default DASHBOARD_USER admin
[ -n "$NEW_PASS" ] && add_default DASHBOARD_PASSWORD "$NEW_PASS"
DASH_PORT="$(grep '^DASHBOARD_PORT=' "$APP_DIR/.env" | tail -1 | cut -d= -f2)"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"

if python3 -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('https://api.adsb.lol/v2/mil', headers={'User-Agent':'mil-watch-br/2.0'}), timeout=20)" 2>/dev/null; then
  say "API adsb.lol acessível"
else
  warn "Não consegui acessar api.adsb.lol agora (o serviço tenta a fonte reserva e repete sozinho)"
fi

# ---------------------------------------------------------------- serviços
unit() {  # nome, descrição, script, segundos, [política de restart]
cat > "/etc/systemd/system/$1.service" <<EOF
[Unit]
Description=$2
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$(command -v python3) -u $APP_DIR/$3
Restart=${5:-always}
RestartSec=$4
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$APP_DIR/data

[Install]
WantedBy=multi-user.target
EOF
}
say "Criando serviços"
unit "$SERVICE" "mil-watch - coletor global de aeronaves militares" mil_watch.py 30
unit "$SERVICE-dashboard" "mil-watch - painel web" dashboard.py 10
unit "$SERVICE-ais" "mil-watch - tráfego de navios em Ormuz (AIS)" ais_watch.py 60 on-failure
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow "$DASH_PORT/tcp" >/dev/null && say "Porta $DASH_PORT liberada no ufw"
fi
systemctl daemon-reload
systemctl enable "$SERVICE" "$SERVICE-dashboard" "$SERVICE-ais" >/dev/null 2>&1
systemctl restart "$SERVICE"
sleep 4
systemctl restart "$SERVICE-dashboard" "$SERVICE-ais"
sleep 3

systemctl is-active --quiet "$SERVICE" || { journalctl -u "$SERVICE" -n 40 --no-pager; die "o coletor não subiu; veja o log acima"; }
say "Coletor rodando. Últimas linhas do log:"
journalctl -u "$SERVICE" -n 4 --no-pager -o cat || true
IP_PUB="$(hostname -I 2>/dev/null | awk '{print $1}')"
if systemctl is-active --quiet "$SERVICE-dashboard"; then
  say "Painel: http://${IP_PUB:-IP_DA_VM}:$DASH_PORT   (usuário: admin)"
  if [ -n "$NEW_PASS" ]; then say "Senha do painel (anote!): $NEW_PASS"
  else say "Senha do painel: a que está em $APP_DIR/.env (DASHBOARD_PASSWORD)"; fi
else
  journalctl -u "$SERVICE-dashboard" -n 20 --no-pager || true
  warn "o painel não subiu (porta $DASH_PORT ocupada? mude DASHBOARD_PORT no .env e rode: systemctl restart $SERVICE-dashboard)"
fi
cat <<EOF

Pronto. Deve chegar no Telegram: "🟢 mil-watch-br v2 iniciado: monitoramento global ativo".
  Coletor ao vivo:  journalctl -u $SERVICE -f
  Painel ao vivo:   journalctl -u $SERVICE-dashboard -f
  Ormuz (AIS):      journalctl -u $SERVICE-ais -f   (precisa de AISSTREAM_API_KEY no .env)
  Configuração:     nano $APP_DIR/.env   (depois: systemctl restart $SERVICE $SERVICE-dashboard)
  Banco de dados:   $APP_DIR/data/milwatch.db  (backup anterior ao lado)
EOF
