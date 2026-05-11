import feedparser
from bs4 import BeautifulSoup
from datetime import datetime
import time
from loguru import logger


class RSSProvider:
    """
    RSS veri sağlayıcısı — limit kaldırıldı, tüm feed'i çeker.
    Google News genelde 60-100+ haber döndürür; Derin Araştırma 5x tarama için 1000'e kadar entry okur.
    """

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
        parsed_data = []
        try:
            feed = feedparser.parse(source_url)

            if not feed.entries:
                logger.warning(f"⚠️ RSS [{source_name}]: Hiç entry bulunamadı. URL kontrol edin: {source_url}")
                return []

            for entry in feed.entries[:max_items]:  # Derin Araştırma: 200 → 1000
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

            logger.info(f"✅ RSS [{source_name}]: {len(parsed_data)} haber çekildi (feed'de toplam {len(feed.entries)} entry var).")
        except Exception as e:
            logger.error(f"❌ RSS Çekim Hatası [{source_name}]: {e}")
        return parsed_data