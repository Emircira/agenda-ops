"""
Seçim Radarı — Gelecek simülasyonu: yalnızca veritabanı + TÜİK JSON dosyasındaki sayısal alanlar.
Tahmini veya uydurma il verisi kullanılmaz; ulusal ortalamalar aynı JSON dosyasından hesaplanır.

UI simülatörü (a,b,c,d) parti adında anahtar kelime ile bloklara toplanır (iktidar / muhalefet /
tepki-sol / diğer); blok harf sırası oy hacmine göre yeniden etiketlenmez.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any, Optional


def top_four_letters_from_party_shares(shares: dict[str, float]) -> dict[str, float]:
    iktidar_keywords = ["AK PART", "ADALET VE KALKINMA", "MHP", "MİLLİYETÇİ", "YENİDEN REFAH", "YRP", "BBP", "HÜDA"]
    muhalefet_keywords = ["CHP", "CUMHURİYET HALK", "İYİ PART", "MEMLEKET", "ZAFER"]
    tepki_sol_keywords = ["DEM", "HDP", "YEŞİL SOL", "TİP", "SAADET", "DEVA", "GELECEK"]

    blocks = {"a": 0.0, "b": 0.0, "c": 0.0, "d": 0.0}

    for party, pct in shares.items():
        p_upper = str(party).upper()
        assigned = False

        # Check İktidar (a)
        for k in iktidar_keywords:
            if k in p_upper:
                blocks["a"] += float(pct)
                assigned = True
                break
        if assigned:
            continue

        # Check Muhalefet (b)
        for k in muhalefet_keywords:
            if k in p_upper:
                blocks["b"] += float(pct)
                assigned = True
                break
        if assigned:
            continue

        # Check Tepki/Sol (c)
        for k in tepki_sol_keywords:
            if k in p_upper:
                blocks["c"] += float(pct)
                assigned = True
                break
        if assigned:
            continue

        # Others (d)
        blocks["d"] += float(pct)

    # Normalizasyon
    total = sum(blocks.values())
    if total > 0:
        blocks = {k: round((v / total) * 100.0, 2) for k, v in blocks.items()}
    else:
        blocks = {"a": 0.0, "b": 0.0, "c": 0.0, "d": 0.0}

    return blocks


def bloc_shares_pct_from_party_shares(party_to_share_pct: dict[str, float]) -> dict[str, float]:
    """Parti yüzdelerini blok (a,b,c,d) yüzdelerine döndürür; `top_four_letters_from_party_shares` kullanır."""
    return top_four_letters_from_party_shares(party_to_share_pct or {})


def base_votes_blocs_from_election_rows(election_rows: list[Any]) -> dict[str, float]:
    """Ham seçim satırlarından sabit bloklara oy yüzdeleri."""
    totals: dict[str, int] = {}
    for row in election_rows:
        party = str(getattr(row, "party", None) or "").strip()
        try:
            v = int(getattr(row, "vote_count", None) or 0)
        except (TypeError, ValueError):
            v = 0
        if not party and v <= 0:
            continue
        totals[party] = totals.get(party, 0) + v
    tv = sum(totals.values()) or 0
    if tv <= 0:
        return {"a": 0.0, "b": 0.0, "c": 0.0, "d": 0.0}
    party_pct = {party: round(100.0 * votes / tv, 6) for party, votes in totals.items()}
    return bloc_shares_pct_from_party_shares(party_pct)


def app_data_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data"


def load_city_stats_list() -> list[dict]:
    p = app_data_dir() / "city_stats.json"
    if not p.is_file():
        return []
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return [x for x in data if isinstance(x, dict)]
    except Exception:
        return []


def national_means_from_city_stats(cities: list[dict]) -> dict[str, float]:
    if not cities:
        return {}
    keys = ["unemployment_rate", "university_grad_pct", "growth_rate", "foreign_pop_pct"]
    out: dict[str, float] = {}
    n = len(cities)
    for k in keys:
        vals = [float(c.get(k) or 0) for c in cities]
        out[k] = sum(vals) / n if n else 0.0
    return out


def find_city_row_from_list(cities: list[dict], province_norm_fn, province: str) -> Optional[dict]:
    target = province_norm_fn(province)
    for c in cities:
        if province_norm_fn(c.get("province", "")) == target:
            return c
    return None


def historical_shares_by_year_from_election_rows(
    rows: list[Any],
    province_norm_fn,
    province: str,
    district_key: str,
    election_type_value: Any,
) -> dict[int, dict[str, float]]:
    """election_results: yıl -> parti -> oy %. İl geneli: önce district'siz satırlar, yoksa ilçeler toplanır."""
    from collections import defaultdict

    dist_key = (district_key or "").strip()
    pn = province_norm_fn(province)

    pool: list[Any] = []
    for r in rows:
        if getattr(r, "election_type", None) != election_type_value:
            continue
        if province_norm_fn(getattr(r, "province", "") or "") != pn:
            continue
        pool.append(r)

    if not pool:
        return {}

    if dist_key:
        use = [
            r
            for r in pool
            if getattr(r, "district", None)
            and province_norm_fn(str(getattr(r, "district", ""))) == province_norm_fn(dist_key)
        ]
        if not use:
            use = [
                r
                for r in pool
                if not (getattr(r, "district", None) and str(getattr(r, "district", "")).strip())
            ]
        pool = use
    else:
        il_only = [
            r
            for r in pool
            if not (getattr(r, "district", None) and str(getattr(r, "district", "")).strip())
        ]
        if il_only:
            pool = il_only

    by_year: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in pool:
        y = int(getattr(r, "election_year", 0))
        p = (getattr(r, "party", None) or "").strip()
        if not y or not p:
            continue
        by_year[y][p] += int(getattr(r, "vote_count", 0) or 0)

    out: dict[int, dict[str, float]] = {}
    for y, pv in sorted(by_year.items()):
        tot = sum(pv.values())
        if tot <= 0:
            continue
        out[y] = {party: round(100.0 * votes / tot, 4) for party, votes in pv.items()}
    return out


def party_share_volatility_pp(shares_by_year: dict[int, dict[str, float]]) -> dict[str, float]:
    years = sorted(shares_by_year.keys())
    parties: set[str] = set()
    for sh in shares_by_year.values():
        parties |= set(sh.keys())
    out: dict[str, float] = {}
    for party in parties:
        series = [float(shares_by_year[y].get(party, 0.0)) for y in years]
        if len(series) < 2:
            out[party] = 0.0
        else:
            out[party] = statistics.pstdev(series)
    return out


def linear_extrapolate_shares(shares_by_year: dict[int, dict[str, float]]) -> dict[str, float]:
    """Son iki seçim yılını doğrusal bağlayıp bir sonraki adımı öngörür (oranlar normalize)."""
    years = sorted(shares_by_year.keys())
    if not years:
        return {}
    if len(years) == 1:
        y = years[0]
        return dict(shares_by_year[y])
    y0, y1 = years[-2], years[-1]
    dt = y1 - y0
    if dt == 0:
        return dict(shares_by_year[y1])
    parties = set(shares_by_year[y0]) | set(shares_by_year[y1])
    raw: dict[str, float] = {}
    for p in parties:
        s0 = float(shares_by_year[y0].get(p, 0.0))
        s1 = float(shares_by_year[y1].get(p, 0.0))
        slope = (s1 - s0) / dt
        step = y1 - y0
        pred = s1 + slope * step
        raw[p] = max(0.0, pred)
    tot = sum(raw.values()) or 1.0
    return {p: round(100.0 * v / tot, 4) for p, v in raw.items()}


def apply_city_stats_with_volatility_weights(
    extrapolated: dict[str, float],
    city_row: dict,
    national: dict[str, float],
    volatility_pp: dict[str, float],
) -> dict[str, float]:
    """
    city_stats.json satırı ve ulusal ortalamaları (aynı dosyadan) kullanarak tek skaler çarpan üretir;
    çarpanın etkisi partiler arası dağıtılırken yalnızca oy payı geçişkenliği (veriden) ağırlık olarak kullanılır.
    """
    u_n = max(float(national.get("unemployment_rate") or 0), 1e-6)
    e_n = max(float(national.get("university_grad_pct") or 0), 1e-6)
    f_n = max(float(national.get("foreign_pop_pct") or 0), 0.1)
    g = float(city_row.get("growth_rate") or 0) / 100.0

    u = float(city_row.get("unemployment_rate") or 0)
    e = float(city_row.get("university_grad_pct") or 0)
    f = float(city_row.get("foreign_pop_pct") or 0)

    city_scalar = ((u / u_n) * (e / e_n) * ((f + 0.01) / (f_n + 0.01)) * (1.0 + g)) ** (1.0 / 4.0)

    max_vol = max(volatility_pp.values()) if volatility_pp else 1.0
    if max_vol <= 0:
        max_vol = 1.0

    out: dict[str, float] = {}
    for p, sh in extrapolated.items():
        w = float(volatility_pp.get(p, 0.0)) / max_vol
        factor = 1.0 + (city_scalar - 1.0) * w
        out[p] = max(0.0, float(sh) * factor)
    tot = sum(out.values()) or 1.0
    return {p: round(100.0 * v / tot, 4) for p, v in out.items()}


def future_radar_simulation(
    city_row: Optional[dict],
    shares_by_year: dict[int, dict[str, float]],
    national: Optional[dict[str, float]],
) -> Optional[dict[str, Any]]:
    if not shares_by_year or not city_row or not national:
        return None
    vol = party_share_volatility_pp(shares_by_year)
    extrap = linear_extrapolate_shares(shares_by_year)
    if not extrap:
        return None
    adjusted = apply_city_stats_with_volatility_weights(extrap, city_row, national, vol)
    return {
        "extrapolated_shares_pct": extrap,
        "adjusted_shares_pct": adjusted,
        "volatility_pp": vol,
        "city_scalar_inputs": {
            "unemployment_rate": city_row.get("unemployment_rate"),
            "university_grad_pct": city_row.get("university_grad_pct"),
            "growth_rate": city_row.get("growth_rate"),
            "foreign_pop_pct": city_row.get("foreign_pop_pct"),
        },
    }


# Eski isimden çağıran kodlar için
top_four_letters_from_shares = top_four_letters_from_party_shares
