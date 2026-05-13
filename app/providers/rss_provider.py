import feedparser
from bs4 import BeautifulSoup
from datetime import datetime
import time
from urllib.parse import quote
from loguru import logger


def _rss_alternate_feed_url(source_url: str) -> str | None:
    """Bazı kaynaklar boş/ölü endpoint döndürür; bilinen çalışan RSS ile devam edilir."""
    if not (source_url or "").strip():
        return None
    low = source_url.strip().lower().split("?", 1)[0].rstrip("/")
    if "trthaber.com" in low and "xml_mobile.php" in low:
        return "https://www.trthaber.com/sondakika.rss"
    return None


class RSSProvider:
    """RSS kaynaklarından haber çekimi (feed başına yapılandırılabilir üst sınır)."""

    @staticmethod
    def google_news_city_complaints_url(city: str) -> str:
        c = (city or "").strip()
        if not c:
            c = "Türkiye"
        q = f'"{c}"+(şikayet+OR+protesto+OR+isyan+OR+tepki+OR+kesinti)'
        return (
            f"https://news.google.com/rss/search?q={quote(q, safe='')}"
            f"&hl=tr&gl=TR&ceid=TR:tr"
        )

    async def fetch_google_news_city_complaints(self, city: str, max_items: int = 10) -> list[dict]:
        url = self.google_news_city_complaints_url(city)
        name = f"GoogleNews Şikayet:{(city or '').strip() or 'Türkiye'}"
        items = await self.fetch_feed(url, name, max_items=max(1, min(int(max_items), 30)))
        return items[:max_items]

    @staticmethod
    def google_news_city_news_url(city: str) -> str:
        c = (city or "").strip()
        if not c:
            c = "Türkiye"
        q = f'"{c}" son dakika haber'
        return (
            f"https://news.google.com/rss/search?q={quote(q, safe='')}"
            f"&hl=tr&gl=TR&ceid=TR:tr"
        )

    async def fetch_google_news_city_news(self, city: str, max_items: int = 15) -> list[dict]:
        url = self.google_news_city_news_url(city)
        name = f"GoogleNews Son Dakika:{(city or '').strip() or 'Türkiye'}"
        items = await self.fetch_feed(url, name, max_items=max(1, min(int(max_items), 30)))
        return items[:max_items]

    def _clean_html(self, raw_html: str) -> str:
        """Haber metinlerindeki gereksiz HTML etiketlerini temizler."""
        if not raw_html:
            return ""
        soup = BeautifulSoup(raw_html, "html.parser")
        return soup.get_text(separator=" ", strip=True)

    async def fetch_feed(self, source_url: str, source_name: str, max_items: int = 1000) -> list[dict]:
        """
        RSS linkinden güncel haberleri standart Karargah formatında çeker.
        max_items: En fazla kaç haber döndürülsün (default 1000 = 5x derin tarama).
        """
        logger.info(f"📡 RSS Avcısı: {source_name} taranıyor...")
        urls_to_try = [source_url.strip()]
        alt = _rss_alternate_feed_url(source_url)
        if alt and alt not in urls_to_try:
            urls_to_try.append(alt)

        parsed_data: list[dict] = []
        for attempt_url in urls_to_try:
            try:
                feed = feedparser.parse(attempt_url)
                if getattr(feed, "bozo", False) and not feed.entries:
                    logger.warning(
                        f"RSS [{source_name}] parse uyarısı (entry yok): {attempt_url} — "
                        f"{getattr(feed, 'bozo_exception', 'bilinmeyen')}"
                    )
                    continue

                if not feed.entries:
                    logger.warning(f"RSS [{source_name}] bu URL için entry yok: {attempt_url}")
                    continue

                if getattr(feed, "bozo", False):
                    logger.debug(f"RSS [{source_name}] bozo=True ama {len(feed.entries)} entry kullanılıyor: {attempt_url}")

                if attempt_url != source_url.strip():
                    logger.info(f"📡 RSS [{source_name}]: yedek URL kullanıldı → {attempt_url}")

                for entry in feed.entries[:max_items]:
                    pub_parsed = entry.get("published_parsed")
                    pub_date = datetime.fromtimestamp(time.mktime(pub_parsed)) if pub_parsed else datetime.utcnow()

                    title = entry.get("title", "")
                    summary = self._clean_html(entry.get("summary", entry.get("description", "")))

                    parsed_data.append({
                        "platform": "rss",
                        "external_id": entry.get("id", entry.get("link", "")),
                        "author_name": source_name,
                        "published_at": pub_date,
                        "text": f"{title}\n\n{summary}",
                        "content_type": "article",
                        "url": entry.get("link", ""),
                        "raw_json": entry
                    })

                logger.info(
                    f"✅ RSS [{source_name}]: {len(parsed_data)} haber çekildi "
                    f"(kaynak: {attempt_url}, feed'de {len(feed.entries)} entry)."
                )
                return parsed_data

            except Exception as e:
                logger.warning(f"RSS [{source_name}] deneme başarısız ({attempt_url}): {e}")

        logger.warning(f"RSS [{source_name}] için kullanılabilir kayıt yok (denenen URL'ler: {urls_to_try}).")
        return []