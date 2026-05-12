import os
import asyncio
import random
import httpx
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from loguru import logger


class XProvider(ABC):
    @abstractmethod
    async def fetch_trends(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def fetch_mentions(self, target: str) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def fetch_keyword_posts(self, keyword: str, limit: int = 70) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def fetch_top_tweets_shallow(
        self, keyword: str, limit: int = 6, search_type: str = "Top"
    ) -> List[Dict[str, Any]]:
        """Tek sayfa arama — trend/gündem maliyet kalkanı (derin sayfalama yok)."""
        pass

    @abstractmethod
    async def fetch_tweet_replies(self, tweet_id: str) -> List[Dict[str, Any]]:
        """Bir tweet'in altındaki yorumları/yanıtları çeker."""
        pass


class RapidXProvider(XProvider):
    """
    Twitter/X veri çekici — twitter-api45 RapidAPI üzerinden.
    • Her API çağrısı arasında 3sn bekleme (rate-limit koruması)
    • Cursor tabanlı sayfalama (pagination) — güçlendirilmiş
    • Reklam/Promoted filtreleme
    • Hit (en çok etkileşim) yorumlarını önceliklendirme
    • seen_ids ile duplicate engellemesi
    """

    # Her API isteği arasındaki bekleme süresi (saniye)
    API_DELAY = 3.0
    # Rate limit sonrası bekleme (saniye)
    RATE_LIMIT_DELAY = 15.0
    # API istek timeout (saniye)
    REQUEST_TIMEOUT = 45.0
    # Maksimum deneme (5xx geçici RapidAPI/upstream kesintileri için birkaç tur)
    MAX_RETRIES = 5

    def __init__(self):
        self.api_key = os.getenv("RAPIDAPI_KEY") or os.getenv("RAPID_API_KEY")
        self.api_host = os.getenv("RAPIDAPI_HOST") or os.getenv("RAPID_API_HOST", "twitter-api45.p.rapidapi.com")
        if not self.api_key:
            raise RuntimeError("RAPIDAPI_KEY/RAPID_API_KEY zorunludur; X verisi için mock fallback yoktur.")
        self.base_url = f"https://{self.api_host}"

    def _get_headers(self):
        return {
            "x-rapidapi-key": self.api_key,
            "x-rapidapi-host": self.api_host
        }

    # ------------------------------------------------------------------ #
    #  YARDIMCI: Güvenli tarih parse
    # ------------------------------------------------------------------ #
    @staticmethod
    def _safe_parse_date(raw_date: str) -> str:
        """Twitter'ın farklı tarih formatlarını ISO'ya çevirir."""
        if not raw_date:
            return datetime.utcnow().isoformat()
        try:
            import email.utils
            dt = email.utils.parsedate_to_datetime(raw_date)
            return dt.replace(tzinfo=None).isoformat()
        except Exception:
            pass
        try:
            return datetime.fromisoformat(raw_date.replace('Z', '+00:00')).replace(tzinfo=None).isoformat()
        except Exception:
            pass
        return datetime.utcnow().isoformat()

    # ------------------------------------------------------------------ #
    #  YARDIMCI: Güvenli yazar adı parse
    # ------------------------------------------------------------------ #
    @staticmethod
    def _safe_author(tweet_data: dict) -> str:
        """Yazar alanını her zaman string olarak döndürür."""
        for key in ("screen_name", "user_screen_name", "username"):
            val = tweet_data.get(key)
            if val and isinstance(val, str):
                return val

        author_raw = tweet_data.get("author")
        if isinstance(author_raw, dict):
            return author_raw.get("screen_name") or author_raw.get("name") or "Unknown"
        if isinstance(author_raw, str) and author_raw:
            return author_raw

        user_obj = tweet_data.get("user_info") or tweet_data.get("user") or {}
        if isinstance(user_obj, dict):
            return user_obj.get("screen_name") or user_obj.get("name") or "Unknown"

        return "Unknown"

    @staticmethod
    def _safe_int(value) -> int:
        try:
            return int(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _extract_account_metrics(cls, tweet_data: dict) -> Dict[str, Any]:
        """Tweet payload'ındaki olası kullanıcı metriklerini normalize eder."""
        candidates = [
            tweet_data.get("author"),
            tweet_data.get("user_info"),
            tweet_data.get("user"),
            tweet_data.get("core", {}).get("user_results", {}).get("result", {}),
        ]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            legacy = candidate.get("legacy") if isinstance(candidate.get("legacy"), dict) else {}
            data = {**candidate, **legacy}
            followers = cls._safe_int(
                data.get("followers_count") or data.get("follower_count") or data.get("followers")
            )
            following = cls._safe_int(
                data.get("friends_count") or data.get("following_count") or data.get("following")
            )
            tweet_count = cls._safe_int(
                data.get("statuses_count") or data.get("tweet_count") or data.get("tweets_count")
            )
            created_at = (
                data.get("created_at") or
                data.get("createdAt") or
                data.get("account_created_at")
            )
            if any([followers, following, tweet_count, created_at]):
                return {
                    "user_id": data.get("id_str") or data.get("id"),
                    "screen_name": data.get("screen_name") or data.get("username"),
                    "account_created_at": created_at,
                    "followers_count": followers,
                    "following_count": following,
                    "tweet_count": tweet_count,
                }

        return {}

    # ------------------------------------------------------------------ #
    #  TEK TWEET PARSE
    # ------------------------------------------------------------------ #
    def _parse_tweet(self, tweet_data: dict, keyword: str = "", target_type: str = "twitter_trend") -> Optional[Dict[str, Any]]:
        """Tek bir tweet nesnesini standart formata dönüştürür."""
        if not isinstance(tweet_data, dict):
            return None

        tweet_id = (
            tweet_data.get("tweet_id") or
            tweet_data.get("tweetId") or
            tweet_data.get("rest_id") or
            tweet_data.get("id") or
            tweet_data.get("id_str")
        )

        text = (
            tweet_data.get("text") or
            tweet_data.get("full_text") or
            tweet_data.get("content") or ""
        )

        if not text and "legacy" in tweet_data:
            legacy = tweet_data["legacy"]
            text = legacy.get("full_text") or legacy.get("text") or ""
            tweet_id = tweet_id or legacy.get("id_str")

        if not text or not tweet_id:
            return None

        author = self._safe_author(tweet_data)
        published_at = self._safe_parse_date(
            tweet_data.get("created_at") or tweet_data.get("createdAt")
        )

        # Etkileşim ve hesap metrikleri: sıralama, reply taraması ve bot skoru için kullanılır.
        likes = tweet_data.get("favorite_count") or tweet_data.get("likes") or 0
        retweets = tweet_data.get("retweet_count") or tweet_data.get("retweets") or 0
        replies_count = tweet_data.get("reply_count") or tweet_data.get("replies") or 0
        account_metrics = self._extract_account_metrics(tweet_data)
        if isinstance(likes, str):
            try: likes = int(likes)
            except: likes = 0
        if isinstance(retweets, str):
            try: retweets = int(retweets)
            except: retweets = 0

        return {
            "external_id": str(tweet_id),
            "text": text,
            "author": str(author),  # Garantili string
            "published_at": published_at,
            "target_type": target_type,
            "target_name": keyword,
            "_likes": int(likes) if likes else 0,
            "_retweets": int(retweets) if retweets else 0,
            "_replies": int(replies_count) if replies_count else 0,
            "_account_metrics": account_metrics,
        }

    # ------------------------------------------------------------------ #
    #  API İSTEK MOTORU (Rate-limit uyumlu, güçlendirilmiş)
    # ------------------------------------------------------------------ #
    async def _api_request(self, endpoint: str, params: dict) -> Optional[dict]:
        """
        Merkezi API istek fonksiyonu.
        • 429 alırsa kademeli bekleme ile yeniden dener (3 deneme).
        • Timeout ve bağlantı hatalarında retry.
        • Diğer hatalarda exception fırlatır; production'da sahte/boş başarı yoktur.
        """
        for attempt in range(self.MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT) as client:
                    response = await client.get(
                        f"{self.base_url}/{endpoint}",
                        params=params,
                        headers=self._get_headers()
                    )
                    logger.info(f"RapidX API [{endpoint}] Status: {response.status_code} (deneme {attempt+1}/{self.MAX_RETRIES})")

                    if response.status_code == 429:
                        wait = self.RATE_LIMIT_DELAY * (attempt + 1)
                        logger.warning(f"RapidX: Rate limit! {wait}sn bekleniyor... (deneme {attempt+1}/{self.MAX_RETRIES})")
                        await asyncio.sleep(wait)
                        continue

                    if response.status_code >= 500:
                        # 502 Bad Gateway / 503: RapidAPI veya twitter-api45 tarafında geçici yük,
                        # proxy timeout veya bakım. Uygulama hatası değildir.
                        ra_hdr = response.headers.get("Retry-After")
                        if ra_hdr:
                            try:
                                wait = float(ra_hdr.strip())
                            except ValueError:
                                wait = min(
                                    120.0,
                                    8 * (2**attempt) + random.uniform(0, 2.5),
                                )
                        else:
                            wait = min(
                                120.0,
                                8 * (2**attempt) + random.uniform(0, 2.5),
                            )
                        wait = max(3.0, min(120.0, wait))
                        body_hint = (response.text or "")[:180].replace("\n", " ").strip()
                        logger.warning(
                            f"RapidX: Upstream HTTP {response.status_code} [{endpoint}] — "
                            f"geçici sunucu veya geçit hatası. {wait:.1f}s sonra tekrar "
                            f"(deneme {attempt + 1}/{self.MAX_RETRIES})."
                        )
                        if body_hint:
                            logger.debug(f"RapidX yanıt özeti: {body_hint}")
                        await asyncio.sleep(wait)
                        continue

                    if response.status_code >= 400:
                        logger.error(f"RapidX API hatası [{endpoint}]: HTTP {response.status_code}")
                        raise RuntimeError(f"RapidX API hatası [{endpoint}]: HTTP {response.status_code} - {response.text[:300]}")

                    data = response.json()
                    logger.debug(f"RapidX [{endpoint}] yanıt anahtarları: {list(data.keys()) if isinstance(data, dict) else type(data)}")
                    return data

            except httpx.TimeoutException:
                wait = 5 * (attempt + 1)
                logger.warning(f"RapidX API timeout [{endpoint}] (deneme {attempt+1}/{self.MAX_RETRIES}), {wait}sn bekleniyor...")
                await asyncio.sleep(wait)
            except httpx.ConnectError:
                wait = 5 * (attempt + 1)
                logger.warning(f"RapidX API bağlantı hatası [{endpoint}] (deneme {attempt+1}/{self.MAX_RETRIES}), {wait}sn bekleniyor...")
                await asyncio.sleep(wait)
            except Exception as e:
                logger.error(f"RapidX API beklenmeyen hata [{endpoint}] (deneme {attempt+1}/{self.MAX_RETRIES}): {e}")
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(5)

        logger.error(f"RapidX API [{endpoint}]: Tüm denemeler tükendi!")
        raise RuntimeError(f"RapidX API [{endpoint}]: Tüm denemeler tükendi.")

    # ------------------------------------------------------------------ #
    #  YANIT PARSE (GÜÇLENDİRİLMİŞ CURSOR ALGILAMA)
    # ------------------------------------------------------------------ #
    def _extract_tweets_from_response(self, data: dict) -> tuple:
        """API yanıtından tweet listesini ve sonraki sayfa cursor'ını çıkarır."""
        tweets = []
        cursor = None

        if not isinstance(data, dict):
            if isinstance(data, list):
                return data, None
            return [], None

        # Tweet listesini bul — genişletilmiş anahtar arama
        for key in ["timeline", "search", "results", "tweets", "data", "entries", "statuses", "list"]:
            if key in data and isinstance(data[key], list):
                tweets = data[key]
                break

        if not tweets:
            if isinstance(data.get("globalObjects"), dict):
                tweets_obj = data["globalObjects"].get("tweets", {})
                tweets = list(tweets_obj.values()) if isinstance(tweets_obj, dict) else []

        # İç içe tweet yapılarını düzleştir (bazı API yanıtları nested gelir)
        if not tweets:
            for key in data:
                val = data[key]
                if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                    # İçinde text/tweet_id olan bir liste bulduk
                    sample = val[0]
                    if any(k in sample for k in ["text", "full_text", "tweet_id", "tweetId", "content"]):
                        tweets = val
                        break

        # Cursor bulma — genişletilmiş arama
        cursor = (
            data.get("next_cursor") or
            data.get("cursor") or
            data.get("cursor_bottom") or
            data.get("next") or
            data.get("continuation_token") or
            data.get("scroll_cursor") or
            data.get("bottom_cursor")
        )

        # Bazı API'ler cursor'ı iç içe yapıda tutar
        if not cursor and "meta" in data and isinstance(data["meta"], dict):
            cursor = data["meta"].get("next_cursor") or data["meta"].get("next_token")

        return tweets, cursor

    # ------------------------------------------------------------------ #
    #  TRENDLER
    # ------------------------------------------------------------------ #
    async def fetch_trends(self) -> List[Dict[str, Any]]:
        """Türkiye trendlerini çeker."""
        logger.info("RapidXProvider: X Trendleri çekiliyor...")
        try:
            data = await self._api_request("trends.php", {"country": "turkey"})
            trends = data if isinstance(data, list) else data.get("trends", [])
            results = []
            for i, trend in enumerate(trends[:35]):
                name = trend.get("name") or trend.get("trend") or f"Trend_{i}"
                results.append({
                    "external_id": f"trend_{name}_{datetime.utcnow().strftime('%Y%m%d')}",
                    "text": f"Trend: {name} — {trend.get('tweet_count', 'N/A')} tweet",
                    "author": "X_Trends",
                    "published_at": datetime.utcnow().isoformat(),
                    "target_type": "twitter_trend",
                    "target_name": name
                })
            return results
        except Exception as e:
            logger.error(f"RapidX fetch_trends hatası: {e}")
            raise

    async def fetch_top_tweets_shallow(
        self, keyword: str, limit: int = 6, search_type: str = "Top"
    ) -> List[Dict[str, Any]]:
        """
        Gündem/trend örnekleme: yalnızca TEK search.php sayfası, çoklu cursor turu yok.
        Reply / conversation API çağrısı yapmaz (API maliyet kalkanı).
        """
        # Üst sınır ~%30 kısılmış (önceki 15 → 10); API maliyeti ve hacim düşer
        cap = max(1, min(int(limit), 10))
        if not (keyword or "").strip():
            return []
        kw = keyword.strip()
        logger.info(
            f"🔎 RapidX: '{kw}' yüzeysel arama (max {cap} tweet, tek sayfa, {search_type})..."
        )
        try:
            out: List[Dict[str, Any]] = []
            seen: set = set()
            attempt_types: List[str] = [search_type]
            if search_type == "Top":
                attempt_types.append("Latest")

            for i, attempt_type in enumerate(attempt_types):
                if i > 0:
                    logger.warning(f"RapidX: önceki arama boş, '{kw}' için {attempt_type} deneniyor.")
                    await asyncio.sleep(self.API_DELAY)
                params = {"query": kw, "search_type": attempt_type}
                data = await self._api_request("search.php", params)
                raw_tweets, _ = self._extract_tweets_from_response(data)
                for tweet in raw_tweets:
                    parsed = self._parse_tweet(tweet, keyword=kw, target_type="twitter_trend")
                    if not parsed:
                        continue
                    eid = parsed.get("external_id")
                    if not eid or eid in seen:
                        continue
                    seen.add(eid)
                    out.append(parsed)
                    if len(out) >= cap:
                        break
                if out:
                    break

            logger.info(f"✅ RapidX '{kw}' yüzeysel: {len(out)} tweet")
            return out
        except Exception as e:
            logger.error(f"RapidX fetch_top_tweets_shallow hatası ({kw}): {e}")
            raise

    # ------------------------------------------------------------------ #
    #  TWEET YORUMLARI (Hit odaklı)
    # ------------------------------------------------------------------ #
    async def fetch_tweet_replies(self, tweet_id: str) -> List[Dict[str, Any]]:
        """Bir tweet'in altındaki en etkili yorumları çeker."""
        try:
            data = await self._api_request("tweet_thread.php", {"id": str(tweet_id)})

            raw_items = []
            if isinstance(data, list):
                raw_items = data
            elif isinstance(data, dict):
                raw_items = data.get("thread", data.get("replies", data.get("conversation", [])))
                if not isinstance(raw_items, list):
                    raw_items = []

            replies = []
            for item in raw_items:
                parsed = self._parse_tweet(item, keyword=str(tweet_id), target_type="twitter_reply")
                if parsed and parsed["external_id"] != str(tweet_id):
                    replies.append(parsed)

            # Hit sıralaması: Beğeni + Retweet'e göre
            replies.sort(key=lambda x: x.get("_likes", 0) + x.get("_retweets", 0), reverse=True)

            return replies[:70]  # Derin araştırma / reply — hacim ~%30 düşük
        except Exception as e:
            logger.error(f"RapidX fetch_tweet_replies hatası (tweet {tweet_id}): {e}")
            raise

    # ------------------------------------------------------------------ #
    #  HESAP TAKİBİ: Timeline + Yorumlar (ANA FONKSİYON)
    # ------------------------------------------------------------------ #
    async def fetch_mentions(self, target: str) -> List[Dict[str, Any]]:
        """
        Bir kullanıcının son tweetlerini çeker, her tweet için
        altındaki en etkili (hit) yorumları toplar.
        Kaynaklar arasında asyncio.sleep ile rate-limit koruması sağlar.
        """
        # URL / @ temizliği
        screen_name = target.strip()
        if "/" in screen_name:
            screen_name = screen_name.split("/")[-1].split("?")[0]
        screen_name = screen_name.replace("@", "").strip()

        if not screen_name:
            logger.warning(f"RapidX: Boş kullanıcı adı, atlanıyor: {target}")
            return []

        logger.info(f"🔍 RapidX: @{screen_name} taranıyor (timeline + yorumlar)...")

        try:
            data = await self._api_request("timeline.php", {"screenname": screen_name})

            raw_tweets, _ = self._extract_tweets_from_response(data)
            logger.info(f"RapidX @{screen_name}: Timeline'da {len(raw_tweets)} ham tweet bulundu.")

            if not raw_tweets:
                return []

            all_posts = []
            seen_ids = set()
            seven_days_ago = datetime.utcnow() - timedelta(days=7)
            tweets_with_replies = 0
            reply_requests_sent = 0

            for tweet in raw_tweets[:105]:  # Hesap timeline — ~%30 daha az tweet
                parsed = self._parse_tweet(tweet, keyword=screen_name, target_type="twitter_self")
                if not parsed:
                    continue

                # Duplicate engelleme
                if parsed["external_id"] in seen_ids:
                    continue
                seen_ids.add(parsed["external_id"])

                # 7 gün filtresi
                try:
                    pub_date = datetime.fromisoformat(parsed["published_at"]).replace(tzinfo=None)
                    if pub_date < seven_days_ago:
                        continue
                except Exception:
                    pass

                all_posts.append(parsed)

                if parsed.get("_replies", 0) <= 0:
                    logger.debug(f"  └── Tweet {parsed['external_id'][:12]}... yorum yok, reply API atlandı")
                    continue

                # Sadece yorumu olan tweetler için reply API çağrısı yap.
                if reply_requests_sent > 0:
                    await asyncio.sleep(self.API_DELAY)

                try:
                    reply_requests_sent += 1
                    replies = await self.fetch_tweet_replies(parsed["external_id"])
                    if replies:
                        tweets_with_replies += 1
                        for r in replies:
                            if r["external_id"] not in seen_ids:
                                r["target_type"] = "twitter_reply"
                                r["target_name"] = f"@{screen_name}"
                                all_posts.append(r)
                                seen_ids.add(r["external_id"])
                        logger.info(f"  └── Tweet {parsed['external_id'][:12]}... → {len(replies)} yorum çekildi")
                except Exception as e:
                    logger.warning(f"  └── Tweet {parsed['external_id'][:12]}... yorum hatası: {e}")
                    raise

            logger.info(
                f"✅ RapidX @{screen_name}: TOPLAM {len(all_posts)} veri çekildi "
                f"({tweets_with_replies} tweet'ten yorum alındı)"
            )
            return all_posts

        except Exception as e:
            logger.error(f"RapidX fetch_mentions GENEL hatası (@{screen_name}): {e}")
            raise

    # ------------------------------------------------------------------ #
    #  ANAHTAR KELİME ARAMASI (GÜÇLENDİRİLMİŞ DEEPScan Pagination)
    # ------------------------------------------------------------------ #
    async def fetch_keyword_posts(self, keyword: str, limit: int = 70) -> List[Dict[str, Any]]:
        """
        Anahtar kelime araması — GÜÇLENDİRİLMİŞ CURSOR TABANLI SAYFALAMA.
        • min 50–100 içerik toplayana kadar sayfalama devam eder
        • seen_ids ile her sayfada duplicate engellenir
        • Ardışık boş sayfa koruması (2 boş sayfa → dur)
        • Her sayfa arasında rate-limit bekleme süresi
        """
        logger.info(f"🔎 RapidX: '{keyword}' DERİN araması başlatılıyor (hedef: {limit})...")

        all_posts = []
        seen_ids = set()
        cursor = None
        # Daha fazla sayfa izni — API'den yeterli veri çekmek için
        max_pages = max(limit // 10, 10)  # En az 10 sayfa
        pages_fetched = 0
        empty_pages_streak = 0  # Ardışık boş sayfa sayacı
        search_type = "Latest"

        thirty_days_ago = datetime.utcnow() - timedelta(days=30)

        while pages_fetched < max_pages and len(all_posts) < limit:
            try:
                params = {"query": keyword, "search_type": search_type}
                if cursor:
                    params["cursor"] = cursor

                data = await self._api_request("search.php", params)
                raw_tweets, next_cursor = self._extract_tweets_from_response(data)

                # Latest boşsa aynı gerçek API üzerinde Top aramasına geç.
                if not raw_tweets and pages_fetched == 0 and search_type == "Latest":
                    logger.warning(f"RapidX: Latest boş, '{keyword}' için Top'a geçiliyor.")
                    search_type = "Top"
                    await asyncio.sleep(self.API_DELAY)
                    continue

                if not raw_tweets:
                    empty_pages_streak += 1
                    logger.info(f"RapidX: Sayfa {pages_fetched + 1}'de veri kalmadı (ardışık boş: {empty_pages_streak}).")
                    if empty_pages_streak >= 2:
                        break
                    # Cursor olsa bile veri yoksa devam et, cursor değişebilir
                    if next_cursor and next_cursor != cursor:
                        cursor = next_cursor
                        await asyncio.sleep(self.API_DELAY)
                        continue
                    break

                # Boş sayfa sayacını sıfırla
                empty_pages_streak = 0

                new_in_page = 0
                for tweet in raw_tweets:
                    parsed = self._parse_tweet(tweet, keyword=keyword, target_type="twitter_trend")
                    if not parsed:
                        continue

                    # Duplicate kontrolü
                    if parsed["external_id"] in seen_ids:
                        continue
                    seen_ids.add(parsed["external_id"])

                    # Tarih filtresi
                    try:
                        pub_date = datetime.fromisoformat(parsed["published_at"]).replace(tzinfo=None)
                        if pub_date >= thirty_days_ago:
                            all_posts.append(parsed)
                            new_in_page += 1
                    except Exception:
                        all_posts.append(parsed)
                        new_in_page += 1

                pages_fetched += 1
                logger.info(
                    f"RapidX sayfa {pages_fetched}/{max_pages}: "
                    f"{len(raw_tweets)} ham tweet, {new_in_page} yeni geçerli → "
                    f"toplam {len(all_posts)}/{limit}"
                )

                # Cursor kontrolü: Yeni cursor yoksa veya aynıysa dur
                if not next_cursor or next_cursor == cursor:
                    logger.info(f"RapidX: Cursor yok veya değişmedi, sayfalama bitiyor.")
                    break
                cursor = next_cursor

                # ↓↓↓ SAYFALAR ARASI RATE-LIMIT BEKLEMESİ ↓↓↓
                await asyncio.sleep(self.API_DELAY)

            except Exception as e:
                logger.error(f"RapidX arama hatası sayfa {pages_fetched}: {e}")
                raise

        logger.info(
            f"✅ RapidX '{keyword}' DEEP-SCAN TAMAMLANDI: "
            f"{len(all_posts)} tweet ({pages_fetched} sayfa tarandı, {len(seen_ids)} benzersiz ID)"
        )
        return all_posts[:limit]