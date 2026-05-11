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
