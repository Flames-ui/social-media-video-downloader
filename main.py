import os
import re
import unicodedata
import json
import asyncio
import uuid  # ← FIXED: Moved to top of file
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import httpx
from dotenv import load_dotenv
from typing import Optional, List

app = FastAPI()

load_dotenv()

app.add_middleware(CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# CONFIGURATION
# ============================================
DATA_DIR = Path("/tmp/reelsdown_data")
DATA_DIR.mkdir(exist_ok=True)

FETCH_STATE_FILE = DATA_DIR / "fetch_state.json"

def init_json_file(filepath: Path, default_data: dict):
    if not filepath.exists():
        with open(filepath, 'w') as f:
            json.dump(default_data, f)

init_json_file(FETCH_STATE_FILE, {
    "last_fetch": None,
    "total_fetched": 0,
    "wwe_count": 0,
    "aew_count": 0
})

# ============================================
# YOUTUBE RSS FEEDS (100% RELIABLE - NEVER BLOCKED)
# ============================================
WWE_RSS_FEEDS = [
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCJ5v_MCY6GNUBTO8-D3XoAg",  # WWE
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCFgpoFswJFyJCXzB4L3VQ_w",  # WWE on FOX
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCjC7hBxJC6k9YlNhY6Yy4ZQ",  # WWE NXT
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCaiNqY7UhE4jdpB6zrHkTOg",  # WWE Raw
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCzFdx53syVlYlZZL2zVhROg",  # WWE SmackDown
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCtTMN8Gg7SHM0IrnCJZJgYg",  # WWE WrestleMania
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCz3H0IC_kU5V3B4cV1V1y5g",  # WWE Vault
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCVjYtUoy3Fy7TaZY4iFiG1w",  # WWE Music
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCrD2TNR6cH9nB2g3nGVEp5A",  # Wrestlelamia
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCtC0NgR0wG3m3pH8q5LwZ5w",  # WhatCulture Wrestling
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCAeEAtK6hvmVnZxwMPhHkBg",  # Cultaholic Wrestling
]

AEW_RSS_FEEDS = [
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCFN4JkGP_bVhAdBsoV9xftA",  # AEW
]

# ============================================
# HELPER FUNCTIONS
# ============================================
def clean_filename(filename: str) -> str:
    """Remove emojis and special characters from filename"""
    if not filename:
        return "video"
    filename = unicodedata.normalize('NFKD', str(filename))
    filename = filename.encode('ASCII', 'ignore').decode('ASCII')
    filename = re.sub(r'[^\w\s-]', '', filename)
    filename = re.sub(r'[-\s]+', '_', filename)
    return filename.strip('_')[:50]

def extract_video_id_from_url(url: str) -> Optional[str]:
    """Extract YouTube video ID from URL"""
    if 'v=' in url:
        return url.split('v=')[1].split('&')[0]
    elif '/shorts/' in url:
        return url.split('/shorts/')[1].split('?')[0]
    elif '/embed/' in url:
        return url.split('/embed/')[1].split('?')[0]
    elif 'youtu.be/' in url:
        return url.split('youtu.be/')[1].split('?')[0]
    return None

def extract_wrestler_from_title(title: str, uploader: str) -> str:
    """Extract wrestler name from title"""
    known_wrestlers = [
        "Oba Femi", "Roman Reigns", "Cody Rhodes", "Rhea Ripley", "Bianca Belair",
        "Seth Rollins", "LA Knight", "Logan Paul", "Bayley", "Iyo Sky",
        "Sami Zayn", "Kevin Owens", "Drew McIntyre", "CM Punk", "Darby Allin", "MJF",
        "Toni Storm", "Will Ospreay", "Kenny Omega", "The Rock", "John Cena",
        "Brock Lesnar", "Triple H", "Shawn Michaels", "Undertaker", "Stone Cold",
        "Randy Orton", "AJ Styles", "Finn Balor", "Becky Lynch", "Charlotte Flair"
    ]
    for wrestler in known_wrestlers:
        if wrestler.lower() in title.lower() or wrestler.lower() in uploader.lower():
            return wrestler
    return uploader

# ============================================
# YOUTUBE RSS FETCHING (RAW DATA ONLY)
# ============================================
async def fetch_single_rss(feed_url: str, platform: str) -> List[dict]:
    """Fetch raw videos from a single YouTube RSS feed"""
    videos = []
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(feed_url)
            response.raise_for_status()
            
            root = ET.fromstring(response.text)
            namespaces = {
                'atom': 'http://www.w3.org/2005/Atom',
                'media': 'http://search.yahoo.com/mrss/',
                'yt': 'http://www.youtube.com/xml/schemas/2015'
            }
            
            for entry in root.findall('atom:entry', namespaces):
                try:
                    # Extract video URL
                    video_url = None
                    for link in entry.findall('atom:link', namespaces):
                        if link.get('rel') == 'alternate':
                            video_url = link.get('href')
                            break
                    
                    if not video_url:
                        continue
                    
                    video_id = extract_video_id_from_url(video_url)
                    if not video_id:
                        continue
                    
                    yt_id = f"yt-{video_id}"
                    
                    # Extract metadata
                    title = entry.find('atom:title', namespaces).text if entry.find('atom:title', namespaces) is not None else "Wrestling Video"
                    published = entry.find('atom:published', namespaces).text if entry.find('atom:published', namespaces) is not None else datetime.now().isoformat()
                    
                    # Get author/uploader
                    author_elem = entry.find('atom:author/atom:name', namespaces)
                    uploader = author_elem.text if author_elem is not None else "WWE"
                    
                    # Get thumbnail
                    media_group = entry.find('media:group', namespaces)
                    thumbnail = ""
                    if media_group is not None:
                        thumb_elem = media_group.find('media:thumbnail', namespaces)
                        if thumb_elem is not None:
                            thumbnail = thumb_elem.get('url', '')
                    
                    # Get description (original - Lovable will AI rewrite)
                    description_elem = entry.find('media:description', namespaces)
                    original_description = description_elem.text if description_elem is not None else ""
                    
                    # Get duration
                    duration_elem = entry.find('media:duration', namespaces)
                    duration = int(duration_elem.text) if duration_elem is not None and duration_elem.text else 0
                    
                    # Get view count
                    stats = entry.find('media:statistics', namespaces)
                    view_count = int(stats.get('views', 0)) if stats is not None else 0
                    
                    # Get like count
                    like_count = int(stats.get('likes', 0)) if stats is not None else 0
                    
                    wrestler = extract_wrestler_from_title(title, uploader)
                    
                    # Raw video object - Lovable will AI rewrite and save to Supabase
                    video_data = {
                        "id": yt_id,
                        "platform": "youtube",
                        "original_id": video_id,
                        "original_title": title,  # Lovable will AI rewrite this
                        "original_description": original_description,  # Lovable will AI rewrite this
                        "thumbnail": thumbnail,
                        "video_url": video_url,  # Ready for immediate playback
                        "uploader": uploader,
                        "wrestler": wrestler,
                        "duration": duration,
                        "view_count": view_count,
                        "like_count": like_count,
                        "upload_timestamp": published,
                        "tags": [platform, "youtube"],
                        "platform_category": platform
                    }
                    
                    videos.append(video_data)
                    
                except Exception as e:
                    print(f"Error parsing entry: {e}")
                    continue
                    
    except Exception as e:
        print(f"Error fetching RSS {feed_url}: {e}")
    
    return videos

async def fetch_all_raw_videos() -> List[dict]:
    """Fetch ALL raw videos from all RSS feeds"""
    all_videos = []
    
    # Fetch WWE videos
    for feed_url in WWE_RSS_FEEDS:
        videos = await fetch_single_rss(feed_url, "wwe")
        all_videos.extend(videos)
    
    # Fetch AEW videos
    for feed_url in AEW_RSS_FEEDS:
        videos = await fetch_single_rss(feed_url, "aew")
        all_videos.extend(videos)
    
    # Update fetch state
    with open(FETCH_STATE_FILE, 'r') as f:
        state = json.load(f)
    
    wwe_count = sum(1 for v in all_videos if v["platform_category"] == "wwe")
    aew_count = sum(1 for v in all_videos if v["platform_category"] == "aew")
    
    state.update({
        "last_fetch": datetime.now().isoformat(),
        "total_fetched": len(all_videos),
        "wwe_count": wwe_count,
        "aew_count": aew_count
    })
    
    with open(FETCH_STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)
    
    return all_videos

# ============================================
# DOWNLOADER LOGIC (FULLY FUNCTIONAL)
# ============================================
YDL_OPTS_BASE = {
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    'force_generic_extractor': False,
    'ignoreerrors': True,
    'no_color': True,
    'geo_bypass': True,
    'socket_timeout': 30,
    'retries': 3,
    'fragment_retries': 3,
    'skip_unavailable_fragments': True,
    'keepvideo': False,
    'hls_prefer_native': True,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-us,en;q=0.5',
        'Accept-Encoding': 'gzip,deflate',
        'Connection': 'keep-alive',
    }
}

@app.get("/info")
async def get_video_info(url: str = Query(...)):
    """Get video metadata - works for YouTube, Facebook, Instagram, X"""
    try:
        ydl_opts = YDL_OPTS_BASE.copy()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise Exception("No info returned")
            return {
                "title": info.get("title", "Unknown"),
                "thumbnail": info.get("thumbnail", ""),
                "description": info.get("description", "")[:500] if info.get("description") else "",
                "duration": info.get("duration", 0),
                "uploader": info.get("uploader", ""),
                "view_count": info.get("view_count", 0),
                "like_count": info.get("like_count", 0),
                "platform": info.get("extractor_key", "unknown"),
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error fetching info: {str(e)}")

@app.get("/download")
async def download_video(url: str = Query(...), format: str = Query("best")):
    """Download video - works for YouTube, Facebook, Instagram, X"""
    try:
        ydl_opts = YDL_OPTS_BASE.copy()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            original_title = info.get("title", "video")
            clean_title = clean_filename(original_title)
            filename = f"{clean_title}.mp4"

        uid = uuid.uuid4().hex[:8]  # ← FIXED: uuid now properly imported
        output_template = f"/tmp/{uid}.%(ext)s"

        if format == "best":
            format_string = "best[ext=mp4]/best"
        elif format == "hd":
            format_string = "best[height<=720][ext=mp4]/best[height<=720]/best[ext=mp4]/best"
        elif format == "sd":
            format_string = "best[height<=480][ext=mp4]/best[height<=480]/best[ext=mp4]/best"
        elif format == "audio":
            format_string = "bestaudio/best"
        else:
            format_string = format

        ydl_opts.update({
            'format': format_string,
            'outtmpl': output_template,
            'merge_output_format': 'mp4',
        })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        actual_file_path = None
        for f in os.listdir("/tmp"):
            if f.startswith(uid):
                actual_file_path = os.path.join("/tmp", f)
                break

        if not actual_file_path or not os.path.exists(actual_file_path):
            raise HTTPException(status_code=500, detail="Download failed")

        def iterfile():
            with open(actual_file_path, "rb") as f:
                yield from f
            try:
                os.unlink(actual_file_path)
            except:
                pass

        return StreamingResponse(
            iterfile(),
            media_type="video/mp4",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )

    except Exception as e:
        error_msg = str(e)
        if "Sign in to confirm" in error_msg:
            raise HTTPException(status_code=403, detail="Platform is blocking this request")
        elif "Video unavailable" in error_msg:
            raise HTTPException(status_code=404, detail="Video unavailable or private")
        else:
            raise HTTPException(status_code=500, detail=f"Error: {error_msg}")

# ============================================
# API ENDPOINTS FOR LOVABLE
# ============================================

@app.get("/fetch/raw")
async def fetch_raw_videos():
    """
    Fetch ALL raw videos from YouTube RSS feeds.
    Returns raw data - Lovable will AI rewrite and save to Supabase.
    Videos contain 'video_url' ready for immediate playback.
    """
    print(f"[{datetime.now()}] Fetching all raw videos from RSS...")
    videos = await fetch_all_raw_videos()
    print(f"[{datetime.now()}] Fetched {len(videos)} total videos")
    
    return {
        "success": True,
        "videos": videos,
        "total": len(videos),
        "wwe_count": sum(1 for v in videos if v["platform_category"] == "wwe"),
        "aew_count": sum(1 for v in videos if v["platform_category"] == "aew"),
        "timestamp": datetime.now().isoformat()
    }

@app.get("/fetch/status")
async def get_fetch_status():
    """Get current fetch status"""
    try:
        with open(FETCH_STATE_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"last_fetch": None, "total_fetched": 0}

@app.get("/preview")
async def get_preview_url(url: str = Query(...)):
    """Get direct video URL for player embed"""
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'format': 'best[ext=mp4]/best',
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "video_url": info.get('url', ''),
                "thumbnail": info.get('thumbnail', ''),
                "title": info.get('title', ''),
                "uploader": info.get('uploader', ''),
                "duration": info.get('duration', 0)
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/")
async def root():
    return {
        "message": "Welcome to ReelsDown Video Downloader API",
        "status": "operational",
        "architecture": "Render fetches raw RSS only. Lovable handles AI rewriting and Supabase storage.",
        "endpoints": {
            "/fetch/raw": "GET all raw videos from YouTube RSS (Lovable calls this)",
            "/fetch/status": "GET fetch status",
            "/info": "GET video metadata",
            "/download": "GET download video file",
            "/preview": "GET video preview URL"
        },
        "formats": ["best", "hd", "sd", "audio"],
        "supported_platforms": ["YouTube", "Facebook", "Instagram", "Twitter/X"],
        "rss_feeds": {
            "wwe": len(WWE_RSS_FEEDS),
            "aew": len(AEW_RSS_FEEDS)
        }
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
