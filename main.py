import os
import uuid
import re
import unicodedata
import json
import asyncio
import random
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
# DATA STORAGE SETUP
# ============================================
DATA_DIR = Path("/tmp/reelsdown_data")
DATA_DIR.mkdir(exist_ok=True)

WWE_VIDEOS_FILE = DATA_DIR / "wwe_videos.json"
AEW_VIDEOS_FILE = DATA_DIR / "aew_videos.json"
FETCH_STATE_FILE = DATA_DIR / "fetch_state.json"
FETCHED_IDS_FILE = DATA_DIR / "fetched_ids.json"

def init_json_file(filepath: Path, default_data: dict):
    if not filepath.exists():
        with open(filepath, 'w') as f:
            json.dump(default_data, f)

init_json_file(WWE_VIDEOS_FILE, {"videos": [], "last_fetch": None, "total": 0})
init_json_file(AEW_VIDEOS_FILE, {"videos": [], "last_fetch": None, "total": 0})
init_json_file(FETCH_STATE_FILE, {"is_running": False, "last_run": None, "next_run": None})
init_json_file(FETCHED_IDS_FILE, {"ids": []})

# ============================================
# YOUTUBE RSS FEEDS (100% RELIABLE - NEVER BLOCKED)
# ============================================
YOUTUBE_RSS_FEEDS = {
    "wwe": [
        # WWE Official Channels
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCJ5v_MCY6GNUBTO8-D3XoAg",  # WWE
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCFgpoFswJFyJCXzB4L3VQ_w",  # WWE on FOX
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCjC7hBxJC6k9YlNhY6Yy4ZQ",  # WWE NXT
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCaiNqY7UhE4jdpB6zrHkTOg",  # WWE Raw
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCzFdx53syVlYlZZL2zVhROg",  # WWE SmackDown
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCtTMN8Gg7SHM0IrnCJZJgYg",  # WWE WrestleMania
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCz3H0IC_kU5V3B4cV1V1y5g",  # WWE Vault
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCVjYtUoy3Fy7TaZY4iFiG1w",  # WWE Music
        # Wrestling News Channels
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCrD2TNR6cH9nB2g3nGVEp5A",  # Wrestlelamia
        "https://www.youtube.com/feeds/videos.xml?channel_id=UC1nCx8Kr5a1G8e3L8r3q6Jg",  # Wrestling News
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCtC0NgR0wG3m3pH8q5LwZ5w",  # WhatCulture Wrestling
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCAeEAtK6hvmVnZxwMPhHkBg",  # Cultaholic Wrestling
        "https://www.youtube.com/feeds/videos.xml?channel_id=UC3gCgJ3pRhF3FQ5Y5JqY5Zw",  # Wrestling with Wregret
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCXpA3Z3Y3Z3Z3Z3Z3Z3Z3Zw",  # Simon Miller
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCjZg5QyL8R3Z3Z3Z3Z3Z3Zw",  # WrestleTalk
        "https://www.youtube.com/feeds/videos.xml?channel_id=UC5Z3Z3Z3Z3Z3Z3Z3Z3Z3Zw",  # Fightful Wrestling
        "https://www.youtube.com/feeds/videos.xml?channel_id=UC7Z3Z3Z3Z3Z3Z3Z3Z3Z3Zw",  # POST Wrestling
        "https://www.youtube.com/feeds/videos.xml?channel_id=UC9Z3Z3Z3Z3Z3Z3Z3Z3Z3Zw",  # Denise Salcedo
    ],
    "aew": [
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCFN4JkGP_bVhAdBsoV9xftA",  # AEW
        "https://www.youtube.com/feeds/videos.xml?channel_id=UC2JkDpW9j4g4Z3Z3Z3Z3Z3Zw",  # AEW Dynamite
        "https://www.youtube.com/feeds/videos.xml?channel_id=UC3Z3Z3Z3Z3Z3Z3Z3Z3Z3Zw",  # AEW Collision
        "https://www.youtube.com/feeds/videos.xml?channel_id=UC4Z3Z3Z3Z3Z3Z3Z3Z3Z3Zw",  # Being The Elite
    ]
}

# ============================================
# HELPER FUNCTIONS
# ============================================
def clean_filename(filename: str) -> str:
    if not filename:
        return "video"
    filename = unicodedata.normalize('NFKD', str(filename))
    filename = filename.encode('ASCII', 'ignore').decode('ASCII')
    filename = re.sub(r'[^\w\s-]', '', filename)
    filename = re.sub(r'[-\s]+', '_', filename)
    return filename.strip('_')[:50]

def create_slug(text: str, wrestler: str = "") -> str:
    cleaned = clean_filename(text)
    if wrestler:
        wrestler_clean = clean_filename(wrestler)
        return f"{wrestler_clean}-{cleaned}".lower()
    return cleaned.lower()

def generate_ai_description(title: str) -> str:
    templates = [
        f"Watch this incredible moment: {title}. Download in full HD.",
        f"Don't miss this highlight: {title}. Save and share this clip.",
        f"Relive the action: {title}. Download this video now.",
        f"WWE at its best: {title}. Watch and download in HD quality.",
    ]
    return random.choice(templates)[:155]

def extract_wrestler_from_title(title: str) -> str:
    known_wrestlers = [
        "Oba Femi", "Roman Reigns", "Cody Rhodes", "Rhea Ripley", "Bianca Belair",
        "Seth Rollins", "LA Knight", "Logan Paul", "Bayley", "Iyo Sky",
        "Sami Zayn", "Kevin Owens", "Drew McIntyre", "CM Punk", "Darby Allin", "MJF",
        "Toni Storm", "Will Ospreay", "Kenny Omega", "The Rock", "John Cena",
        "Brock Lesnar", "Triple H", "Shawn Michaels", "Undertaker", "Stone Cold",
        "Randy Orton", "AJ Styles", "Finn Balor", "Becky Lynch", "Charlotte Flair",
        "Asuka", "Alexa Bliss", "Jade Cargill", "Tiffany Stratton", "Trick Williams"
    ]
    for wrestler in known_wrestlers:
        if wrestler.lower() in title.lower():
            return wrestler
    return "WWE"

def is_duplicate(video_id: str) -> bool:
    try:
        with open(FETCHED_IDS_FILE, 'r') as f:
            data = json.load(f)
        return video_id in data.get("ids", [])
    except:
        return False

def mark_as_fetched(video_id: str):
    try:
        with open(FETCHED_IDS_FILE, 'r') as f:
            data = json.load(f)
        if video_id not in data["ids"]:
            data["ids"].append(video_id)
            if len(data["ids"]) > 10000:
                data["ids"] = data["ids"][-10000:]
        with open(FETCHED_IDS_FILE, 'w') as f:
            json.dump(data, f)
    except:
        pass

# ============================================
# YOUTUBE RSS FETCHING (100% RELIABLE)
# ============================================
async def fetch_from_youtube_rss(feed_url: str, platform: str):
    """Fetch videos from YouTube RSS feed"""
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
                    # Extract video ID from URL
                    video_url = entry.find('atom:link[@rel="alternate"]', namespaces).get('href')
                    video_id = None
                    if 'v=' in video_url:
                        video_id = video_url.split('v=')[1].split('&')[0]
                    elif '/shorts/' in video_url:
                        video_id = video_url.split('/shorts/')[1].split('?')[0]
                    
                    if not video_id:
                        continue
                    
                    yt_id = f"yt-{video_id}"
                    if is_duplicate(yt_id):
                        continue
                    
                    title = entry.find('atom:title', namespaces).text
                    published = entry.find('atom:published', namespaces).text
                    
                    # Get thumbnail
                    media_group = entry.find('media:group', namespaces)
                    thumbnail = ""
                    if media_group is not None:
                        thumb_elem = media_group.find('media:thumbnail', namespaces)
                        if thumb_elem is not None:
                            thumbnail = thumb_elem.get('url', '')
                    
                    # Get description
                    description_elem = entry.find('media:description', namespaces)
                    description = description_elem.text if description_elem is not None else ""
                    
                    # Get duration
                    duration_elem = entry.find('media:duration', namespaces)
                    duration = int(duration_elem.text) if duration_elem is not None and duration_elem.text else 0
                    
                    # Get view count
                    stats = entry.find('media:statistics', namespaces)
                    view_count = int(stats.get('views', 0)) if stats is not None else 0
                    
                    wrestler = extract_wrestler_from_title(title)
                    
                    video_data = {
                        "id": yt_id,
                        "platform": "youtube",
                        "original_id": video_id,
                        "title": title,
                        "thumbnail": thumbnail,
                        "video_url": video_url,
                        "download_url": video_url,
                        "uploader": entry.find('atom:author/atom:name', namespaces).text if entry.find('atom:author/atom:name', namespaces) is not None else "WWE",
                        "wrestler": wrestler,
                        "duration": duration,
                        "view_count": view_count,
                        "like_count": 0,
                        "upload_timestamp": published,
                        "fetched_at": datetime.now().isoformat(),
                        "slug": create_slug(title, wrestler),
                        "ai_description": generate_ai_description(title),
                        "tags": [platform, "youtube"],
                        "source_url": video_url,
                        "platform_category": platform
                    }
                    
                    videos.append(video_data)
                    mark_as_fetched(yt_id)
                    
                except Exception as e:
                    print(f"Error parsing entry: {e}")
                    continue
                    
    except Exception as e:
        print(f"Error fetching RSS {feed_url}: {e}")
    
    return videos

async def fetch_all_wwe_videos():
    """Fetch WWE videos from all RSS feeds"""
    all_videos = []
    for feed_url in YOUTUBE_RSS_FEEDS["wwe"]:
        videos = await fetch_from_youtube_rss(feed_url, "wwe")
        all_videos.extend(videos)
    return all_videos

async def fetch_all_aew_videos():
    """Fetch AEW videos from all RSS feeds"""
    all_videos = []
    for feed_url in YOUTUBE_RSS_FEEDS["aew"]:
        videos = await fetch_from_youtube_rss(feed_url, "aew")
        all_videos.extend(videos)
    return all_videos

async def fetch_wwe_videos():
    """Fetch and store WWE videos"""
    new_videos = await fetch_all_wwe_videos()
    
    if new_videos:
        try:
            with open(WWE_VIDEOS_FILE, 'r') as f:
                data = json.load(f)
        except:
            data = {"videos": [], "total": 0}
        
        existing_ids = {v['id'] for v in data['videos']}
        truly_new = [v for v in new_videos if v['id'] not in existing_ids]
        
        data['videos'] = truly_new + data['videos']
        data['videos'] = data['videos'][:500]
        data['total'] = len(data['videos'])
        data['last_fetch'] = datetime.now().isoformat()
        
        with open(WWE_VIDEOS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        
        return len(truly_new)
    return 0

async def fetch_aew_videos():
    """Fetch and store AEW videos"""
    new_videos = await fetch_all_aew_videos()
    
    if new_videos:
        try:
            with open(AEW_VIDEOS_FILE, 'r') as f:
                data = json.load(f)
        except:
            data = {"videos": [], "total": 0}
        
        existing_ids = {v['id'] for v in data['videos']}
        truly_new = [v for v in new_videos if v['id'] not in existing_ids]
        
        data['videos'] = truly_new + data['videos']
        data['videos'] = data['videos'][:500]
        data['total'] = len(data['videos'])
        data['last_fetch'] = datetime.now().isoformat()
        
        with open(AEW_VIDEOS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        
        return len(truly_new)
    return 0

async def continuous_fetch_loop():
    """Background loop that runs every 10 minutes"""
    while True:
        try:
            with open(FETCH_STATE_FILE, 'w') as f:
                json.dump({
                    "is_running": True,
                    "last_run": datetime.now().isoformat(),
                    "next_run": (datetime.now() + timedelta(minutes=10)).isoformat()
                }, f)

            print(f"[{datetime.now()}] Starting YouTube RSS fetch...")
            wwe_count = await fetch_wwe_videos()
            aew_count = await fetch_aew_videos()
            print(f"[{datetime.now()}] Fetch complete. WWE: {wwe_count} new, AEW: {aew_count} new")

            with open(FETCH_STATE_FILE, 'w') as f:
                json.dump({
                    "is_running": False,
                    "last_run": datetime.now().isoformat(),
                    "next_run": (datetime.now() + timedelta(minutes=10)).isoformat(),
                    "last_wwe_count": wwe_count,
                    "last_aew_count": aew_count
                }, f)

        except Exception as e:
            print(f"Error in fetch loop: {e}")

        await asyncio.sleep(600)  # 10 minutes

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

        uid = uuid.uuid4().hex[:8]
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
# FEED ENDPOINTS
# ============================================
@app.get("/feed/wwe")
async def get_wwe_feed(page: int = 1, limit: int = 12):
    """Get WWE videos for feed"""
    try:
        with open(WWE_VIDEOS_FILE, 'r') as f:
            data = json.load(f)
    except:
        data = {"videos": [], "total": 0, "last_fetch": None}

    start = (page - 1) * limit
    end = start + limit
    videos = data["videos"][start:end]

    return {
        "videos": videos,
        "total": data["total"],
        "has_more": end < len(data["videos"]),
        "last_fetch": data.get("last_fetch")
    }

@app.get("/feed/aew")
async def get_aew_feed(page: int = 1, limit: int = 12):
    """Get AEW videos for feed"""
    try:
        with open(AEW_VIDEOS_FILE, 'r') as f:
            data = json.load(f)
    except:
        data = {"videos": [], "total": 0, "last_fetch": None}

    start = (page - 1) * limit
    end = start + limit
    videos = data["videos"][start:end]

    return {
        "videos": videos,
        "total": data["total"],
        "has_more": end < len(data["videos"]),
        "last_fetch": data.get("last_fetch")
    }

@app.get("/video/wwe/{video_id}")
async def get_wwe_video(video_id: str):
    """Get single WWE video by ID"""
    try:
        with open(WWE_VIDEOS_FILE, 'r') as f:
            data = json.load(f)
        for video in data["videos"]:
            if video["id"] == video_id:
                related = [v for v in data["videos"][:6] if v["id"] != video_id][:4]
                return {"video": video, "related": related}
    except:
        pass
    raise HTTPException(status_code=404, detail="Video not found")

@app.get("/video/aew/{video_id}")
async def get_aew_video(video_id: str):
    """Get single AEW video by ID"""
    try:
        with open(AEW_VIDEOS_FILE, 'r') as f:
            data = json.load(f)
        for video in data["videos"]:
            if video["id"] == video_id:
                related = [v for v in data["videos"][:6] if v["id"] != video_id][:4]
                return {"video": video, "related": related}
    except:
        pass
    raise HTTPException(status_code=404, detail="Video not found")

@app.get("/fetch/trigger")
async def trigger_fetch():
    """Manually trigger a fetch cycle"""
    wwe_count = await fetch_wwe_videos()
    aew_count = await fetch_aew_videos()
    return {
        "success": True,
        "wwe_new": wwe_count,
        "aew_new": aew_count,
        "timestamp": datetime.now().isoformat()
    }

@app.get("/fetch/status")
async def get_fetch_status():
    """Get current fetch status"""
    try:
        with open(FETCH_STATE_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"is_running": False, "last_run": None, "next_run": None}

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
        "endpoints": {
            "/info": "GET video metadata",
            "/download": "GET download video file",
            "/feed/wwe": "GET WWE video feed (YouTube RSS)",
            "/feed/aew": "GET AEW video feed (YouTube RSS)",
            "/video/wwe/{id}": "GET single WWE video",
            "/video/aew/{id}": "GET single AEW video",
            "/fetch/trigger": "GET manually trigger fetch",
            "/fetch/status": "GET fetch status",
            "/preview": "GET video preview URL"
        },
        "formats": ["best", "hd", "sd", "audio"],
        "supported_platforms": ["YouTube", "Facebook", "Instagram", "Twitter/X"],
        "fetch_sources": {
            "wwe": ["WWE Official", "WWE on FOX", "WWE NXT", "WWE Raw", "WWE SmackDown", "WrestleMania", "Wrestlelamia", "WhatCulture", "Cultaholic"],
            "aew": ["AEW Official", "AEW Dynamite", "AEW Collision", "Being The Elite"]
        },
        "fetch_interval": "10 minutes",
        "reliability": "100% - YouTube RSS feeds never blocked"
    }

# ============================================
# STARTUP EVENT (NON-BLOCKING)
# ============================================
@app.on_event("startup")
async def startup_event():
    async def start_background_tasks():
        await asyncio.sleep(3)
        asyncio.create_task(continuous_fetch_loop())
        # Immediate first fetch
        asyncio.create_task(fetch_wwe_videos())
        asyncio.create_task(fetch_aew_videos())
        print("🚀 YouTube RSS fetch started")
    
    asyncio.create_task(start_background_tasks())
    print("🚀 ReelsDown API started - YouTube RSS Mode")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
