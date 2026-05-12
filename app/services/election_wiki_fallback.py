"""
Wikipedia (tr.wikipedia.org) tablolarından il bazlı seçim sonucu okuma — DB boşsa tamamlayıcı.
Yalnızca sayfadan çekilen hücreler kullanılır; sayı uydurulmaz.
"""

from __future__ import annotations

import io
import re
from typing import Any, Optional
from urllib.parse import unquote

import pandas as pd
import requests

from app.models.core import ElectionCategory

SESSION_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AgendaOpsElectionBot/1.0; +https://example.invalid)"}


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

