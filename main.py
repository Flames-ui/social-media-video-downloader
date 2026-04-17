import os
import re
import unicodedata
import json
import asyncio
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
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
# CONFIGURATION & PERSISTENCE
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
# YOUTUBE RSS FEEDS
# ============================================
WWE_RSS_FEEDS = [
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCJ5v_MCY6GNUBTO8-D3XoAg",
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCFgpoFswJFyJCXzB4L3VQ_w",
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCjC7hBxJC6k9YlNhY6Yy4ZQ",
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCaiNqY7UhE4jdpB6zrHkTOg",
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCzFdx53syVlYlZZL2zVhROg",
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCtTMN8Gg7SHM0IrnCJZJgYg",
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCz3H0IC_kU5V3B4cV1V1y5g",
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCVjYtUoy3Fy7TaZY4iFiG1w",
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCrD2TNR6cH9nB2g3nGVEp5A",
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCtC0NgR0wG3m3pH8q5LwZ5w",
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCAeEAtK6hvmVnZxwMPhHkBg",
]

AEW_RSS_FEEDS = [
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCFN4JkGP_bVhAdBsoV9xftA",
]

# ============================================
# UTILITIES
# ============================================
def clean_filename(filename: str) -> str:
    if not filename: return "video"
    filename = unicodedata.normalize('NFKD', str(filename))
    filename = filename.encode('ASCII', 'ignore').decode('ASCII')
    filename = re.sub(r'[^\w\s-]', '', filename)
    filename = re.sub(r'[-\s]+', '_', filename)
    return filename.strip('_')[:50]

def extract_video_id_from_url(url: str) -> Optional[str]:
    if not url: return None
    if 'v=' in url: return url.split('v=')[1].split('&')[0]
    elif '/shorts/' in url: return url.split('/shorts/')[1].split('?')[0]
    elif '/embed/' in url: return url.split('/embed/')[1].split('?')[0]
    elif 'youtu.be/' in url: return url.split('youtu.be/')[1].split('?')[0]
    return None

def extract_wrestler_from_title(title: str, uploader: str) -> str:
    known_wrestlers = [
        "Oba Femi", "Roman Reigns", "Cody Rhodes", "Rhea Ripley", "Bianca Belair",
        "Seth Rollins", "LA Knight", "Logan Paul", "Bayley", "Iyo Sky",
        "Sami Zayn", "Kevin Owens", "Drew McIntyre", "CM Punk", "Darby Allin", 
        "MJF", "Toni Storm", "Will Ospreay", "Kenny Omega", "The Rock", "John Cena"
    ]
    for wrestler in known_wrestlers:
        if wrestler.lower() in title.lower() or wrestler.lower() in uploader.lower():
            return wrestler
    return uploader

# ============================================
# WILDCARD RSS LOGIC (FIXES 0 RESULTS ISSUE)
# ============================================
async def fetch_single_rss(feed_url: str, platform: str) -> List[dict]:
    videos = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(feed_url)
            response.raise_for_status()
            
            root = ET.fromstring(response.content)
            # Use Wildcard {*} to ignore namespace URI mismatches
            entries = root.findall('.//{*}entry')
            
            for entry in entries:
                try:
                    # Find IDs and Metadata using wildcard
                    id_elem = entry.find('.//{*}videoId')
                    if id_elem is None: continue
                    video_id = id_elem.text

                    title_elem = entry.find('.//{*}title')
                    title = title_elem.text if title_elem is not None else "Wrestling Video"
                    
                    thumb_elem = entry.find('.//{*}thumbnail')
                    thumbnail = thumb_elem.get('url') if thumb_elem is not None else ""
                    
                    desc_elem = entry.find('.//{*}description')
                    description = desc_elem.text[:500] if desc_elem is not None and desc_elem.text else ""
                    
                    stats_elem = entry.find('.//{*}statistics')
                    view_count = int(stats_elem.get('views', 0)) if stats_elem is not None else 0

                    dur_elem = entry.find('.//{*}duration')
                    duration = int(dur_elem.text) if dur_elem is not None and dur_elem.text else 0

                    author_elem = entry.find('.//{*}author/{*}name')
                    uploader = author_elem.text if author_elem is not None else platform.upper()

                    pub_elem = entry.find('.//{*}published')
                    published = pub_elem.text if pub_elem is not None else datetime.now().isoformat()

                    videos.append({
                        "id": f"yt-{video_id}",
                        "platform": "youtube",
                        "original_id": video_id,
                        "original_title": title,
                        "original_description": description,
                        "thumbnail": thumbnail,
                        "video_url": f"https://www.youtube.com/watch?v={video_id}",
                        "uploader": uploader,
                        "wrestler": extract_wrestler_from_title(title, uploader),
                        "duration": duration,
                        "view_count": view_count,
                        "upload_timestamp": published,
                        "platform_category": platform
                    })
                except Exception: continue
    except Exception as e:
        print(f"Error fetching {feed_url}: {e}")
    return videos

# ============================================
# DOWNLOADER LOGIC (RETAINED & POWERFUL)
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
        'Connection': 'keep-alive',
    }
}

@app.get("/info")
async def get_video_info(url: str = Query(...)):
    try:
        with yt_dlp.YoutubeDL(YDL_OPTS_BASE) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info: raise Exception("No info found")
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
        raise HTTPException(status_code=400, detail=f"Error: {str(e)}")

@app.get("/download")
async def download_video(url: str = Query(...), format: str = Query("best")):
    try:
        with yt_dlp.YoutubeDL(YDL_OPTS_BASE) as ydl:
            info = ydl.extract_info(url, download=False)
            clean_title = clean_filename(info.get("title", "video"))
            filename = f"{clean_title}.mp4"

        uid = uuid.uuid4().hex[:8]
        output_template = f"/tmp/{uid}.%(ext)s"

        format_string = {
            "best": "best[ext=mp4]/best",
            "hd": "best[height<=720][ext=mp4]/best",
            "sd": "best[height<=480][ext=mp4]/best",
            "audio": "bestaudio/best"
        }.get(format, format)

        opts = {**YDL_OPTS_BASE, 'format': format_string, 'outtmpl': output_template, 'merge_output_format': 'mp4'}

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        file_path = next((os.path.join("/tmp", f) for f in os.listdir("/tmp") if f.startswith(uid)), None)
        if not file_path: raise HTTPException(status_code=500, detail="Download failed")

        def iterfile():
            with open(file_path, "rb") as f: yield from f
            try: os.unlink(file_path)
            except: pass

        return StreamingResponse(iterfile(), media_type="video/mp4",
                                 headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/fetch/raw")
async def fetch_raw_videos():
    all_videos = []
    # Fetch all feeds
    tasks = [fetch_single_rss(f, "wwe") for f in WWE_RSS_FEEDS] + \
            [fetch_single_rss(f, "aew") for f in AEW_RSS_FEEDS]
    
    results = await asyncio.gather(*tasks)
    for res in results:
        all_videos.extend(res)
    
    # Update state
    with open(FETCH_STATE_FILE, 'r') as f: state = json.load(f)
    state.update({"last_fetch": datetime.now().isoformat(), "total_fetched": len(all_videos)})
    with open(FETCH_STATE_FILE, 'w') as f: json.dump(state, f)
    
    return {"success": True, "videos": all_videos, "total": len(all_videos)}

@app.get("/preview")
async def get_preview_url(url: str = Query(...)):
    try:
        opts = {**YDL_OPTS_BASE, 'format': 'best[ext=mp4]/best'}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {"video_url": info.get('url', ''), "thumbnail": info.get('thumbnail', ''), "title": info.get('title', '')}
    except Exception as e: raise HTTPException(status_code=400, detail=str(e))

@app.get("/")
async def root():
    return {"message": "ReelsDown API Operational", "status": "active"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
