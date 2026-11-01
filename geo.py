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
