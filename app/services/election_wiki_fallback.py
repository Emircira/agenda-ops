"""
Wikipedia (tr.wikipedia.org) tablolarından il bazlı seçim sonucu okuma — DB boşsa tamamlayıcı.
Yalnızca sayfadan çekilen hücreler kullanılır; sayı uydurulmaz.

Ayrıca MediaWiki ``prop=extracts`` (+ ``explaintext``, ``exintro``) ile il/ilçe maddesi giriş
özeti düz metin ve kısaltılmış olarak alınır (Stratejik Seçim Radarı — ``historical_context``).
"""

from __future__ import annotations

import io
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote

import httpx
import pandas as pd
import requests

from app.models.core import ElectionCategory

SESSION_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AgendaOpsElectionBot/1.0; +https://example.invalid)"}

TR_WIKI_API = "https://tr.wikipedia.org/w/api.php"
# Wikimedia: açıklayıcı User-Agent (bot politikası)
WIKI_CONTEXT_HEADERS = {
    "User-Agent": "AgendaOps-Karargah/1.0 (+https://example.invalid; tr-wiki-context) httpx",
    "Accept": "application/json",
}


def normalize_tr(text: str) -> str:
    if not text:
        return ""
    return (
        str(text)
        .replace("İ", "i")
        .replace("I", "ı")
        .replace("Ş", "ş")
        .replace("Ğ", "ğ")
        .replace("Ü", "ü")
        .replace("Ö", "ö")
        .replace("Ç", "ç")
        .lower()
    )


def _wiki_slug_to_key(url: str) -> str:
    parts = url.rstrip("/").split("/wiki/")
    if len(parts) < 2:
        return "wikipedia"
    return f"wikipedia:{unquote(parts[-1])}"


def wikipedia_urls_for(election_type: ElectionCategory, year: int) -> list[str]:
    if election_type == ElectionCategory.local:
        mapping = {
            2019: "2019_Türkiye_yerel_seçimleri",
            2014: "2014_Türkiye_yerel_seçimleri",
            2009: "2009_Türkiye_yerel_seçimleri",
        }
        slug = mapping.get(year)
        if slug:
            base = f"https://tr.wikipedia.org/wiki/{slug}"
            return [base, f"{base}?printable=yes"]
    if election_type == ElectionCategory.parliamentary:
        mapping = {
            2018: "2018_Türkiye_genel_seçimleri",
            2015: "Kasım_2015_Türkiye_genel_seçimleri",
            2011: "2011_Türkiye_genel_seçimleri",
        }
        slug = mapping.get(year)
        if slug:
            base = f"https://tr.wikipedia.org/wiki/{slug}"
            return [base, f"{base}?printable=yes"]
    if election_type == ElectionCategory.presidential:
        mapping = {
            2023: "2023_Türkiye_cumhurbaşkanlığı_seçimi",
            2018: "2018_Türkiye_cumhurbaşkanlığı_ve_genel_seçimleri",
            2014: "2014_Türkiye_cumhurbaşkanlığı_seçimi",
        }
        slug = mapping.get(year)
        if slug:
            base = f"https://tr.wikipedia.org/wiki/{slug}"
            return [base, f"{base}?printable=yes"]
    return []


def _parse_intish(val: Any) -> int:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0
    s = str(val).strip()
    if not s or s in ("-", "—", ""):
        return 0
    s = re.sub(r"<[^>]+>", "", s)
    s = re.split(r"[\[\(]", s, maxsplit=1)[0].strip()
    s = s.replace(".", "").replace(",", "").replace("%", "")
    try:
        return int(float(s))
    except ValueError:
        return 0


def _cell_text(val: Any) -> str:
    s = re.sub(r"<[^>]+>", "", str(val) if val is not None else "")
    s = re.sub(r"\[\s*\d+\s*\]", "", s)
    return normalize_tr(s.strip())


def _pick_province_column(df: pd.DataFrame) -> Optional[str]:
    for col in df.columns:
        cn = normalize_tr(str(col))
        if "il" in cn and "sec" not in cn and "say" not in cn:
            return col
        if cn.strip() == "il":
            return col
    for col in df.columns:
        if normalize_tr(str(col)).startswith("il "):
            return col
    # İlk sütun genelde il adı
    if len(df.columns) > 0:
        c0 = df.columns[0]
        sample = df[c0].dropna().astype(str).head(12)
        good = sum(1 for v in sample if v and len(_cell_text(v)) <= 35 and not str(v).replace(".", "").isdigit())
        if good >= 3:
            return c0
    return None


def parse_province_vote_table(html: str, province: str) -> list[dict[str, Any]]:
    """İl satırını ve oy/parti sütunlarını bulur (Wikipedia wikitable)."""
    pn = normalize_tr(province)
    pn_compact = pn.replace(" ", "").replace(".", "")
    try:
        tables = pd.read_html(io.StringIO(html), decimal=",", thousands=".", flavor="lxml")
    except Exception:
        try:
            tables = pd.read_html(io.StringIO(html), decimal=",", thousands=".")
        except Exception:
            return []

    for df in tables:
        if len(df.columns) < 2 or len(df) < 2:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = ["_".join(map(str, col)).strip() for col in df.columns.values]
        prov_col = _pick_province_column(df)
        if prov_col is None:
            continue
        match_idx = None
        for idx, val in df[prov_col].items():
            cell = _cell_text(val)
            if not cell or cell in ("toplam", "genel toplam", "üniversite", "bölge"):
                continue
            if (
                pn == cell
                or pn in cell
                or cell in pn
                or pn_compact == cell.replace(" ", "").replace(".", "")
                or (len(pn) >= 4 and pn[:4] == cell[:4] and abs(len(pn) - len(cell)) <= 2)
            ):
                match_idx = idx
                break
        if match_idx is None:
            continue
        row = df.loc[match_idx]
        out: list[dict[str, Any]] = []
        for col in df.columns:
            if col == prov_col:
                continue
            cname = str(col).strip()
            low = normalize_tr(cname)
            if (
                "oran" in low
                or "yüzde" in low
                or low.endswith("%")
                or "toplam" == low
                or "katılım" in low
                or "seçmen" in low
                or "geçerli" in low
            ):
                continue
            votes = _parse_intish(row[col])
            if votes <= 0:
                continue
            party = re.sub(r"<[^>]+>", "", cname).strip()
            if len(party) < 2:
                continue
            out.append({"party": party, "vote_count": votes})
        if len(out) >= 1:
            return out
    return []


def fetch_wiki_votes_for_province(
    province: str,
    election_type: ElectionCategory,
    years_to_try: Optional[list[int]] = None,
) -> tuple[list[dict[str, Any]], str, Optional[int]]:
    if years_to_try is None:
        years_to_try = [2024, 2023, 2019, 2018, 2015, 2014, 2011, 2009]
    for y in years_to_try:
        urls = wikipedia_urls_for(election_type, y)
        for url in urls:
            try:
                r = requests.get(url, headers=SESSION_HEADERS, timeout=25)
                r.raise_for_status()
            except Exception:
                continue
            rows = parse_province_vote_table(r.text, province)
            if rows:
                key = _wiki_slug_to_key(url)
                return rows, f"{key} (yıl={y})", y
    return [], "", None


def _wiki_title_slug(s: str) -> str:
    return (s or "").strip().replace(" ", "_")


def _title_candidates(province: str, district: str) -> List[str]:
    """Türkçe Vikipedi başlık adayları (ilçe öncelikli)."""
    p = _wiki_title_slug(province)
    d_raw = (district or "").strip()
    if d_raw:
        d = _wiki_title_slug(d_raw)
        return [
            f"{d},_{p}",
            f"{d}_(ilçesi)",
            f"{d}_(ilçe)",
            f"{d}_{p}",
            d,
        ]
    return [
        p,
        f"{p}_(il)",
        f"{p}_ili",
    ]


def _sanitize_plain_extract(raw: str) -> str:
    """explaintext çıktısına güvenilir; HTML ve olası wiki sızıntılarını güvenlik için süzer."""
    if not raw:
        return ""
    s = str(raw).strip()
    s = re.sub(r"<[^>]+>", " ", s)
    s = " ".join(s.split())
    for marker in ("{|", "{{", "__TOC__", "[[Dosya:", "[[File:", "[[Resim:"):
        if marker in s:
            s = s.split(marker, 1)[0].strip()
    s = re.sub(r"\[\[([^]|]+)\|([^\]]*)\]\]", r"\2", s)
    s = re.sub(r"\[\[([^\]]+)\]\]", r"\1", s)
    return " ".join(s.split()).strip()


def _truncate_intro_extract(
    text: str,
    *,
    max_chars: int = 500,
    max_sentences: int = 5,
) -> str:
    """İlk birkaç cümle ve en fazla ``max_chars`` karakter düz özet."""
    text = _sanitize_plain_extract(text)
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?…])\s+", text)
    sentences = [p.strip() for p in parts if p.strip()]
    if sentences:
        blob = " ".join(sentences[:max_sentences]).strip()
    else:
        blob = text
    if len(blob) > max_chars:
        cut = blob[:max_chars]
        sp = cut.rfind(" ")
        if sp > int(max_chars * 0.55):
            blob = cut[:sp].rstrip(" ,;:\"'") + "…"
        else:
            blob = cut.rstrip() + "…"
    return _sanitize_plain_extract(blob)


async def _wiki_api(client: httpx.AsyncClient, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """MediaWiki API GET; ağ/DNS/timeout veya hatalı yanıtta asla exception fırlatmaz."""
    try:
        q = {**params, "format": "json", "formatversion": "2"}
        r = await client.get(TR_WIKI_API, params=q, headers=WIKI_CONTEXT_HEADERS, timeout=httpx.Timeout(30.0))
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("errors"):
        return None
    if "error" in data:
        return None
    return data


async def _resolve_page_title(client: httpx.AsyncClient, candidates: List[str]) -> tuple[Optional[str], List[str]]:
    """İlk var olan başlığı döndürür (normalize edilmiş)."""
    tried: List[str] = []
    for raw in candidates:
        t = raw.strip()
        if not t:
            continue
        tried.append(t)
        data = await _wiki_api(
            client,
            {"action": "query", "titles": t, "prop": "info", "inprop": "url"},
        )
        if data is None:
            continue
        pages = (data.get("query") or {}).get("pages") or []
        for pg in pages:
            if pg.get("missing") or pg.get("invalid"):
                continue
            title = pg.get("title")
            if title:
                return title, tried
    return None, tried


async def _open_search_title(client: httpx.AsyncClient, search: str) -> Optional[str]:
    if not search.strip():
        return None
    try:
        r = await client.get(
            TR_WIKI_API,
            params={
                "action": "opensearch",
                "search": search,
                "limit": 5,
                "namespace": 0,
                "format": "json",
            },
            headers=WIKI_CONTEXT_HEADERS,
            timeout=httpx.Timeout(20.0),
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list) and data[1]:
            return data[1][0]
    except Exception:
        pass
    return None


async def _fetch_intro_extract_plain(client: httpx.AsyncClient, title: str) -> str:
    """Sadece giriş özeti — ``explaintext`` + ``exintro`` (wikitext / rev içerik yok)."""
    data = await _wiki_api(
        client,
        {
            "action": "query",
            "titles": title,
            "prop": "extracts",
            "explaintext": "1",
            "exintro": "1",
            "redirects": "1",
        },
    )
    if data is None:
        return ""
    pages = (data.get("query") or {}).get("pages") or []
    for pg in pages:
        ex = pg.get("extract")
        if ex:
            return _truncate_intro_extract(str(ex))
    return ""


def fallback_wikipedia_regional_context(
    province: str,
    district: str,
    exc: Optional[BaseException] = None,
) -> Dict[str, Any]:
    """Ağ/DNS/timeout — analizi durdurmaz; API şemasıyla uyumlu sessiz yanıt."""
    scope = "district" if (district or "").strip() else "province"
    err = str(exc) if exc is not None else "wikipedia_unreachable"
    return {
        "ok": False,
        "source": "tr.wikipedia.org",
        "scope": scope,
        "title": None,
        "page_url": None,
        "summary": "Ağ bağlantısı kurulamadı veya Wikipedia'ya erişilemiyor.",
        "history": "",
        "population": "",
        "error": err,
        "attempted_titles": [],
        "limits": {"max_chars": 500, "max_sentences": 5},
    }


async def fetch_wikipedia_regional_context(province: str, district: str = "") -> Dict[str, Any]:
    """
    tr.wikipedia.org — maddenin giriş özeti (düz metin, kısaltılmış).

    İlçe seçiliyse önce ilçe maddesi, yoksa il maddesi hedeflenir. Bölüm ayrıştırması yoktur.
    Ağ veya DNS hatalarında exception fırlatmaz.
    """
    province = (province or "").strip()
    district = (district or "").strip()
    scope = "district" if district else "province"
    result: Dict[str, Any] = {
        "ok": False,
        "source": "tr.wikipedia.org",
        "scope": scope,
        "title": None,
        "page_url": None,
        "summary": None,
        "history": "",
        "population": "",
        "error": None,
        "attempted_titles": [],
        "limits": {"max_chars": 500, "max_sentences": 5},
    }
    if not province:
        result["error"] = "province_required"
        return result

    candidates = _title_candidates(province, district)
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            title, tried = await _resolve_page_title(client, candidates)
            result["attempted_titles"] = list(tried)

            if not title:
                q = f"{district} {province}".strip() if district else province
                title = await _open_search_title(client, q)
                if title:
                    result["attempted_titles"].append(f"opensearch:{q}")

            if not title:
                result["error"] = "page_not_found"
                return result

            info_data = await _wiki_api(
                client,
                {"action": "query", "titles": title, "prop": "info", "inprop": "url"},
            )
            if info_data is None:
                result["summary"] = "Ağ bağlantısı kurulamadı veya Wikipedia'ya erişilemiyor."
                result["error"] = "wikipedia_unreachable"
                result["title"] = title
                slug = quote(title.replace(" ", "_"), safe="")
                result["page_url"] = f"https://tr.wikipedia.org/wiki/{slug}"
                return result

            pages = (info_data.get("query") or {}).get("pages") or []
            page_url = None
            for pg in pages:
                page_url = pg.get("fullurl") or pg.get("canonicalurl")
                if page_url:
                    break
            if not page_url:
                slug = quote(title.replace(" ", "_"), safe="")
                page_url = f"https://tr.wikipedia.org/wiki/{slug}"

            summary = await _fetch_intro_extract_plain(client, title)

            if not summary:
                result["error"] = "empty_content"
                result["title"] = title
                result["page_url"] = page_url
                return result

            result["ok"] = True
            result["title"] = title
            result["page_url"] = page_url
            result["summary"] = summary
    except Exception as e:
        return fallback_wikipedia_regional_context(province, district, e)
    return result

