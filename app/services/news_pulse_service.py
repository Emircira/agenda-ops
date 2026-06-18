"""
Canlı Saha Nabzı — İl bazlı güncel haber çekimi (Google News RSS).

Fail-safe tasarım: internet kesilse, RSS bozulsa veya timeout olsa bile
ana analiz motorunu etkilemez; boş liste veya hata mesajı döner.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

import feedparser
from bs4 import BeautifulSoup
from loguru import logger

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------
_DEFAULT_MAX_ITEMS = 8
_FEED_TIMEOUT_SECONDS = 10
_MAX_TITLE_LEN = 200
_MAX_SNIPPET_LEN = 300


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------
def _clean_html(raw: str) -> str:
    """HTML etiketlerini düz metne çevirir."""
    if not raw:
        return ""
    try:
        return BeautifulSoup(raw, "html.parser").get_text(separator=" ", strip=True)
    except Exception:
        return raw


def _build_google_news_url(province: str) -> str:
    """İl için Google News RSS arama URL'si oluşturur."""
    city = (province or "").strip()
    if not city:
        city = "Türkiye"
    # Son 24 saat ve il adı ile haber arama
    query = f'"{city}" (gündem OR haber OR son dakika OR gelişme)'
    return (
        f"https://news.google.com/rss/search?"
        f"q={quote(query, safe='')}&hl=tr&gl=TR&ceid=TR:tr"
    )


def _parse_pub_date(entry: dict) -> Optional[datetime]:
    """RSS entry'den tarih parse eder; hata olursa None."""
    import time as _time

    pp = entry.get("published_parsed")
    if pp:
        try:
            return datetime.fromtimestamp(_time.mktime(pp))
        except Exception:
            pass
    # Fallback: string parse
    raw = entry.get("published", "")
    if raw:
        for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                return datetime.strptime(raw, fmt)
            except Exception:
                continue
    return None


def _is_within_hours(dt: Optional[datetime], hours: int = 48) -> bool:
    """Tarih son N saat içinde mi?"""
    if dt is None:
        return True  # tarih bilinmiyor → göster (kayıp bilgi yok)
    try:
        return dt > datetime.utcnow() - timedelta(hours=hours)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Ana Servis
# ---------------------------------------------------------------------------
async def fetch_province_news_pulse(
    province: str,
    max_items: int = _DEFAULT_MAX_ITEMS,
    timeout_seconds: int = _FEED_TIMEOUT_SECONDS,
) -> dict:
    """
    Seçilen il için Google News RSS'den güncel haberleri çeker.

    Dönüş:
        {
            "ok": True/False,
            "province": "Ankara",
            "items": [
                {
                    "title": "...",
                    "snippet": "...",
                    "url": "...",
                    "source": "Haber kaynağı",
                    "published_at": "2026-05-21T12:00:00",
                    "age_label": "2 saat önce"
                },
                ...
            ],
            "fetched_at": "...",
            "error": null | "hata mesajı"
        }
    """
    province_clean = (province or "").strip()
    result = {
        "ok": False,
        "province": province_clean,
        "items": [],
        "fetched_at": datetime.utcnow().isoformat(),
        "error": None,
    }

    if not province_clean:
        result["error"] = "İl adı belirtilmedi."
        return result

    feed_url = _build_google_news_url(province_clean)
    logger.info(f"📰 Saha Nabzı: {province_clean} için haber taranıyor → {feed_url[:80]}…")

    try:
        # feedparser senkron; thread pool'da çalıştır + timeout
        loop = asyncio.get_event_loop()
        feed = await asyncio.wait_for(
            loop.run_in_executor(None, feedparser.parse, feed_url),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning(f"📰 Saha Nabzı [{province_clean}]: Zaman aşımı ({timeout_seconds}s)")
        result["error"] = f"Haber çekimi zaman aşımına uğradı ({timeout_seconds}s)."
        return result
    except Exception as exc:
        logger.warning(f"📰 Saha Nabzı [{province_clean}]: RSS parse hatası: {exc}")
        result["error"] = f"Haber çekimi sırasında hata: {str(exc)[:120]}"
        return result

    if not feed or not getattr(feed, "entries", None):
        logger.info(f"📰 Saha Nabzı [{province_clean}]: Haber bulunamadı (boş feed).")
        result["ok"] = True  # başarılı ama boş
        result["error"] = None
        return result

    items = []
    now = datetime.utcnow()
    for entry in feed.entries[:max_items * 2]:  # fazla çek, sonra filtrele
        if len(items) >= max_items:
            break

        pub_dt = _parse_pub_date(entry)
        if not _is_within_hours(pub_dt, hours=48):
            continue

        title = (entry.get("title") or "")[:_MAX_TITLE_LEN].strip()
        if not title:
            continue

        # Google News RSS: source genelde title'da " - KaynakAdı" olarak eklenir
        source_name = ""
        if entry.get("source", {}).get("title"):
            source_name = entry["source"]["title"]
        elif " - " in title:
            parts = title.rsplit(" - ", 1)
            if len(parts) == 2 and len(parts[1]) < 50:
                source_name = parts[1].strip()

        snippet = _clean_html(
            entry.get("summary", entry.get("description", ""))
        )[:_MAX_SNIPPET_LEN].strip()

        url = entry.get("link", entry.get("id", ""))

        # Yaş etiketi
        age_label = ""
        if pub_dt:
            diff = now - pub_dt
            total_min = int(diff.total_seconds() / 60)
            if total_min < 60:
                age_label = f"{max(1, total_min)} dk önce"
            elif total_min < 1440:
                age_label = f"{total_min // 60} saat önce"
            else:
                age_label = f"{total_min // 1440} gün önce"

        items.append({
            "title": title,
            "snippet": snippet,
            "url": url,
            "source": source_name,
            "published_at": pub_dt.isoformat() if pub_dt else None,
            "age_label": age_label,
        })

    result["ok"] = True
    result["items"] = items
    logger.info(f"✅ Saha Nabzı [{province_clean}]: {len(items)} haber bulundu.")
    return result
