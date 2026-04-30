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
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import httpx
from dotenv import load_dotenv
from pingtop_handler import get_pingtop_video
from typing import Optional, List

app = FastAPI()
load_dotenv()

app.add_middleware(CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = Path("/tmp/reelsdown_data")
DATA_DIR.mkdir(exist_ok=True)
FETCH_STATE_FILE = DATA_DIR / "fetch_state.json"

def init_json_file(filepath, default_data):
    if not filepath.exists():
        with open(filepath, 'w') as f:
            json.dump(default_data, f)

init_json_file(FETCH_STATE_FILE, {"last_fetch": None, "total_fetched": 0, "wwe_count": 0, "aew_count": 0})

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

CHROME_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
MOBILE_UA = 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36'

def clean_filename(filename):
    if not filename: return "video"
    filename = unicodedata.normalize('NFKD', str(filename))
    filename = filename.encode('ASCII', 'ignore').decode('ASCII')
    filename = re.sub(r'[^\w\s-]', '', filename)
    filename = re.sub(r'[-\s]+', '_', filename)
    return filename.strip('_')[:50]

def detect_platform(url):
    u = url.lower()
    if 'youtube.com' in u or 'youtu.be' in u: return 'youtube'
    if 'tiktok.com' in u: return 'tiktok'
    if 'instagram.com' in u: return 'instagram'
    if 'facebook.com' in u or 'fb.watch' in u or 'fb.com' in u: return 'facebook'
    if 'twitter.com' in u or 'x.com' in u: return 'twitter'
    if 'threads.net' in u: return 'threads'
    if 'linkedin.com' in u: return 'linkedin'
    if 'spotify.com' in u: return 'spotify'
    if 'audiomack.com' in u: return 'audiomack'
    if 'soundcloud.com' in u: return 'soundcloud'
    if 'music.apple.com' in u: return 'applemusic'
    if 'ping.top' in u or 'pingtop.com' in u: return 'pingtop'
    return 'unknown'

def extract_wrestler_from_title(title, uploader):
    wrestlers = ["Oba Femi","Roman Reigns","Cody Rhodes","Rhea Ripley","Bianca Belair",
                 "Seth Rollins","LA Knight","Logan Paul","Bayley","Iyo Sky","Sami Zayn",
                 "Kevin Owens","Drew McIntyre","CM Punk","Darby Allin","MJF","Toni Storm",
                 "Will Ospreay","Kenny Omega","The Rock","John Cena"]
    for w in wrestlers:
        if w.lower() in title.lower() or w.lower() in uploader.lower():
            return w
    return uploader

# ============================================
# PLATFORM-SPECIFIC SCRAPERS (No API key needed)
# ============================================

async def get_tiktok_url(url: str) -> Optional[str]:
    """Get TikTok video URL without watermark using tikwm.com API"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://www.tikwm.com/api/",
                data={"url": url, "count": 12, "cursor": 0, "web": 1, "hd": 1},
                headers={"User-Agent": CHROME_UA}
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("code") == 0:
                    video_data = data.get("data", {})
                    # Return HD no-watermark URL
                    return video_data.get("hdplay") or video_data.get("play") or video_data.get("wmplay")
    except Exception as e:
        print(f"TikTok API error: {e}")
    return None

async def get_instagram_url(url: str) -> Optional[str]:
    """Get Instagram video URL using instaloader approach"""
    try:
        # Use Instagram's own oEmbed API for metadata
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"https://api.instagram.com/oembed/?url={url}",
                headers={"User-Agent": MOBILE_UA}
            )
            if r.status_code == 200:
                data = r.json()
                # oEmbed gives thumbnail but not video URL
                # Use alternative scraper
                pass
    except Exception:
        pass

    # Try saveinsta approach
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            # Get shortcode from URL
            match = re.search(r'/(p|reel|tv)/([A-Za-z0-9_-]+)', url)
            if not match: return None
            shortcode = match.group(2)

            r = await client.get(
                f"https://www.instagram.com/p/{shortcode}/?__a=1&__d=dis",
                headers={
                    "User-Agent": MOBILE_UA,
                    "Accept": "application/json",
                    "x-ig-app-id": "936619743392459",
                }
            )
            if r.status_code == 200:
                data = r.json()
                media = data.get("items", [{}])[0]
                video_versions = media.get("video_versions", [])
                if video_versions:
                    return video_versions[0].get("url")
    except Exception as e:
        print(f"Instagram scraper error: {e}")
    return None

async def get_twitter_url(url: str) -> Optional[str]:
    """Get Twitter/X video URL using fxtwitter API"""
    try:
        # Extract tweet ID
        match = re.search(r'status/(\d+)', url)
        if not match: return None
        tweet_id = match.group(1)

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Use fxtwitter/FixTweet API (free, no key)
            r = await client.get(
                f"https://api.fxtwitter.com/status/{tweet_id}",
                headers={"User-Agent": CHROME_UA}
            )
            if r.status_code == 200:
                data = r.json()
                tweet = data.get("tweet", {})
                media = tweet.get("media", {})
                videos = media.get("videos", [])
                if videos:
                    # Get highest quality
                    best = max(videos, key=lambda v: v.get("width", 0) * v.get("height", 0))
                    return best.get("url")
                # Check for external media
                external = media.get("external", {})
                if external.get("url"):
                    return external["url"]
    except Exception as e:
        print(f"Twitter API error: {e}")
    return None

async def get_facebook_url(url: str) -> Optional[str]:
    """Get Facebook video URL - yt-dlp works well for FB"""
    return None  # Fall through to yt-dlp which handles FB well

async def get_soundcloud_url(url: str) -> Optional[str]:
    """SoundCloud works with yt-dlp"""
    return None

# ============================================
# YT-DLP WITH BEST SETTINGS
# ============================================
def get_ytdlp_opts(platform: str, fmt: str, output: str, is_audio: bool = False) -> dict:
    opts = {
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': False,
        'socket_timeout': 60,
        'retries': 5,
        'fragment_retries': 5,
        'format': fmt,
        'outtmpl': output,
        'merge_output_format': 'mp3' if is_audio else 'mp4',
        'http_headers': {'User-Agent': CHROME_UA},
    }

    if platform == 'youtube':
        # mweb client avoids PO token requirement for most videos
        opts['extractor_args'] = {
            'youtube': {
                'player_client': ['mweb', 'android_vr', 'android'],
                'skip': ['hls'],
            }
        }
        # Use combined formats to avoid DASH issues
        if not is_audio:
            opts['format'] = 'best[ext=mp4]/best'

    elif platform == 'facebook':
        opts['http_headers'] = {
            'User-Agent': CHROME_UA,
            'Referer': 'https://www.facebook.com/',
        }

    elif platform == 'audiomack':
        opts['http_headers'] = {'User-Agent': CHROME_UA, 'Referer': 'https://audiomack.com/'}
        opts['format'] = 'bestaudio/best'

    elif platform == 'soundcloud':
        opts['http_headers'] = {'User-Agent': CHROME_UA, 'Referer': 'https://soundcloud.com/'}
        opts['format'] = 'bestaudio/best'

    if is_audio:
        opts['format'] = 'bestaudio/best'
        opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}]

    return opts

def build_format(fmt: str, platform: str) -> str:
    if platform == 'youtube':
        # Use combined format for YouTube to avoid PO token issues with DASH
        return 'best[ext=mp4]/best'
    if fmt in ['mp3', 'audio']: return 'bestaudio/best'
    return {
        'best':  'best[ext=mp4]/best',
        '1080p': 'best[height<=1080][ext=mp4]/best[height<=1080]/best',
        '720p':  'best[height<=720][ext=mp4]/best[height<=720]/best',
        '480p':  'best[height<=480][ext=mp4]/best[height<=480]/best',
        '360p':  'best[height<=360][ext=mp4]/best[height<=360]/best',
        'hd':    'best[height<=1080][ext=mp4]/best[height<=1080]/best',
        'sd':    'best[height<=480][ext=mp4]/best[height<=480]/best',
    }.get(fmt, 'best[ext=mp4]/best')

# ============================================
# RSS FETCHING
# ============================================
async def fetch_single_rss(feed_url, platform):
    videos = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(feed_url)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            for entry in root.findall('.//{*}entry'):
                try:
                    id_elem = entry.find('.//{*}videoId')
                    if id_elem is None: continue
                    video_id = id_elem.text
                    title_elem = entry.find('.//{*}title')
                    title = title_elem.text if title_elem is not None else "Wrestling Video"
                    thumb_elem = entry.find('.//{*}thumbnail')
                    thumbnail = thumb_elem.get('url') if thumb_elem is not None else ""
                    desc_elem = entry.find('.//{*}description')
                    description = (desc_elem.text or "")[:500] if desc_elem is not None else ""
                    stats_elem = entry.find('.//{*}statistics')
                    view_count = int(stats_elem.get('views', 0)) if stats_elem is not None else 0
                    dur_elem = entry.find('.//{*}duration')
                    duration = int(dur_elem.text) if dur_elem is not None and dur_elem.text else 0
                    author_elem = entry.find('.//{*}author/{*}name')
                    uploader = author_elem.text if author_elem is not None else platform.upper()
                    pub_elem = entry.find('.//{*}published')
                    published = pub_elem.text if pub_elem is not None else datetime.now().isoformat()
                    videos.append({
                        "id": f"yt-{video_id}", "platform": "youtube", "original_id": video_id,
                        "original_title": title, "original_description": description,
                        "thumbnail": thumbnail, "video_url": f"https://www.youtube.com/watch?v={video_id}",
                        "uploader": uploader, "wrestler": extract_wrestler_from_title(title, uploader),
                        "duration": duration, "view_count": view_count,
                        "upload_timestamp": published, "platform_category": platform
                    })
                except Exception:
                    continue
    except Exception as e:
        print(f"RSS error {feed_url}: {e}")
    return videos

# ============================================
# API ENDPOINTS
# ============================================

@app.get("/")
async def root():
    return {"message": "ReelsDown API Operational", "status": "active", "version": "3.1"}

@app.get("/pingtop/info")
async def get_pingtop_info(url: str = Query(...)):
    """Test PingTop video extraction"""
    result = await get_pingtop_video(url)
    if not result:
        raise HTTPException(status_code=400, detail="Could not extract PingTop video info. Video may be private or URL format unsupported.")
    return result

@app.get("/info")
async def get_video_info(url: str = Query(...)):
    try:
        platform = detect_platform(url)
        fmt = build_format('best', platform)
        opts = get_ytdlp_opts(platform, fmt, '/tmp/info')
        opts.pop('outtmpl', None)
        opts.pop('merge_output_format', None)

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise Exception("Could not extract video info.")

            formats = []
            if info.get('formats'):
                seen = set()
                for f in reversed(info['formats']):
                    h = f.get('height')
                    if h and h not in seen and f.get('vcodec','') != 'none':
                        seen.add(h)
                        formats.append({'quality': f'{h}p', 'format': f'{h}p', 'ext': 'mp4'})
                formats.sort(key=lambda x: int(x['quality'].replace('p','')), reverse=True)

            if not formats:
                formats = [
                    {'quality': '720p', 'format': '720p', 'ext': 'mp4'},
                    {'quality': '480p', 'format': '480p', 'ext': 'mp4'},
                    {'quality': 'Best', 'format': 'best', 'ext': 'mp4'},
                ]
            formats.append({'quality': 'MP3 320kbps', 'format': 'mp3', 'ext': 'mp3'})

            return {
                "title": info.get("title", "Unknown"),
                "thumbnail": info.get("thumbnail", ""),
                "description": (info.get("description") or "")[:500],
                "duration": info.get("duration", 0),
                "uploader": info.get("uploader", ""),
                "view_count": info.get("view_count", 0),
                "like_count": info.get("like_count", 0),
                "platform": platform,
                "formats": formats[:8],
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/download")
async def download_video(url: str = Query(...), format: str = Query("best")):
    try:
        platform = detect_platform(url)
        is_audio = format in ['mp3', 'audio'] or platform in ['spotify', 'audiomack', 'soundcloud', 'applemusic']

        # ── STRATEGY 1: Platform-specific scrapers ──────────────
        direct_url = None

        if platform == 'pingtop':
            result = await get_pingtop_video(url)
            if result and result.get('video_url'):
                direct_url = result['video_url']
                # Stream directly
                async def stream_pingtop():
                    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                        async with client.stream("GET", direct_url, headers={"User-Agent": MOBILE_UA}) as response:
                            async for chunk in response.aiter_bytes(8192):
                                yield chunk
                title = result.get('title', 'pingtop_video')
                filename = f"{clean_filename(title)}.mp4"
                return StreamingResponse(
                    stream_pingtop(),
                    media_type="video/mp4",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'}
                )
            else:
                raise HTTPException(status_code=400, detail="Could not extract PingTop video. The video may be private.")

        if platform == 'tiktok':
            direct_url = await get_tiktok_url(url)
        elif platform == 'twitter':
            direct_url = await get_twitter_url(url)
        elif platform == 'instagram':
            direct_url = await get_instagram_url(url)

        if direct_url:
            # Stream directly from platform CDN
            async def stream_direct():
                async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                    async with client.stream("GET", direct_url, headers={"User-Agent": MOBILE_UA}) as response:
                        async for chunk in response.aiter_bytes(8192):
                            yield chunk

            ext = 'mp3' if is_audio else 'mp4'
            media_type = "audio/mpeg" if is_audio else "video/mp4"
            return StreamingResponse(
                stream_direct(),
                media_type=media_type,
                headers={"Content-Disposition": f'attachment; filename="video.{ext}"'}
            )

        # ── STRATEGY 2: yt-dlp ─────────────────────────────────
        uid = uuid.uuid4().hex[:8]
        ext = 'mp3' if is_audio else 'mp4'
        output_template = f"/tmp/{uid}.%(ext)s"
        fmt = build_format(format, platform)
        opts = get_ytdlp_opts(platform, fmt, output_template, is_audio)

        try:
            info = None
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                title = info.get("title", "video") if info else "video"
            filename = f"{clean_filename(title)}.{ext}"
        except Exception:
            filename = f"video.{ext}"

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        file_path = next((os.path.join("/tmp", f) for f in os.listdir("/tmp") if f.startswith(uid)), None)
        if not file_path:
            raise HTTPException(status_code=500, detail=f"Download failed for {platform}. Please try again.")

        media_type = "audio/mpeg" if is_audio else "video/mp4"

        def iterfile():
            with open(file_path, "rb") as f: yield from f
            try: os.unlink(file_path)
            except: pass

        return StreamingResponse(iterfile(), media_type=media_type,
                                 headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download error: {str(e)}")

@app.get("/preview")
async def get_preview_url(url: str = Query(...)):
    try:
        platform = detect_platform(url)

        # Platform-specific scrapers for preview
        if platform == 'tiktok':
            v = await get_tiktok_url(url)
            if v: return {"video_url": v, "thumbnail": "", "title": "", "platform": platform}

        if platform == 'twitter':
            v = await get_twitter_url(url)
            if v: return {"video_url": v, "thumbnail": "", "title": "", "platform": platform}

        # yt-dlp fallback
        fmt = build_format('best', platform)
        opts = get_ytdlp_opts(platform, fmt, '/tmp/preview')
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "video_url": info.get('url', ''),
                "thumbnail": info.get('thumbnail', ''),
                "title": info.get('title', ''),
                "platform": platform,
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/fetch/raw")
async def fetch_raw_videos():
    all_videos = []
    tasks = [fetch_single_rss(f, "wwe") for f in WWE_RSS_FEEDS] + [fetch_single_rss(f, "aew") for f in AEW_RSS_FEEDS]
    for res in await asyncio.gather(*tasks):
        all_videos.extend(res)
    with open(FETCH_STATE_FILE, 'r') as f: state = json.load(f)
    state.update({"last_fetch": datetime.now().isoformat(), "total_fetched": len(all_videos),
                  "wwe_count": sum(1 for v in all_videos if v['platform_category']=='wwe'),
                  "aew_count": sum(1 for v in all_videos if v['platform_category']=='aew')})
    with open(FETCH_STATE_FILE, 'w') as f: json.dump(state, f)
    return {"success": True, "videos": all_videos, "total": len(all_videos)}

@app.get("/fetch/new")
async def fetch_new_videos(since: str = Query(None)):
    all_videos = []
    tasks = [fetch_single_rss(f, "wwe") for f in WWE_RSS_FEEDS] + [fetch_single_rss(f, "aew") for f in AEW_RSS_FEEDS]
    for res in await asyncio.gather(*tasks):
        all_videos.extend(res)
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace('Z', '+00:00'))
            all_videos = [v for v in all_videos if datetime.fromisoformat(v['upload_timestamp'].replace('Z', '+00:00')) > since_dt]
        except Exception:
            pass
    return {"success": True, "videos": all_videos, "total": len(all_videos)}

@app.get("/status")
async def get_status():
    try:
        with open(FETCH_STATE_FILE, 'r') as f: state = json.load(f)
    except: state = {}
    return {"status": "operational", "version": "3.1", "fetch_state": state}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
