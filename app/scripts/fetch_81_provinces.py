"""
81 il için nisanyanmap.com HTML'inden gömülü JSON (Next/Nuxt/script regex+parse) çıkarır;
sonucu app/data/nisanyan.json olarak yazar.

Çalıştırma (repo kökünden): python app/scripts/fetch_81_provinces.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import quote

import httpx
from loguru import logger

# Proje kökü: app/scripts/fetch_81_provinces.py -> parents[2] = agenda-ops
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.services.nisanyan_scraper import (  # noqa: E402
    _collect_script_json_from_html,
    _extract_rows_from_json_tree,
    _normalize_rows_for_api,
)

# Güncel masaüstü tarayıcı kimliği (2025–2026 tipik)
HTTP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

NISANYAN_MAP_BASE = "https://nisanyanmap.com"

# Türkiye'nin 81 ili (resmî sıra / plaka koduna göre)
PROVINCES_81: List[str] = [
    "Adana",
    "Adıyaman",
    "Afyonkarahisar",
    "Ağrı",
    "Amasya",
    "Ankara",
    "Antalya",
    "Artvin",
    "Aydın",
    "Balıkesir",
    "Bilecik",
    "Bingöl",
    "Bitlis",
    "Bolu",
    "Burdur",
    "Bursa",
    "Çanakkale",
    "Çankırı",
    "Çorum",
    "Denizli",
    "Diyarbakır",
    "Edirne",
    "Elazığ",
    "Erzincan",
    "Erzurum",
    "Eskişehir",
    "Gaziantep",
    "Giresun",
    "Gümüşhane",
    "Hakkâri",
    "Hatay",
    "Isparta",
    "Mersin",
    "İstanbul",
    "İzmir",
    "Kars",
    "Kastamonu",
    "Kayseri",
    "Kırklareli",
    "Kırşehir",
    "Kocaeli",
    "Konya",
    "Kütahya",
    "Malatya",
    "Manisa",
    "Kahramanmaraş",
    "Mardin",
    "Muğla",
    "Muş",
    "Nevşehir",
    "Niğde",
    "Ordu",
    "Rize",
    "Sakarya",
    "Samsun",
    "Siirt",
    "Sinop",
    "Sivas",
    "Tekirdağ",
    "Tokat",
    "Trabzon",
    "Tunceli",
    "Şanlıurfa",
    "Uşak",
    "Van",
    "Yozgat",
    "Zonguldak",
    "Aksaray",
    "Bayburt",
    "Karaman",
    "Kırıkkale",
    "Batman",
    "Şırnak",
    "Bartın",
    "Ardahan",
    "Iğdır",
    "Yalova",
    "Karabük",
    "Kilis",
    "Osmaniye",
    "Düzce",
]


def _dedupe_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for r in rows:
        sn = (r.get("settlement_name") or "").strip()
        if not sn or sn in seen:
            continue
        seen.add(sn)
        out.append(r)
    return out


def extract_places_from_html(html: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """<script> içindeki Next/Nuxt/gömülü JSON ağaçlarını çıkarır; mock_fallback yok."""
    meta: Dict[str, Any] = {"embedded_json_roots": 0, "parse_sources": []}
    roots = _collect_script_json_from_html(html)
    meta["embedded_json_roots"] = len(roots)

    combined: List[Dict[str, Any]] = []
    for root in roots:
        combined.extend(_extract_rows_from_json_tree(root))

    if combined:
        meta["parse_sources"].append("embedded_script_json")
        return _normalize_rows_for_api(_dedupe_rows(combined)), meta

    return [], meta


async def fetch_province(
    client: httpx.AsyncClient,
    province: str,
) -> Dict[str, Any]:
    q = quote(province.strip(), safe="")
    url = f"{NISANYAN_MAP_BASE}/?y={q}"
    entry: Dict[str, Any] = {
        "province": province,
        "url": url,
        "ok": False,
        "rows": [],
        "meta": {},
        "error": None,
    }
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        html = resp.text
        rows, pmeta = extract_places_from_html(html)
        entry["meta"] = {**pmeta, "html_bytes": len(html)}
        entry["rows"] = rows
        if rows:
            entry["ok"] = True
            entry["meta"]["row_count"] = len(rows)
        else:
            entry["error"] = "no_rows_extracted"
            logger.error(
                "Hata: {} — çıkarılabilir satır yok (json_kök={}, url={})",
                province,
                pmeta.get("embedded_json_roots"),
                url,
            )
    except Exception as e:
        entry["error"] = str(e)
        logger.error("Hata: {} — {} (url={})", province, e, url)
    return entry


async def run() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "https://nisanyanmap.com/?y={province}",
        "user_agent_note": HTTP_UA[:80] + "…",
        "province_count": len(PROVINCES_81),
        "results": [],
    }

    headers = {
        "User-Agent": HTTP_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    timeout = httpx.Timeout(45.0)
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=timeout) as client:
        for i, province in enumerate(PROVINCES_81):
            logger.info("[{}/{}] {} çekiliyor…", i + 1, len(PROVINCES_81), province)
            result = await fetch_province(client, province)
            out["results"].append(result)
            if i < len(PROVINCES_81) - 1:
                await asyncio.sleep(3)

    ok_n = sum(1 for r in out["results"] if r.get("ok"))
    out["summary"] = {
        "success_provinces": ok_n,
        "failed_or_empty": len(PROVINCES_81) - ok_n,
        "total_rows": sum(len(r.get("rows") or []) for r in out["results"]),
    }
    return out


def main() -> None:
    data_dir = _PROJECT_ROOT / "app" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "nisanyan.json"

    payload = asyncio.run(run())
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Yazıldı: {} (satır özeti: {})", target, payload.get("summary"))


if __name__ == "__main__":
    main()
