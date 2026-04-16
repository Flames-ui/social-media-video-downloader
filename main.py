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
FETCHED_IDS_FILE = DATA_DIR / "fetched_ids.json"
FETCH_STATE_FILE = DATA_DIR / "fetch_state.json"

def init_json_file(filepath: Path, default_data: dict):
    if not filepath.exists():
        with open(filepath, 'w') as f:
            json.dump(default_data, f)

init_json_file(WWE_VIDEOS_FILE, {"videos": [], "last_fetch": None, "total": 0})
init_json_file(AEW_VIDEOS_FILE, {"videos": [], "last_fetch": None, "total": 0})
init_json_file(FETCHED_IDS_FILE, {"ids": []})
init_json_file(FETCH_STATE_FILE, {"is_running": False, "last_run": None, "next_run": None})

# ============================================
# WRESTLING TIKTOK ACCOUNTS & SOURCES
# ============================================

# Official WWE & AEW Accounts
OFFICIAL_ACCOUNTS = {
    "wwe": [
        "@wwe",                    # Official WWE - 34.5M followers
        "@wwenxt",                 # WWE NXT official
        "@wweraw",                 # WWE Raw official
        "@wrestlemania",           # WrestleMania official
    ],
    "aew": [
        "@aew",                    # Official AEW - 2.4M followers
        "@aewdynamite",            # AEW Dynamite official
        "@aewcollision",           # AEW Collision official
    ]
}

# Wrestling Content Creators & Influencers (High follower accounts)
WRESTLING_CREATORS = [
    "@paulheyman",               # Paul Heyman - WWE personality
    "@shadesdaily",              # 150K+ followers - Wrestling content
    "@vorostwins",               # 1.4M followers - Wrestlers/TikTokers
    "@allenownz",                # 3M+ followers - Wrestling coverage
    "@wrestlelamia",             # WrestleMania coverage
    "@wrestlingnews",            # Wrestling news updates
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

def generate_ai_description(title: str, uploader: str, wrestler: str = "") -> str:
    """Generate unique AI-rewritten description"""
    templates = [
        f"{uploader} shares an incredible moment. Watch this must-see clip in full HD.",
        f"Don't miss this highlight from {uploader}. Download and save this epic moment.",
        f"{uploader} brings the action. Relive this moment in HD quality.",
        f"WrestleMania 42 moment: {uploader} makes history at Allegiant Stadium.",
    ]

    description = random.choice(templates)
    if wrestler:
        description = description.replace(uploader, wrestler)
    return description[:155]

def extract_wrestler_from_title(title: str, uploader: str, tags: List[str]) -> str:
    """Extract wrestler name from available data"""
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

def is_duplicate(video_id: str) -> bool:
    """Check if video ID has already been fetched"""
    try:
        with open(FETCHED_IDS_FILE, 'r') as f:
            data = json.load(f)
        return video_id in data.get("ids", [])
    except:
        return False

def mark_as_fetched(video_id: str):
    """Mark video ID as fetched"""
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

def is_fresh_video(upload_timestamp) -> bool:
    """Check if video was uploaded in the last 2 minutes"""
    if not upload_timestamp:
        return True
    try:
        if isinstance(upload_timestamp, (int, float)):
            upload_time = datetime.fromtimestamp(upload_timestamp)
        else:
            upload_time = datetime.fromisoformat(str(upload_timestamp).replace('Z', '+00:00'))
        upload_time = upload_time.replace(tzinfo=None)
        two_minutes_ago = datetime.now() - timedelta(minutes=2)
        return upload_time > two_minutes_ago
    except:
        return True

# ============================================
# VIDEO PROCESSING FUNCTION
# ============================================
async def process_video_entry(entry: dict, platform: str, tags: List[str]):
    """Process a single video entry and return formatted data"""
    try:
        video_id = f"tiktok-{entry.get('id', '')}"
        if is_duplicate(video_id):
            return None

        upload_time = entry.get('timestamp')
        if not is_fresh_video(upload_time):
            return None

        title = entry.get('title', 'Wrestling Video')
        uploader = entry.get('uploader', 'Unknown')
        wrestler = extract_wrestler_from_title(title, uploader, tags)

        video_data = {
            "id": video_id,
            "platform": "tiktok",
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
            "upload_timestamp": datetime.fromtimestamp(upload_time).isoformat() if upload_time else None,
            "fetched_at": datetime.now().isoformat(),
            "slug": create_slug(title, wrestler),
            "ai_description": generate_ai_description(title, uploader, wrestler),
            "tags": tags,
            "source_url": entry.get('webpage_url', ''),
            "platform_category": platform
        }

        mark_as_fetched(video_id)
        return video_data

    except Exception as e:
        print(f"Error processing video entry: {e}")
        return None

# ============================================
# VIDEO FETCHING FUNCTION (WORKING METHODS ONLY)
# ============================================
async def fetch_videos_for_platform(platform: str, limit: int = 15):
    """Fetch videos from official accounts and wrestling creators only"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'force_generic_extractor': False,
        'ignoreerrors': True,
        'socket_timeout': 30,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }
    }

    all_videos = []
    official_accounts = OFFICIAL_ACCOUNTS.get(platform, [])

    # Method 1: Official accounts
    for account in official_accounts:
        try:
            account_url = f"https://www.tiktok.com/@{account.replace('@', '')}"
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(account_url, download=False)
                if info:
                    for entry in info.get('entries', [])[:5]:
                        if entry:
                            video_data = await process_video_entry(entry, platform, [account])
                            if video_data:
                                all_videos.append(video_data)
        except Exception as e:
            print(f"Error fetching account {account}: {e}")

    # Method 2: Wrestling creator accounts (WWE only)
    if platform == "wwe":
        for creator in WRESTLING_CREATORS:
            try:
                creator_url = f"https://www.tiktok.com/@{creator.replace('@', '')}"
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(creator_url, download=False)
                    if info:
                        for entry in info.get('entries', [])[:3]:
                            if entry:
                                video_data = await process_video_entry(entry, platform, [creator])
                                if video_data:
                                    all_videos.append(video_data)
            except Exception as e:
                print(f"Error fetching creator {creator}: {e}")

    return all_videos

async def fetch_wwe_videos():
    """Fetch WWE videos"""
    new_videos = await fetch_videos_for_platform("wwe", limit=15)

    if new_videos:
        try:
            with open(WWE_VIDEOS_FILE, 'r') as f:
                data = json.load(f)
        except:
            data = {"videos": [], "total": 0}

        data["videos"] = new_videos + data["videos"]
        data["videos"] = data["videos"][:500]
        data["total"] = len(data["videos"])
        data["last_fetch"] = datetime.now().isoformat()

        with open(WWE_VIDEOS_FILE, 'w') as f:
            json.dump(data, f, indent=2)

    return len(new_videos) if new_videos else 0

async def fetch_aew_videos():
    """Fetch AEW videos"""
    new_videos = await fetch_videos_for_platform("aew", limit=15)

    if new_videos:
        try:
            with open(AEW_VIDEOS_FILE, 'r') as f:
                data = json.load(f)
        except:
            data = {"videos": [], "total": 0}

        data["videos"] = new_videos + data["videos"]
        data["videos"] = data["videos"][:500]
        data["total"] = len(data["videos"])
        data["last_fetch"] = datetime.now().isoformat()

        with open(AEW_VIDEOS_FILE, 'w') as f:
            json.dump(data, f, indent=2)

    return len(new_videos) if new_videos else 0

async def continuous_fetch_loop():
    """Background loop that runs every 2 minutes"""
    while True:
        try:
            with open(FETCH_STATE_FILE, 'w') as f:
                json.dump({
                    "is_running": True,
                    "last_run": datetime.now().isoformat(),
                    "next_run": (datetime.now() + timedelta(minutes=2)).isoformat()
                }, f)

            print(f"[{datetime.now()}] Starting fetch cycle...")
            wwe_count = await fetch_wwe_videos()
            aew_count = await fetch_aew_videos()
            print(f"[{datetime.now()}] Fetch complete. WWE: {wwe_count} new, AEW: {aew_count} new")

            with open(FETCH_STATE_FILE, 'w') as f:
                json.dump({
                    "is_running": False,
                    "last_run": datetime.now().isoformat(),
                    "next_run": (datetime.now() + timedelta(minutes=2)).isoformat(),
                    "last_wwe_count": wwe_count,
                    "last_aew_count": aew_count
                }, f)

        except Exception as e:
            print(f"Error in fetch loop: {e}")

        await asyncio.sleep(120)

# ============================================
# EXISTING DOWNLOADER LOGIC
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
    'extractor_args': {
        'youtube': {
            'skip': ['dash', 'hls'],
            'player_skip': ['configs', 'js'],
        }
    },
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
        try:
            ydl_opts = YDL_OPTS_BASE.copy()
            ydl_opts['cookiefile'] = '/tmp/cookies.txt'
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
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
        except Exception as e2:
            raise HTTPException(status_code=400, detail=f"Error fetching info: {str(e2)}")

@app.get("/download")
async def download_video(url: str = Query(...), format: str = Query("best")):
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
            raise HTTPException(status_code=403, detail="YouTube is blocking this request")
        elif "Video unavailable" in error_msg:
            raise HTTPException(status_code=404, detail="Video unavailable or private")
        else:
            raise HTTPException(status_code=500, detail=f"Error: {error_msg}")

# ============================================
# FEED ENDPOINTS
# ============================================
@app.get("/feed/wwe")
async def get_wwe_feed(page: int = 1, limit: int = 12):
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
    try:
        with open(FETCH_STATE_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"is_running": False, "last_run": None, "next_run": None}

@app.get("/preview")
async def get_preview_url(url: str = Query(...)):
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
            "/feed/wwe": "GET WWE video feed",
            "/feed/aew": "GET AEW video feed",
            "/fetch/trigger": "GET manually trigger fetch",
            "/fetch/status": "GET fetch status",
            "/preview": "GET video preview URL"
        },
        "formats": ["best", "hd", "sd", "audio"],
        "auto_fetch": {
            "interval": "2 minutes",
            "status": "active",
            "sources": "Official WWE/AEW accounts + Wrestling creators"
        }
    }

# ============================================
# STARTUP EVENT
# ============================================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(continuous_fetch_loop())
    asyncio.create_task(fetch_wwe_videos())
    asyncio.create_task(fetch_aew_videos())
    print("🚀 ReelsDown API started - Fetch loop active (every 2 minutes)")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
