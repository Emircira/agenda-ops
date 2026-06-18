"""Seçim radarı: YSK zaman serisi + TÜİK + isteğe bağlı Nişanyan + Vikipedi tarihsel bağlam."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.models.core import ElectionCategory, NisanyanDemographics
from app.repositories.election_repository import ElectionRepository
from app.repositories.nisanyan_repository import NisanyanRepository
from app.services.election_wiki_fallback import (
    fallback_wikipedia_regional_context,
    fetch_wikipedia_regional_context,
    normalize_tr,
)
from app.services.simulator import historical_shares_by_year_from_election_rows

YSK_YEAR_MIN = 2009
YSK_YEAR_MAX = 2024


def _app_data_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data"


def load_tuik_json_layers(province: str, district: str = "") -> Dict[str, Any]:
    """city_stats.json / district_stats.json — yapılandırılmış özet."""
    out: Dict[str, Any] = {"city": None, "district": None, "sources": []}
    p_norm = normalize_tr(province)
    d_dir = _app_data_dir()
    city_path = d_dir / "city_stats.json"
    if city_path.is_file():
        try:
            with open(city_path, "r", encoding="utf-8-sig") as f:
                cities = json.load(f)
            for item in cities if isinstance(cities, list) else []:
                if isinstance(item, dict) and normalize_tr(item.get("province", "")) == p_norm:
                    out["city"] = item
                    out["sources"].append("app/data/city_stats.json")
                    break
        except OSError:
            pass
    if district and str(district).strip():
        dist_path = d_dir / "district_stats.json"
        if dist_path.is_file():
            try:
                with open(dist_path, "r", encoding="utf-8-sig") as f:
                    rows = json.load(f)
                d_norm = normalize_tr(district)
                for item in rows if isinstance(rows, list) else []:
                    if (
                        isinstance(item, dict)
                        and normalize_tr(item.get("province", "")) == p_norm
                        and normalize_tr(item.get("district", "")) == d_norm
                    ):
                        out["district"] = item
                        out["sources"].append("app/data/district_stats.json")
                        break
            except OSError:
                pass
    return out


def _chartjs_datasets(shares_by_year: Dict[str, Dict[str, float]], max_parties: int = 6) -> Dict[str, Any]:
    years = sorted(int(y) for y in shares_by_year.keys())
    if not years:
        return {"labels": [], "datasets": []}
    party_scores: Dict[str, float] = {}
    ly = str(years[-1])
    for p, v in shares_by_year.get(ly, {}).items():
        party_scores[p] = float(v)
    top_parties = [p for p, _ in sorted(party_scores.items(), key=lambda x: -x[1])[:max_parties]]

    datasets = []
    colors = [
        "rgba(234, 67, 53, 0.9)",
        "rgba(26, 115, 232, 0.9)",
        "rgba(251, 188, 4, 0.9)",
        "rgba(52, 168, 83, 0.9)",
        "rgba(158, 109, 235, 0.9)",
        "rgba(0, 172, 193, 0.9)",
    ]
    for i, party in enumerate(top_parties):
        data = [round(float(shares_by_year.get(str(y), {}).get(party, 0.0)), 2) for y in years]
        datasets.append(
            {
                "label": party,
                "data": data,
                "borderColor": colors[i % len(colors)],
                "backgroundColor": colors[i % len(colors)].replace("0.9", "0.15"),
                "tension": 0.25,
                "fill": False,
            }
        )
    return {"labels": years, "datasets": datasets}


async def build_ysk_bundle(
    erepo: ElectionRepository,
    province: str,
    district: str,
) -> Dict[str, Any]:
    all_rows = await erepo.list_election_results_by_type(ElectionCategory.local)
    shares = historical_shares_by_year_from_election_rows(
        all_rows,
        normalize_tr,
        province,
        district or "",
        ElectionCategory.local,
    )
    shares_str = {
        str(y): parties for y, parties in shares.items() if YSK_YEAR_MIN <= int(y) <= YSK_YEAR_MAX
    }
    years = sorted(int(y) for y in shares_str.keys())
    return {
        "election_type": "local",
        "year_range": [YSK_YEAR_MIN, YSK_YEAR_MAX],
        "years": years,
        "shares_by_year": shares_str,
        "chartjs": _chartjs_datasets(shares_str),
    }


def _serialize_nisanyan_row(r: NisanyanDemographics) -> Dict[str, Any]:
    return {
        "id": r.id,
        "province": r.province,
        "district": r.district,
        "settlement_name": r.settlement_name,
        "historical_name": r.historical_name,
        "cultural_origin": r.cultural_origin,
    }


def _serialize_nisanyan_ephemeral(
    province: str,
    district: str,
    item: Dict[str, Any],
) -> Dict[str, Any]:
    """mock_fallback vb. için DB satırı olmadan API şekli."""
    return {
        "id": None,
        "province": province,
        "district": district,
        "settlement_name": item.get("settlement_name") or "—",
        "historical_name": item.get("historical_name"),
        "cultural_origin": item.get("cultural_origin"),
    }


async def build_geo_synthesis(
    *,
    erepo: ElectionRepository,
    nrepo: Optional[NisanyanRepository] = None,
    province: str,
    district: str,
    scraper_call: Optional[
        Callable[[], Awaitable[Tuple[List[Dict[str, Any]], Dict[str, Any]]]]
    ] = None,
) -> Dict[str, Any]:
    """
    YSK 2009–2024 yerel, TÜİK, isteğe bağlı Nişanyan önbelleği, tr.wikipedia historical_context.

    ``scraper_call`` ve ``nrepo`` yoksa Nişanyan atlanır; tarihsel sekme Vikipedi kullanır.
    """
    province = (province or "").strip()
    district = (district or "").strip()
    dkey = district

    scrape_meta: Dict[str, Any]
    nisanyan_serialized: List[Dict[str, Any]]

    if scraper_call is None or nrepo is None:
        nisanyan_serialized = []
        scrape_meta = {
            "disabled": True,
            "note": "Tarihsel içerik Vikipedi API (historical_context) ile sağlanır.",
        }
    else:
        cached = await nrepo.list_by_province_district(province, dkey)
        scrape_meta = {"cache_hit": bool(cached)}

        if cached:
            nisanyan_serialized = [_serialize_nisanyan_row(r) for r in cached]
        else:
            raw_rows, meta = await scraper_call()
            scrape_meta.update(meta)
            scrape_meta["cache_hit"] = False

            if meta.get("mock_fallback") is True:
                scrape_meta["persisted_count"] = 0
                scrape_meta["db_persist_skipped"] = True
                nisanyan_serialized = [
                    _serialize_nisanyan_ephemeral(province, dkey, item) for item in raw_rows
                ]
            else:
                orm_rows = [
                    NisanyanDemographics(
                        province=province,
                        district=dkey,
                        settlement_name=item.get("settlement_name") or "—",
                        historical_name=item.get("historical_name"),
                        cultural_origin=item.get("cultural_origin"),
                    )
                    for item in raw_rows
                ]
                await nrepo.replace_district_cache(
                    province=province, district=dkey, rows=orm_rows, commit=True
                )
                cached = await nrepo.list_by_province_district(province, dkey)
                scrape_meta["persisted_count"] = len(orm_rows)
                nisanyan_serialized = [_serialize_nisanyan_row(r) for r in cached]
    ysk = await build_ysk_bundle(erepo, province, dkey)
    tuik_db_rows = await erepo.list_district_demographics_for(
        province,
        district if district else None,
    )
    tuik_db = [
        {
            "province": r.province,
            "district": r.district,
            "year": r.year,
            "total_population": r.total_population,
            "growth_rate": r.growth_rate,
            "university_grad_pct": r.university_grad_pct,
            "unemployment_rate": r.unemployment_rate,
            "foreign_pop_pct": r.foreign_pop_pct,
        }
        for r in tuik_db_rows
    ]
    tuik_json = load_tuik_json_layers(province, district)

    try:
        historical_context = await fetch_wikipedia_regional_context(province, dkey)
    except Exception as e:
        historical_context = fallback_wikipedia_regional_context(province, dkey, e)

    return {
        "province": province,
        "district": dkey,
        "ysk": ysk,
        "tuik": {
            "database": tuik_db,
            "json_files": {"city": tuik_json.get("city"), "district": tuik_json.get("district")},
            "sources": tuik_json.get("sources", []),
        },
        "nisanyan": {
            "rows": nisanyan_serialized,
            "meta": scrape_meta,
        },
        "historical_context": historical_context,
    }
