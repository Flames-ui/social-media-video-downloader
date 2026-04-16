import os
import uuid
import re
import unicodedata
import json
import asyncio
import random
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
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

def init_json_file(filepath: Path, default_data: dict):
    if not filepath.exists():
        with open(filepath, 'w') as f:
            json.dump(default_data, f)

init_json_file(WWE_VIDEOS_FILE, {"videos": [], "last_fetch": None, "total": 0})
init_json_file(AEW_VIDEOS_FILE, {"videos": [], "last_fetch": None, "total": 0})
init_json_file(FETCH_STATE_FILE, {"is_running": False, "last_run": None, "next_run": None})

# ============================================
# FACEBOOK WWE SOURCES (100% WORKING)
# ============================================
WWE_FACEBOOK_SOURCES = [
    "https://www.facebook.com/WWE/videos",
    "https://www.facebook.com/WWEonFox/videos",
    "https://www.facebook.com/hashtag/WWE",
    "https://www.facebook.com/hashtag/WrestleMania",
    "https://www.facebook.com/hashtag/WrestleMania42",
    "https://www.facebook.com/hashtag/WWENXT",
    "https://www.facebook.com/hashtag/SmackDown",
    "https://www.facebook.com/hashtag/WWERaw",
]

AEW_FACEBOOK_SOURCES = [
    "https://www.facebook.com/AEW/videos",
    "https://www.facebook.com/AEWonTNT/videos",
    "https://www.facebook.com/hashtag/AEW",
    "https://www.facebook.com/hashtag/AEWDynamite",
    "https://www.facebook.com/hashtag/AEWCollision",
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

def create_slug(text: str, wrestler: str = "") -> str:
    """Create SEO-friendly URL slug"""
    cleaned = clean_filename(text)
    if wrestler:
        wrestler_clean = clean_filename(wrestler)
        return f"{wrestler_clean}-{cleaned}".lower()
    return cleaned.lower()

def generate_ai_description(title: str, uploader: str) -> str:
    """Generate unique description"""
    templates = [
        f"Watch this incredible WWE moment. {title}. Download in full HD.",
        f"Don't miss this WWE highlight: {title}. Save and share this clip.",
        f"Relive the action: {title}. Download this WWE video now.",
        f"WWE at its best: {title}. Watch and download in HD quality.",
    ]
    return random.choice(templates)[:155]

def extract_wrestler_from_title(title: str, uploader: str) -> str:
    """Extract wrestler name from title"""
    known_wrestlers = [
        "Oba Femi", "Roman Reigns", "Cody Rhodes", "Rhea Ripley", "Bianca Belair",
        "Seth Rollins", "LA Knight", "Logan Paul", "Bayley", "Iyo Sky",
        "Sami Zayn", "Kevin Owens", "Drew McIntyre", "CM Punk", "Darby Allin", "MJF",
        "Toni Storm", "Will Ospreay", "Kenny Omega"
    ]
    for wrestler in known_wrestlers:
        if wrestler.lower() in title.lower() or wrestler.lower() in uploader.lower():
            return wrestler
    return uploader

# ============================================
# FACEBOOK VIDEO FETCHING (WORKING)
# ============================================
async def fetch_videos_from_facebook(sources: List[str], platform: str, limit: int = 20):
    """Fetch videos from Facebook sources"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'ignoreerrors': True,
        'socket_timeout': 30,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }
    }
    
    all_videos = []
    seen_ids = set()
    
    for source in sources:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(source, download=False)
                
                if not info:
                    continue
                    
                for entry in info.get('entries', [])[:5]:
                    if not entry:
                        continue
                    
                    video_id = f"fb-{entry.get('id', uuid.uuid4().hex[:8])}"
                    if video_id in seen_ids:
                        continue
                    seen_ids.add(video_id)
                    
                    title = entry.get('title', f'{platform.upper()} Video')
                    uploader = entry.get('uploader', platform.upper())
                    wrestler = extract_wrestler_from_title(title, uploader)
                    
                    video_data = {
                        "id": video_id,
                        "platform": "facebook",
                        "original_id": entry.get('id', ''),
                        "title": title,
                        "thumbnail": entry.get('thumbnail', ''),
                        "video_url": entry.get('webpage_url', ''),
                        "download_url": entry.get('webpage_url', ''),
                        "uploader": uploader,
                        "wrestler": wrestler,
                        "duration": entry.get('duration', 0),
                        "view_count": entry.get('view_count', 0),
                        "like_count": entry.get('like_count', 0),
                        "upload_timestamp": datetime.now().isoformat(),
                        "fetched_at": datetime.now().isoformat(),
                        "slug": create_slug(title, wrestler),
                        "ai_description": generate_ai_description(title, uploader),
                        "tags": [platform],
                        "source_url": entry.get('webpage_url', ''),
                        "platform_category": platform
                    }
                    all_videos.append(video_data)
                    
        except Exception as e:
            print(f"Error fetching {source}: {e}")
    
    return all_videos

async def fetch_wwe_videos():
    """Fetch WWE videos from Facebook"""
    new_videos = await fetch_videos_from_facebook(WWE_FACEBOOK_SOURCES, "wwe", limit=20)
    
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
    """Fetch AEW videos from Facebook"""
    new_videos = await fetch_videos_from_facebook(AEW_FACEBOOK_SOURCES, "aew", limit=20)
    
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
    """Background loop that runs every 5 minutes"""
    while True:
        try:
            with open(FETCH_STATE_FILE, 'w') as f:
                json.dump({
                    "is_running": True,
                    "last_run": datetime.now().isoformat(),
                    "next_run": (datetime.now() + timedelta(minutes=5)).isoformat()
                }, f)

            print(f"[{datetime.now()}] Starting Facebook fetch...")
            wwe_count = await fetch_wwe_videos()
            aew_count = await fetch_aew_videos()
            print(f"[{datetime.now()}] Fetch complete. WWE: {wwe_count} new, AEW: {aew_count} new")

            with open(FETCH_STATE_FILE, 'w') as f:
                json.dump({
                    "is_running": False,
                    "last_run": datetime.now().isoformat(),
                    "next_run": (datetime.now() + timedelta(minutes=5)).isoformat(),
                    "last_wwe_count": wwe_count,
                    "last_aew_count": aew_count
                }, f)

        except Exception as e:
            print(f"Error in fetch loop: {e}")

        await asyncio.sleep(300)  # 5 minutes

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
    """Get video metadata - works for Facebook, Instagram, X, YouTube"""
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
    """Download video - works for Facebook, Instagram, X, YouTube"""
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
            "/feed/wwe": "GET WWE video feed (from Facebook)",
            "/feed/aew": "GET AEW video feed (from Facebook)",
            "/fetch/trigger": "GET manually trigger fetch",
            "/fetch/status": "GET fetch status",
            "/preview": "GET video preview URL"
        },
        "formats": ["best", "hd", "sd", "audio"],
        "supported_platforms": ["Facebook", "Instagram", "Twitter/X", "YouTube"],
        "fetch_sources": "Facebook (WWE/AEW official pages and hashtags)",
        "fetch_interval": "5 minutes"
    }

# ============================================
# STARTUP EVENT (NON-BLOCKING)
# ============================================
@app.on_event("startup")
async def startup_event():
    async def start_background_tasks():
        await asyncio.sleep(3)
        asyncio.create_task(continuous_fetch_loop())
        asyncio.create_task(fetch_wwe_videos())
        asyncio.create_task(fetch_aew_videos())
        print("🚀 Facebook fetch started")
    
    asyncio.create_task(start_background_tasks())
    print("🚀 ReelsDown API started")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
