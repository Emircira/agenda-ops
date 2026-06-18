"""
Nişanyan Yeradları — talebe bağlı HTTP kazıma (On-Demand).

SPA gövdelerinde sunucu tarafı .mli tablosu olmayabilir; bu modül önce <script>
içinde gömülü JSON (__NEXT_DATA__, application/json, vb.) ayıklar, sonra (varsa)
eski .mli HTML seçicilerini dener. Başarısızlık veya erişim engeli durumunda
UI testleri için kurumsal mock veri üretir (çökmez).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup
from loguru import logger

# Eski harita sorgusu (scriptype/nisanyanmap make-request.js)
NISANYAN_BASE = "https://www.nisanyanyeradlari.com"
UA = "Mozilla/5.0 (compatible; KarargahOSINT/1.1; +https://localhost) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# —— Gömülü JSON kalıpları ——
_RE_NEXT_DATA = re.compile(
    r'<script[^>]*\bid\s*=\s*["\']__NEXT_DATA__["\'][^>]*>(?P<json>.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
_RE_NUXT_DATA = re.compile(
    r'<script[^>]*>\s*window\.__NUXT__\s*=\s*(?P<json>\{.*?\})\s*;\s*</script>',
    re.DOTALL | re.IGNORECASE,
)
_RE_SVELTEKIT_DATA = re.compile(
    r'<script[^>]*type\s*=\s*["\']application\/(?:ld\+)?json["\'][^>]*>(?P<json>.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
# Büyük JSON nesnesi: {"..." veya [{ ile başlayan script gövdeleri (dikkatli, kısa devre)
_RE_JSON_OBJECT_IN_SCRIPT = re.compile(
    r"<script[^>]*>(?P<body>\s*(?:const|let|var)\s+\w+\s*=\s*)?(?P<json>\{[\s\S]{20,8192}\}|\[[\s\S]{20,8192}\])\s*;?\s*</script>",
    re.IGNORECASE,
)

_NAME_KEYS = re.compile(
    r"^(name|title|label|yer_?ad|yeradi|settlement|mahalle|koy|köy|village|place|"
    r"locality|toponym|eski|historical|oldname|old_name|eski_?ad|koken|köken|origin|not|description|detail|ozet|özet)$",
    re.IGNORECASE,
)


def _legacy_search_path(query: str) -> str:
    q = quote(query.strip(), safe="")
    return (
        "/?x=lalt&lg=&eth1=&eth2=&asr="
        f"&y={q}&t=&j=&ua=0&u=1&ll=&z=&sh=&srt=y"
    )


def _is_cloudflare_or_challenge(html: str) -> bool:
    if not html:
        return True
    head = html[:12000].lower()
    if "cf-mitigated" in head or "cf-ray" in head and "challenge" in head:
        return True
    if "just a moment" in head and "cloudflare" in head:
        return True
    if "checking your browser" in head:
        return True
    if "attention required" in head and "cloudflare" in head:
        return True
    return False


def _safe_json_loads(raw: str) -> Optional[Any]:
    s = raw.strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Bazen sonunda fazladan `;` veya trailing junk
    for end in range(len(s), max(0, len(s) - 200), -1):
        chunk = s[:end].rstrip().rstrip(";")
        if len(chunk) < 2:
            continue
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            continue
    return None


def _collect_script_json_from_html(html: str) -> List[Any]:
    """Regex + script type=application/json ile olası kök JSON'ları toplar."""
    roots: List[Any] = []
    seen: set[int] = set()

    def _add_parsed(obj: Any) -> None:
        if obj is None:
            return
        oid = id(obj)
        if oid in seen:
            return
        seen.add(oid)
        roots.append(obj)

    for pat in (_RE_NEXT_DATA, _RE_NUXT_DATA):
        for m in pat.finditer(html):
            parsed = _safe_json_loads(m.group("json"))
            if parsed is not None:
                _add_parsed(parsed)

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script"):
        sid = (tag.get("id") or "").strip()
        if sid in ("__NEXT_DATA__", "__NUXT_DATA__"):
            txt = tag.string or tag.get_text() or ""
            parsed = _safe_json_loads(txt)
            if parsed is not None:
                _add_parsed(parsed)
                continue
        typ = (tag.get("type") or "").lower()
        if typ in ("application/json", "application/ld+json"):
            txt = tag.string or tag.get_text() or ""
            parsed = _safe_json_loads(txt)
            if parsed is not None:
                _add_parsed(parsed)

    for m in _RE_SVELTEKIT_DATA.finditer(html):
        parsed = _safe_json_loads(m.group("json"))
        if parsed is not None:
            _add_parsed(parsed)

    # Serbest script içi { ... } / [ ... ] (ilk birkaç eşleşme)
    for i, m in enumerate(_RE_JSON_OBJECT_IN_SCRIPT.finditer(html)):
        if i >= 12:
            break
        parsed = _safe_json_loads(m.group("json"))
        if parsed is not None:
            _add_parsed(parsed)

    return roots


def _dict_name_score(d: Dict[str, Any]) -> int:
    if not isinstance(d, dict):
        return 0
    score = 0
    for k in d.keys():
        if isinstance(k, str) and _NAME_KEYS.match(k.strip()):
            score += 2
        elif isinstance(k, str) and any(x in k.lower() for x in ("ad", "name", "yer", "mah", "koy", "eski")):
            score += 1
    return score


def _row_from_dict(d: Dict[str, Any], keys_settlement: Tuple[str, ...]) -> Optional[Dict[str, Any]]:
    """Sözlükten standart satıra map; yeterli alan yoksa None."""

    def _get_first(*candidates: str) -> Optional[str]:
        for c in candidates:
            v = d.get(c)
            if v is None and c.lower() != c:
                v = d.get(c.lower())
            if v is None:
                continue
            if isinstance(v, (int, float)):
                return str(v)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, dict):
                for kk in ("name", "label", "text", "title"):
                    vv = v.get(kk)
                    if isinstance(vv, str) and vv.strip():
                        return vv.strip()
        return None

    settlement = _get_first(*keys_settlement)
    if not settlement:
        settlement = _get_first(
            "settlement_name",
            "yerAdi",
            "yer_adi",
            "toponym",
            "name",
            "title",
            "label",
            "mahalle",
            "koyAdi",
        )
    if not settlement:
        return None

    historical = _get_first(
        "historical_name",
        "oldName",
        "old_name",
        "eskiAd",
        "eski_ad",
        "formerName",
        "historical",
        "eskİAd",
    )
    cultural = _get_first(
        "cultural_origin",
        "origin",
        "koken",
        "köken",
        "description",
        "detail",
        "notes",
        "not",
        "text",
        "body",
        "summary",
    )
    if not historical and not cultural:
        # Kalan metin alanlarını birleştir
        blobs: List[str] = []
        for k, v in d.items():
            if k in ("settlement_name",) or not isinstance(v, str):
                continue
            if len(v) > 400:
                continue
            if len(v.strip()) > 2:
                blobs.append(f"{k}: {v.strip()}")
        cultural = " ".join(blobs[:6]) if blobs else None

    return {
        "settlement_name": settlement[:500],
        "historical_name": (historical[:500] if historical else None),
        "cultural_origin": (cultural[:12000] + "…" if cultural and len(cultural) > 12000 else cultural),
    }


def _extract_rows_from_json_tree(obj: Any, depth: int = 0, max_depth: int = 14) -> List[Dict[str, Any]]:
    """Recursive: anlamlı dict listelerinden standart satırlar üret."""
    if depth > max_depth:
        return []

    if isinstance(obj, list):
        if not obj:
            return []
        dict_items = [x for x in obj if isinstance(x, dict)]
        if len(dict_items) >= max(1, len(obj) // 2):
            scores = [_dict_name_score(x) for x in dict_items]
            if scores and max(scores) >= 2:
                rows_out: List[Dict[str, Any]] = []
                keys_try: Tuple[str, ...] = (
                    "settlement_name",
                    "name",
                    "yerAdi",
                    "yer",
                    "label",
                    "title",
                    "mahalleAdi",
                    "ad",
                )
                for d in dict_items:
                    r = _row_from_dict(d, keys_try)
                    if r:
                        rows_out.append(r)
                if rows_out:
                    return rows_out
        out: List[Dict[str, Any]] = []
        for item in obj:
            out.extend(_extract_rows_from_json_tree(item, depth + 1, max_depth))
        # Tekil tekrarları settlement_name ile ayıkla
        seen = set()
        uniq: List[Dict[str, Any]] = []
        for r in out:
            k = r.get("settlement_name", "")
            if k and k not in seen:
                seen.add(k)
                uniq.append(r)
        return uniq

    if isinstance(obj, dict):
        acc: List[Dict[str, Any]] = []
        for v in obj.values():
            acc.extend(_extract_rows_from_json_tree(v, depth + 1, max_depth))
        return acc

    return []


def _parse_legacy_mli(html: str) -> List[Dict[str, Any]]:
    """nisanyanmap@1.0.0 parse-results.js ile uyumlu seçiciler (varsa)."""
    soup = BeautifulSoup(html, "html.parser")
    lust = soup.select_one(".lust")
    if not lust or not re.search(r"\d+", lust.get_text()):
        return []

    rows: List[Dict[str, Any]] = []
    for el in soup.select(".mli"):
        links = el.select("a")
        name = links[1].get_text(strip=True) if len(links) > 1 else ""
        itag = el.select_one("i")
        typ = itag.get_text(strip=True) if itag else ""
        addr_parts = [x.get_text(" ", strip=True) for x in el.select(".ax")]
        history_fragments: List[str] = []
        entries_blob: List[str] = []

        for sec in el.select("div"):
            children = list(sec.children)
            if not children:
                continue
            last = children[-1]
            if getattr(last, "name", None) == "a" and (last.get("href") or "") == "javascript:void(0)":
                author = last.get_text(strip=True)
                try:
                    last.extract()
                except Exception:
                    pass
                raw_txt = sec.get_text(" ", strip=True)
                if raw_txt:
                    for part in raw_txt.split("■"):
                        p = part.strip()
                        if p:
                            entries_blob.append(f"{p} ({author})" if author else p)
            else:
                txt = sec.get_text(" ", strip=True)
                if txt:
                    history_fragments.append(txt)

        settlement = name or typ or (addr_parts[0] if addr_parts else "") or "Ad bilinmiyor"
        hist = history_fragments[:3]
        historical = hist[0][:500] if hist else None
        cultural = " | ".join(
            [x for x in [*hist, *entries_blob[:12], *addr_parts] if x]
        )
        if len(cultural) > 12000:
            cultural = cultural[:12000] + "…"

        rows.append(
            {
                "settlement_name": settlement[:500],
                "historical_name": historical,
                "cultural_origin": cultural or None,
                "raw_type": typ or None,
                "address": addr_parts,
            }
        )
    return rows


def _normalize_rows_for_api(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Depo/API ile uyum: yalnızca üç ana alan (+ ham alanları koru opsiyonel)."""
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "settlement_name": str(r.get("settlement_name") or "")[:500],
                "historical_name": (str(r["historical_name"])[:500] if r.get("historical_name") else None),
                "cultural_origin": (
                    (str(r["cultural_origin"])[:12000] + "…")
                    if r.get("cultural_origin") and len(str(r["cultural_origin"])) > 12000
                    else (str(r["cultural_origin"]) if r.get("cultural_origin") else None)
                ),
            }
        )
    return out


def mock_fallback_rows(province: str, district: str) -> List[Dict[str, Any]]:
    """B Planı: gösterim/test için kurumsal dilde örnek yerleşim satırları."""
    province = (province or "").strip() or "İl"
    district = (district or "").strip()
    loc_ctx = f"{district} ilçesi, {province} ili" if district else f"{province} ili (il geneli)"
    return [
        {
            "settlement_name": "Merkez",
            "historical_name": "Eski Adı Bilinmiyor",
            "cultural_origin": (
                f"{loc_ctx} — örnek OSINT önbellek kaydı. 19. yy sonu göçmen yerleşimi ve mübadele bölgesi. "
                "Bölgenin demografik yapısı tarihsel olarak kozmopolit özellikler gösterir; resmi Nişanyan "
                "verisine ulaşılamadığında arayüz doğrulaması için üretilmiştir."
            ),
        },
        {
            "settlement_name": "Çevre yerleşim (örnek)",
            "historical_name": "Kaynakta doğrulanmamış toponim",
            "cultural_origin": (
                f"{loc_ctx} kırsalında, tarım ve küçük sanayi geçişi ile şekillenen nüfus hareketliliği "
                "gözlenir. Tarihsel hafıza metni canlı veri yerine kurumsal test amaçlıdır."
            ),
        },
        {
            "settlement_name": "Kentsel doküman (örnek)",
            "historical_name": "—",
            "cultural_origin": (
                "Kent merkezinde planlı iskân ve çok katlı yapılaşma döneminde yoğun iç göç; "
                "sosyoekonomik profil çeşitlilik gösterir. Kesin etnik/köken beyanı için Nişanyan "
                "Index Anatolicus birincil kaynağa başvurulmalıdır."
            ),
        },
    ]


async def fetch_district_history(province: str, district: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    il + ilçe için Nişanyan arama dizesi üretir; HTTP GET sonrası script JSON veya .mli ile ayrıştırır.

    Returns:
        rows: `settlement_name`, `historical_name`, `cultural_origin`
        meta: istek URL'si, durum, parse notları
    """
    province = (province or "").strip()
    district = (district or "").strip()
    if not province:
        return [], {"error": "province_required", "url": None}

    query = f"{district} {province}".strip() if district else province
    path = _legacy_search_path(query)
    url = f"{NISANYAN_BASE}{path}"
    meta: Dict[str, Any] = {
        "url": url,
        "query": query,
        "site_redirect_note": "nisanyanmap.com bu hosta yönlendirilir (Index Anatolicus).",
    }

    html = ""
    try:
        async with httpx.AsyncClient(
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.5",
            },
            follow_redirects=True,
            timeout=httpx.Timeout(25.0),
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        logger.warning(f"Nişanyan HTTP: {e}")
        meta["error"] = str(e)
        meta["parse_status"] = "http_error_mock_fallback"
        meta["hint"] = "Canlı kaynağa ulaşılamadı; kurumsal mock veri döndürüldü (UI sürekliliği)."
        rows = mock_fallback_rows(province, district)
        meta["row_count"] = len(rows)
        meta["mock_fallback"] = True
        return rows, meta

    meta["html_bytes"] = len(html)

    if _is_cloudflare_or_challenge(html):
        meta["parse_status"] = "cloudflare_or_challenge_mock_fallback"
        meta["hint"] = "Erişim koruması veya doğrulama sayfası algılandı; mock veri döndürüldü."
        rows = mock_fallback_rows(province, district)
        meta["row_count"] = len(rows)
        meta["mock_fallback"] = True
        return rows, meta

    # 1) Script içi JSON (derin ağaç tarama)
    json_roots = _collect_script_json_from_html(html)
    meta["embedded_json_roots"] = len(json_roots)
    combined_json_rows: List[Dict[str, Any]] = []
    for root in json_roots:
        combined_json_rows.extend(_extract_rows_from_json_tree(root))

    if combined_json_rows:
        # Yinelenen yerleşim adları
        seen_names: set[str] = set()
        uniq_rows: List[Dict[str, Any]] = []
        for r in combined_json_rows:
            sn = r.get("settlement_name") or ""
            if sn and sn not in seen_names:
                seen_names.add(sn)
                uniq_rows.append(r)
        meta["parse_status"] = "embedded_json"
        meta["row_count"] = len(uniq_rows)
        return _normalize_rows_for_api(uniq_rows), meta

    # 2) Klasik .mli tablosu
    if ".mli" in html:
        parsed = _parse_legacy_mli(html)
        meta["parse_status"] = "mli_parsed" if parsed else "mli_selector_empty"
        meta["row_count"] = len(parsed)
        if parsed:
            return _normalize_rows_for_api(parsed), meta

    # 3) B Planı mock
    meta["parse_status"] = "no_extractable_data_mock_fallback"
    meta["hint"] = (
        "Sayfada çözümlenebilir JSON veya .mli bulunamadı (tipik SPA). "
        "Arayüz testi için kurumsal mock kayıtlar üretildi."
    )
    rows = mock_fallback_rows(province, district)
    meta["row_count"] = len(rows)
    meta["mock_fallback"] = True
    return rows, meta
