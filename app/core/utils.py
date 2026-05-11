import re
from datetime import datetime
from typing import List, Dict, Any

def pre_filter_content(text: str) -> bool:
    """
    Yapay zeka kotasını korumak için ön filtreleme.
    True dönerse içerik analiz edilebilir.
    False dönerse içerik analiz için gereksizdir.
    """
    # Siyasi mesajlar kısa olabilir (örn: "İstifa et!", "Bravo"), bu yüzden limiti düşürüyoruz.
    if not text or len(text.strip()) < 10:
        return False
    
    # Sadece URL içeriyorsa atla
    url_pattern = r'^https?://\S+$'
    if re.match(url_pattern, text.strip()):
        return False

    return True

def extract_youtube_comments_as_articles(video, comments, source_id, domain='general'):
    articles = []
    vid_id = video["id"] if isinstance(video.get("id"), str) else video["id"]["videoId"]
    vid_title = video["snippet"]["title"]
    
    for c in comments:
        try:
            snippet = c["snippet"]["topLevelComment"]["snippet"]
            comment_text = snippet["textDisplay"]
            
            # Yorumdaki zaman damgalarını çıkar (örn: 1:20 veya 12:34:56)
            timestamps = re.findall(r'\b\d{1,2}:\d{2}(?::\d{2})?\b', comment_text)
            time_context = f" | Video Anı: {', '.join(timestamps)}" if timestamps else ""
            like_count = snippet.get("likeCount", 0)
            
            # Anti-Poisoning Bağlam Zırhı
            full_text = f"[Video: {vid_title} | Bağlam: {domain} | Hit/Beğeni: {like_count}{time_context}]\nYorum: {comment_text}"
            
            pub_at_str = snippet["publishedAt"]
            if pub_at_str.endswith('Z'): pub_at_str = pub_at_str[:-1] + '+00:00'
            
            articles.append({
                "source_id": source_id,
                "platform": "youtube_comment",
                "external_id": c["id"],
                "author_name": snippet["authorDisplayName"],
                "published_at": datetime.fromisoformat(pub_at_str).replace(tzinfo=None),
                "text": full_text,
                "content_type": "comment",
                "url": f"https://youtube.com/watch?v={vid_id}&lc={c['id']}",
                "domain": domain,
                "raw_json": c
            })
        except Exception:
            pass
    return articles


def _safe_int(value, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _safe_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        import email.utils
        parsed = email.utils.parsedate_to_datetime(str(value))
        return parsed.replace(tzinfo=None)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def calculate_twitter_bot_likelihood(raw_json: Dict[str, Any] | None) -> float:
    """Twitter hesap metriklerinden 0.0-1.0 arası bot olasılığı üretir."""
    if not isinstance(raw_json, dict):
        return 0.0

    account = raw_json.get("account_metrics") or raw_json.get("account") or {}
    if not isinstance(account, dict):
        return 0.0

    followers = _safe_int(account.get("followers_count"))
    following = _safe_int(account.get("following_count") or account.get("friends_count"))
    tweet_count = _safe_int(account.get("tweet_count") or account.get("statuses_count"))
    created_at = _safe_date(account.get("account_created_at") or account.get("created_at"))

    score = 0.0
    if created_at:
        age_days = max(1, (datetime.utcnow() - created_at).days)
        tweets_per_day = tweet_count / age_days if tweet_count else 0
        if age_days < 30:
            score += 0.30
        elif age_days < 180:
            score += 0.18
        if tweets_per_day > 80:
            score += 0.30
        elif tweets_per_day > 30:
            score += 0.22
        elif tweets_per_day > 10:
            score += 0.12

    ratio = following / max(followers, 1)
    if following > 200 and followers < 25:
        score += 0.25
    elif ratio > 10:
        score += 0.25
    elif ratio > 5:
        score += 0.18
    elif ratio > 2.5:
        score += 0.10

    if followers == 0 and following > 50:
        score += 0.15

    return round(max(0.0, min(score, 1.0)), 3)


def twitter_bot_signal_summary(raw_json: Dict[str, Any] | None) -> Dict[str, Any]:
    """Dashboard ve raporlar için bot skorunu açıklayan temel sinyalleri döndürür."""
    account = (raw_json or {}).get("account_metrics") or {}
    followers = _safe_int(account.get("followers_count"))
    following = _safe_int(account.get("following_count") or account.get("friends_count"))
    tweet_count = _safe_int(account.get("tweet_count") or account.get("statuses_count"))
    created_at = _safe_date(account.get("account_created_at") or account.get("created_at"))
    age_days = max(1, (datetime.utcnow() - created_at).days) if created_at else None
    tweets_per_day = round(tweet_count / age_days, 2) if age_days and tweet_count else 0
    return {
        "score": calculate_twitter_bot_likelihood(raw_json),
        "followers": followers,
        "following": following,
        "ratio": round(following / max(followers, 1), 2) if following else 0,
        "account_age_days": age_days,
        "tweets_per_day": tweets_per_day,
    }
