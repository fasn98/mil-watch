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
