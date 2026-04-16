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
        "@smackdown",              # WWE SmackDown official
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
    "@raevynbrianaa",            # Wrestling content creator
    "@markmugen23",              # Wrestling/Martial Arts content
    "@wrestlelamia",             # WrestleMania coverage
    "@wrestlingnews",            # Wrestling news updates
]

# Wrestler-Specific Accounts (Search by username pattern)
WRESTLER_USERNAMES = [
    # WrestleMania 42 Featured Wrestlers (Priority)
    "codyrhodes",                # Cody Rhodes - Main Event
    "romanreigns",               # Roman Reigns - Main Event
    "rhearipley",                # Rhea Ripley - Women's World Champion
    "biancabelair",              # Bianca Belair
    "sethrollins",               # Seth Rollins
    "laknight",                  # LA Knight
    "loganpaul",                 # Logan Paul
    "bayley",                    # Bayley
    "iyosky",                    # Iyo Sky
    "samizayn",                  # Sami Zayn
    "kevinowens",                # Kevin Owens
    "drewmcintyre",              # Drew McIntyre
    "cmpunk",                    # CM Punk
    "obafemi",                   # Oba Femi
    "trickwilliams",             # Trick Williams
    "tiffanystratton",           # Tiffany Stratton
    "jadecargill",               # Jade Cargill
    # AEW Wrestlers
    "darbyallin",                # Darby Allin
    "mjf",                       # MJF
    "tonistorm",                 # Toni Storm
    "willospreay",               # Will Ospreay
    "kennyomega",                # Kenny Omega
    "youngbucks",                # The Young Bucks
]

# Hashtags to search
WWE_HASHTAGS = [
    # General WWE
    "WWE", "ObaFemi", "RomanReigns", "WWENXT", "SmackDown",
    "WWERaw", "SummerSlam", "RoyalRumble", "MITB", "SurvivorSeries",
    "CodyRhodes", "RheaRipley", "BiancaBelair", "SethRollins", "TiffanyStratton", "LAKnight",
    # WrestleMania 42 Specific (CRITICAL FOR NEXT 2 WEEKS)
    "WrestleMania42", "WM42", "WrestleMania",
    "WrestleManiaWeek", "WM42Predictions", "WM42Results",
    "WM42Highlights", "WrestleManiaHighlights", "WM42Press",
    "WM42Weekend", "RoadToWrestleMania", "WrestleManiaSunday",
    # WM42 Match Specific
    "CodyVsRoman", "FinishTheStory", "WWEChampionship",
    "RheaRipleyWM42", "WomensWorldChampion",
    "SethRollinsWM42", "WorldHeavyweightChampionship",
    "LAKnightWM42", "USChampionship",
    "BayleyVsIyo", "WWEWomensChampionship",
    "SamiZaynWM42", "KevinOwensWM42",
    "DrewMcIntyreWM42", "CMPunkWM42",
    "ObaFemiWM42", "NXTTagTeam",
]

AEW_HASHTAGS = [
    "AEW", "AEWDynamite", "AEWCollision", "DarbyAllin", "MJF",
    "ToniStorm", "WillOspreay", "KennyOmega", "TheYoungBucks", "AEWRevolution", "AEWAllIn"
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
    """Generate unique AI-rewritten description (same meaning, different words)"""
    templates = [
        f"{uploader} shares an incredible moment as {title}. Watch this must-see clip in full HD.",
        f"Don't miss this highlight: {title}. {uploader} delivers an unforgettable performance.",
        f"{uploader} brings the action with {title}. Download and save this epic moment.",
        f"Relive the excitement: {title}. {uploader} at their absolute best.",
        f"An absolute banger from {uploader}: {title}. Download now in HD quality.",
        f"WrestleMania 42 moment: {title}. {uploader} makes history at Allegiant Stadium.",
        f"From WM42: {title}. {uploader} steals the show on the grandest stage.",
    ]

    description = random.choice(templates)

    if wrestler:
        description = description.replace(uploader, wrestler)

    return description[:155]

def extract_wrestler_from_title(title: str, uploader: str, tags: List[str]) -> str:
    """Extract wrestler name from available data"""
    known_wrestlers = [
        "Oba Femi", "Roman Reigns", "Cody Rhodes", "Rhea Ripley", "Bianca Belair",
        "Seth Rollins", "Tiffany Stratton", "LA Knight", "Logan Paul", "Bayley",
        "Iyo Sky", "Sami Zayn", "Kevin Owens", "Drew McIntyre", "CM Punk",
        "Trick Williams", "Jade Cargill", "Darby Allin", "MJF",
        "Toni Storm", "Will Ospreay", "Kenny Omega", "The Young Bucks"
    ]

    for wrestler in known_wrestlers:
        if wrestler.lower() in title.lower() or wrestler.lower() in uploader.lower():
            return wrestler

    for tag in tags:
        for wrestler in known_wrestlers:
            if wrestler.lower().replace(" ", "") in tag.lower():
                return wrestler

    return uploader

def is_duplicate(video_id: str) -> bool:
    """Check if video ID has already been fetched"""
    with open(FETCHED_IDS_FILE, 'r') as f:
        data = json.load(f)
    return video_id in data.get("ids", [])

def mark_as_fetched(video_id: str):
    """Mark video ID as fetched"""
    with open(FETCHED_IDS_FILE, 'r') as f:
        data = json.load(f)

    if video_id not in data["ids"]:
        data["ids"].append(video_id)
        # Keep only last 10000 IDs to prevent file bloat
        if len(data["ids"]) > 10000:
            data["ids"] = data["ids"][-10000:]

    with open(FETCHED_IDS_FILE, 'w') as f:
        json.dump(data, f)

def is_fresh_video(upload_timestamp) -> bool:
    """Check if video was uploaded in the last 2 minutes"""
    if not upload_timestamp:
        return True  # If no timestamp, assume fresh

    try:
        if isinstance(upload_timestamp, (int, float)):
            upload_time = datetime.fromtimestamp(upload_timestamp)
        else:
            upload_time = datetime.fromisoformat(str(upload_timestamp).replace('Z', '+00:00'))

        # Remove timezone info for comparison
        upload_time = upload_time.replace(tzinfo=None)
        two_minutes_ago = datetime.now() - timedelta(minutes=2)

        return upload_time > two_minutes_ago
    except:
        return True  # If parsing fails, assume fresh

# ============================================
# VIDEO PROCESSING FUNCTION
# ============================================
async def process_video_entry(entry: dict, platform: str, tags: List[str]):
    """Process a single video entry and return formatted data"""
    try:
        video_id = f"tiktok-{entry.get('id', '')}"

        # Skip if duplicate
        if is_duplicate(video_id):
            return None

        # Skip if not fresh
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
# VIDEO FETCHING FUNCTIONS
# ============================================
async def fetch_videos_for_platform(platform: str, limit: int = 15):
    """Fetch videos for a specific platform (WWE or AEW) from multiple sources"""
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
    hashtags = WWE_HASHTAGS if platform == "wwe" else AEW_HASHTAGS
    official_accounts = OFFICIAL_ACCOUNTS.get(platform, [])

    # PRIORITY: WrestleMania 42 hashtags first (if WWE platform)
    wm42_tags = [tag for tag in hashtags if "WM42" in tag or "WrestleMania42" in tag or "WrestleMania" in tag]
    other_tags = [tag for tag in hashtags if tag not in wm42_tags]
    priority_tags = wm42_tags[:6] + other_tags[:4]  # 6 WM42 tags + 4 regular

    # Method 1: Search by hashtags (WM42 priority)
    for tag in priority_tags[:10]:
        try:
            search_url = f"https://www.tiktok.com/tag/{tag.replace('#', '')}"

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(search_url, download=False)

                for entry in info.get('entries', [])[:5]:
                    if entry:
                        video_data = await process_video_entry(entry, platform, [tag])
                        if video_data:
                            all_videos.append(video_data)

        except Exception as e:
            print(f"Error fetching hashtag {tag}: {e}")

    # Method 2: Fetch from official accounts
    for account in official_accounts[:4]:
        try:
            account_url = f"https://www.tiktok.com/@{account.replace('@', '')}"

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(account_url, download=False)

                for entry in info.get('entries', [])[:5]:
                    if entry:
                        video_data = await process_video_entry(entry, platform, [account])
                        if video_data:
                            all_videos.append(video_data)

        except Exception as e:
            print(f"Error fetching account {account}: {e}")

    # Method 3: Search for wrestler usernames (WM42 wrestlers first)
    wm42_wrestlers = ["codyrhodes", "romanreigns", "rhearipley", "sethrollins", "laknight", "loganpaul", "bayley", "iyosky", "samizayn", "kevinowens", "drewmcintyre", "cmpunk"]
    other_wrestlers = [w for w in WRESTLER_USERNAMES if w not in wm42_wrestlers]
    priority_wrestlers = wm42_wrestlers[:8] + other_wrestlers[:4]

    for wrestler in priority_wrestlers[:12]:
        try:
            search_url = f"https://www.tiktok.com/search?q={wrestler}"

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(search_url, download=False)

                for entry in info.get('entries', [])[:3]:
                    if entry:
                        video_data = await process_video_entry(entry, platform, [wrestler])
                        if video_data:
                            all_videos.append(video_data)

        except Exception as e:
            print(f"Error searching wrestler {wrestler}: {e}")

    # Method 4: Fetch from wrestling creator accounts (only for WWE)
    if platform == "wwe":
        for creator in WRESTLING_CREATORS[:6]:
            try:
                creator_url = f"https://www.tiktok.com/@{creator.replace('@', '')}"

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(creator_url, download=False)

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
        with open(WWE_VIDEOS_FILE, 'r') as f:
            data = json.load(f)

        # Add new videos to beginning
        data["videos"] = new_videos + data["videos"]
        data["videos"] = data["videos"][:500]  # Keep last 500
        data["total"] = len(data["videos"])
        data["last_fetch"] = datetime.now().isoformat()

        with open(WWE_VIDEOS_FILE, 'w') as f:
            json.dump(data, f, indent=2)

    return len(new_videos) if new_videos else 0

async def fetch_aew_videos():
    """Fetch AEW videos"""
    new_videos = await fetch_videos_for_platform("aew", limit=15)

    if new_videos:
        with open(AEW_VIDEOS_FILE, 'r') as f:
            data = json.load(f)

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
            # Update fetch state
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

        await asyncio.sleep(120)  # 2 minutes

# ============================================
# EXISTING DOWNLOADER LOGIC (PRESERVED EXACTLY)
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
    """Get video metadata with YouTube fallback methods"""
    try:
        # Try primary method
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
        # Fallback: Try with cookies file if exists
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
        # Get metadata first
        ydl_opts = YDL_OPTS_BASE.copy()

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            original_title = info.get("title", "video")
            clean_title = clean_filename(original_title)
            extension = "mp4"
            filename = f"{clean_title}.{extension}"

        uid = uuid.uuid4().hex[:8]
        output_template = f"/tmp/{uid}.%(ext)s"

        # Format selection with fallbacks
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

        ydl_opts = YDL_OPTS_BASE.copy()
        ydl_opts.update({
            'format': format_string,
            'outtmpl': output_template,
            'merge_output_format': 'mp4',
        })

        # Download
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Find file
        actual_file_path = None
        for f in os.listdir("/tmp"):
            if f.startswith(uid):
                actual_file_path = os.path.join("/tmp", f)
                break

        if not actual_file_path or not os.path.exists(actual_file_path):
            raise HTTPException(status_code=500, detail="Download failed - video may be restricted")

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
        if "Sign in to confirm you're not a bot" in error_msg:
            raise HTTPException(status_code=403, detail="YouTube is blocking this request. Try a different video or try again later.")
        elif "Video unavailable" in error_msg:
            raise HTTPException(status_code=404, detail="This video is unavailable or private.")
        else:
            raise HTTPException(status_code=500, detail=f"Error during download: {error_msg}")

# ============================================
# NEW FEED ENDPOINTS
# ============================================
@app.get("/feed/wwe")
async def get_wwe_feed(page: int = 1, limit: int = 12):
    """Get WWE videos for feed"""
    with open(WWE_VIDEOS_FILE, 'r') as f:
        data = json.load(f)

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
    with open(AEW_VIDEOS_FILE, 'r') as f:
        data = json.load(f)

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
    with open(WWE_VIDEOS_FILE, 'r') as f:
        data = json.load(f)

    for video in data["videos"]:
        if video["id"] == video_id:
            # Get related videos
            related = [v for v in data["videos"][:6] if v["id"] != video_id][:4]
            return {"video": video, "related": related}

    raise HTTPException(status_code=404, detail="Video not found")

@app.get("/video/aew/{video_id}")
async def get_aew_video(video_id: str):
    """Get single AEW video by ID"""
    with open(AEW_VIDEOS_FILE, 'r') as f:
        data = json.load(f)

    for video in data["videos"]:
        if video["id"] == video_id:
            related = [v for v in data["videos"][:6] if v["id"] != video_id][:4]
            return {"video": video, "related": related}

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
    with open(FETCH_STATE_FILE, 'r') as f:
        data = json.load(f)
    return data

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

@app.get("/sitemap-videos.xml")
async def get_video_sitemap():
    """Generate dynamic sitemap for all video pages"""
    with open(WWE_VIDEOS_FILE, 'r') as f:
        wwe_data = json.load(f)
    with open(AEW_VIDEOS_FILE, 'r') as f:
        aew_data = json.load(f)

    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n'
    sitemap += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:video="http://www.google.com/schemas/sitemap-video/1.1">\n'

    base_url = "https://www.reelsdown.name.ng"

    for video in wwe_data["videos"] + aew_data["videos"]:
        platform = "wwe" if video in wwe_data["videos"] else "aew"
        url = f"{base_url}/{platform}/v/{video['id']}/{video['slug']}"

        sitemap += f"  <url>\n"
        sitemap += f"    <loc>{url}</loc>\n"
        sitemap += f"    <lastmod>{video['fetched_at'][:10]}</lastmod>\n"
        sitemap += f"    <changefreq>daily</changefreq>\n"
        sitemap += f"    <priority>0.8</priority>\n"
        sitemap += f"    <video:video>\n"
        sitemap += f"      <video:title>{video['title']}</video:title>\n"
        sitemap += f"      <video:description>{video['ai_description']}</video:description>\n"
        sitemap += f"      <video:thumbnail_loc>{video['thumbnail']}</video:thumbnail_loc>\n"
        sitemap += f"      <video:duration>{video['duration']}</video:duration>\n"
        sitemap += f"    </video:video>\n"
        sitemap += f"  </url>\n"

    sitemap += '</urlset>'

    return StreamingResponse(
        iter([sitemap]),
        media_type="application/xml",
        headers={"Content-Disposition": "inline; filename=sitemap-videos.xml"}
    )

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
            "/video/wwe/{id}": "GET single WWE video",
            "/video/aew/{id}": "GET single AEW video",
            "/fetch/trigger": "GET manually trigger fetch",
            "/fetch/status": "GET fetch status",
            "/preview": "GET video preview URL",
            "/sitemap-videos.xml": "GET dynamic video sitemap"
        },
        "formats": ["best", "hd", "sd", "audio"],
        "supported_platforms": ["TikTok", "Instagram", "Facebook", "Twitter/X", "YouTube"],
        "auto_fetch": {
            "interval": "2 minutes",
            "status": "active",
            "wrestlemania_42_mode": "ENABLED - Priority Fetching Active"
        }
    }

# ============================================
# STARTUP EVENT
# ============================================
@app.on_event("startup")
async def startup_event():
    """Start background fetch loop on startup"""
    asyncio.create_task(continuous_fetch_loop())
    # Trigger immediate first fetch
    asyncio.create_task(fetch_wwe_videos())
    asyncio.create_task(fetch_aew_videos())
    print("🚀 ReelsDown API started - WrestleMania 42 Mode ACTIVE - Background fetch loop (every 2 minutes)")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
