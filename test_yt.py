import asyncio
import os
from dotenv import load_dotenv
load_dotenv()
from app.providers.youtube_provider import YouTubeProvider

async def main():
    provider = YouTubeProvider()
    videos = await provider.fetch_keyword_videos("fenerbahçe", max_results=1)
    if not videos:
        print("No videos")
        return
    vid_id = videos[0]["id"]
    if isinstance(vid_id, dict): vid_id = vid_id["videoId"]
    print("Video:", videos[0]["snippet"]["title"])
    comments = await provider.fetch_video_comments(vid_id, max_results=2)
    for c in comments:
        snippet = c["snippet"]["topLevelComment"]["snippet"]
        print(f"Comment by {snippet['authorDisplayName']}: {snippet['textDisplay']} (Likes: {snippet['likeCount']})")

asyncio.run(main())
