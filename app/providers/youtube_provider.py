import asyncio
import os
import time
from datetime import datetime, timedelta
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from loguru import logger


class YouTubeProvider:
    """
    YouTube veri sağlayıcısı — Sayfalama (pagination) destekli.
    Tek seferde 50 video (API max), nextPageToken ile devam.
    Rate limit koruması ve retry mekanizması eklenmiş.
    """

    # İstekler arası bekleme süresi (saniye) — quota koruması
    API_DELAY = 1.5
    # Rate limit sonrası bekleme (saniye)
    RATE_LIMIT_DELAY = 30.0
    # Maksimum retry sayısı
    MAX_RETRIES = 3

    def __init__(self):
        self.api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
        if not self.api_key:
            raise RuntimeError("YOUTUBE_API_KEY zorunludur; YouTube için boş/mock fallback yoktur.")
        self.youtube = build('youtube', 'v3', developerKey=self.api_key)

    async def _safe_execute(self, request, context: str = ""):
        """
        Google API isteğini güvenli çalıştırma.
        • 403 (quota aşımı) → bekleme + retry
        • 429 (rate limit) → bekleme + retry
        • Timeout → retry
        """
        for attempt in range(self.MAX_RETRIES):
            try:
                # İstekler arası minimum bekleme
                await asyncio.sleep(self.API_DELAY)
                result = await asyncio.to_thread(request.execute)
                return result
            except HttpError as e:
                status = e.resp.status
                if status in (403, 429):
                    wait = self.RATE_LIMIT_DELAY * (attempt + 1)
                    logger.warning(
                        f"⚠️ YouTube API rate limit/quota ({status}) [{context}] "
                        f"(deneme {attempt+1}/{self.MAX_RETRIES}), {wait}sn bekleniyor..."
                    )
                    await asyncio.sleep(wait)
                    if attempt == self.MAX_RETRIES - 1:
                        logger.error(f"❌ YouTube API quota aşıldı [{context}], tüm denemeler tükendi.")
                        raise
                elif status >= 500:
                    wait = 10 * (attempt + 1)
                    logger.warning(f"YouTube API sunucu hatası ({status}) [{context}], {wait}sn sonra tekrar...")
                    await asyncio.sleep(wait)
                else:
                    raise
            except Exception as e:
                if attempt < self.MAX_RETRIES - 1:
                    wait = 5 * (attempt + 1)
                    logger.warning(f"YouTube API hata [{context}] (deneme {attempt+1}): {e}, {wait}sn sonra tekrar...")
                    await asyncio.sleep(wait)
                else:
                    raise
        raise RuntimeError(f"YouTube API isteği başarısız oldu [{context}]")

    async def fetch_channel_videos(self, channel_id: str, max_results: int = 50) -> list:
        """
        Kanal bazlı video çekimi — sayfalama + rate limit koruması.
        """
        try:
            all_videos = []
            page_token = None
            remaining = max_results
            pages_fetched = 0

            while remaining > 0:
                batch_size = min(remaining, 50)  # YouTube API max 50 per page
                req = self.youtube.search().list(
                    part="id",
                    channelId=channel_id,
                    maxResults=batch_size,
                    order="date",
                    type="video",
                    pageToken=page_token
                )
                res = await self._safe_execute(req, context=f"channel_search:{channel_id}")
                if not res:
                    break

                video_ids = [item["id"]["videoId"] for item in res.get("items", []) if "videoId" in item.get("id", {})]

                if video_ids:
                    stats_req = self.youtube.videos().list(part="snippet,statistics", id=",".join(video_ids))
                    stats_res = await self._safe_execute(stats_req, context=f"video_stats:{channel_id}")
                    if stats_res:
                        all_videos.extend(stats_res.get("items", []))

                remaining -= batch_size
                pages_fetched += 1
                page_token = res.get("nextPageToken")
                if not page_token:
                    break

                logger.info(f"YouTube Kanal [{channel_id}] sayfa {pages_fetched}: {len(all_videos)} video (devam ediyor...)")

            logger.info(f"✅ YouTube Kanal [{channel_id}]: {len(all_videos)} video çekildi ({pages_fetched} sayfa).")
            return all_videos
        except HttpError as e:
            if e.resp.status == 403:
                logger.error(f"❌ YouTube Quota/erişim hatası (kanal {channel_id}).")
            else:
                logger.error(f"❌ YouTube Video Hatası ({channel_id}): {e}")
            raise
        except Exception as e:
            logger.error(f"❌ YouTube Video Hatası ({channel_id}): {e}")
            raise

    async def fetch_keyword_videos(self, keyword: str, max_results: int = 100) -> list:
        """
        Anahtar kelime bazlı video arama — DERİN SAYFALAMA DESTEKLİ.
        En az 50–100 video toplayana kadar sayfalama devam eder.
        Rate limit koruması ve retry mekanizması mevcut.
        """
        try:
            all_videos = []
            seen_ids = set()
            page_token = None
            remaining = max_results
            pages_fetched = 0

            # Sadece son 30 günün videoları (eski içerikleri atla)
            thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).isoformat() + "Z"

            logger.info(f"🔎 YouTube DERİN arama: '{keyword}' (hedef: {max_results} video)")

            while remaining > 0:
                batch_size = min(remaining, 50)
                req = self.youtube.search().list(
                    part="id,snippet",
                    q=keyword,
                    maxResults=batch_size,
                    order="date",
                    type="video",
                    publishedAfter=thirty_days_ago,
                    pageToken=page_token
                )
                res = await self._safe_execute(req, context=f"keyword_search:{keyword}")
                if not res:
                    break

                video_ids = []
                for item in res.get("items", []):
                    vid_id = item.get("id", {}).get("videoId")
                    if vid_id and vid_id not in seen_ids:
                        video_ids.append(vid_id)
                        seen_ids.add(vid_id)

                if video_ids:
                    stats_req = self.youtube.videos().list(part="snippet,statistics", id=",".join(video_ids))
                    stats_res = await self._safe_execute(stats_req, context=f"video_stats:{keyword}")
                    if stats_res:
                        all_videos.extend(stats_res.get("items", []))

                remaining -= batch_size
                pages_fetched += 1
                page_token = res.get("nextPageToken")

                logger.info(
                    f"YouTube Arama [{keyword}] sayfa {pages_fetched}: "
                    f"{len(video_ids)} yeni video → toplam {len(all_videos)}"
                )

                if not page_token:
                    break

            logger.info(f"✅ YouTube Arama [{keyword}]: {len(all_videos)} video çekildi ({pages_fetched} sayfa).")
            return all_videos
        except HttpError as e:
            if e.resp.status == 403:
                logger.error(f"❌ YouTube Quota/erişim hatası (arama: {keyword}).")
                raise
            logger.error(f"❌ YouTube Keyword Hatası ({keyword}): {e}")
            raise
        except Exception as e:
            logger.error(f"❌ YouTube Keyword Hatası ({keyword}): {e}")
            raise

    async def fetch_video_comments(self, video_id: str, max_results: int = 100) -> list:
        """
        Video yorumları — DERİN SAYFALAMA DESTEKLİ.
        max_results kadar yorum çeker (her sayfada 100'e kadar).
        Rate limit koruması ve retry mekanizması mevcut.
        """
        try:
            all_comments = []
            page_token = None
            remaining = max_results
            pages_fetched = 0

            while remaining > 0:
                batch_size = min(remaining, 100)  # commentThreads API max 100
                req = self.youtube.commentThreads().list(
                    part="snippet,replies",
                    videoId=video_id,
                    maxResults=batch_size,
                    order="relevance",
                    pageToken=page_token
                )
                res = await self._safe_execute(req, context=f"comments:{video_id}")
                if not res:
                    break

                items = res.get("items", [])
                all_comments.extend(items)

                remaining -= len(items)
                pages_fetched += 1
                page_token = res.get("nextPageToken")
                if not page_token or not items:
                    break

            return all_comments
        except HttpError as e:
            if e.resp.status == 403:
                logger.error(f"❌ YouTube yorum erişim/quota hatası: {video_id}")
            else:
                logger.error(f"❌ YouTube Yorum Hatası ({video_id}): {e}")
            raise
        except Exception as e:
            logger.error(f"❌ YouTube Yorum Hatası ({video_id}): {e}")
            raise