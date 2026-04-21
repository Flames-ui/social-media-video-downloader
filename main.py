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

def get_ydl_opts(platform, fmt, output):
    base = {
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': False,
        'no_color': True,
        'socket_timeout': 60,
        'retries': 5,
        'fragment_retries': 5,
        'skip_unavailable_fragments': True,
        'keepvideo': False,
        'format': fmt,
        'outtmpl': output,
        'merge_output_format': 'mp4',
    }

    if platform == 'youtube':
        base['http_headers'] = {'User-Agent': CHROME_UA, 'Accept-Language': 'en-US,en;q=0.9'}
        base['extractor_args'] = {'youtube': {'player_client': ['android', 'web']}}

    elif platform == 'tiktok':
        base['http_headers'] = {
            'User-Agent': MOBILE_UA,
            'Referer': 'https://www.tiktok.com/',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        base['format'] = 'download_addr-0/download_addr/play_addr_h264/play_addr/bestvideo+bestaudio/best'
        base['extractor_args'] = {'tiktok': {'webpage_download': True}}

    elif platform == 'instagram':
        base['http_headers'] = {
            'User-Agent': MOBILE_UA,
            'Referer': 'https://www.instagram.com/',
            'x-ig-app-id': '936619743392459',
        }

    elif platform == 'facebook':
        base['http_headers'] = {'User-Agent': CHROME_UA, 'Referer': 'https://www.facebook.com/'}

    elif platform == 'twitter':
        base['http_headers'] = {
            'User-Agent': CHROME_UA,
            'Referer': 'https://twitter.com/',
            'Authorization': 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA',
        }
        base['extractor_args'] = {'twitter': {'api': ['syndication', 'graphql']}}

    elif platform == 'threads':
        base['http_headers'] = {'User-Agent': MOBILE_UA, 'Referer': 'https://www.threads.net/'}

    elif platform == 'linkedin':
        base['http_headers'] = {'User-Agent': CHROME_UA, 'Referer': 'https://www.linkedin.com/'}

    elif platform in ['spotify', 'applemusic', 'audiomack', 'soundcloud']:
        base['format'] = 'bestaudio/best'
        base['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}]
        base['merge_output_format'] = 'mp3'
        if platform == 'audiomack':
            base['http_headers'] = {'User-Agent': CHROME_UA, 'Referer': 'https://audiomack.com/'}
        elif platform == 'soundcloud':
            base['http_headers'] = {'User-Agent': CHROME_UA, 'Referer': 'https://soundcloud.com/'}
        else:
            base['http_headers'] = {'User-Agent': CHROME_UA}
    else:
        base['http_headers'] = {'User-Agent': CHROME_UA}

    return base

def build_format(fmt, platform):
    if platform in ['spotify', 'audiomack', 'soundcloud', 'applemusic']: return 'bestaudio/best'
    if platform == 'tiktok': return 'download_addr-0/download_addr/play_addr_h264/play_addr/bestvideo+bestaudio/best'
    if fmt in ['mp3', 'audio']: return 'bestaudio/best'
    return {
        'best':  'bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        '1080p': 'bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[height<=1080]/best',
        '720p':  'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[height<=720]/best',
        '480p':  'bestvideo[ext=mp4][height<=480]+bestaudio[ext=m4a]/best[height<=480]/best',
        '360p':  'bestvideo[ext=mp4][height<=360]+bestaudio[ext=m4a]/best[height<=360]/best',
        'hd':    'bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[height<=1080]/best',
        'sd':    'bestvideo[ext=mp4][height<=480]+bestaudio/best[height<=480]/best',
    }.get(fmt, 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best')

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
                    title = (entry.find('.//{*}title') or type('', (), {'text': 'Wrestling Video'})()).text
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

@app.get("/")
async def root():
    return {"message": "ReelsDown API Operational", "status": "active", "version": "2.1"}

@app.get("/info")
async def get_video_info(url: str = Query(...)):
    try:
        platform = detect_platform(url)
        opts = get_ydl_opts(platform, build_format('best', platform), '/tmp/info')
        opts.pop('outtmpl', None)
        opts.pop('merge_output_format', None)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info: raise Exception("Could not extract info. URL may be private or unsupported.")
            formats = []
            if info.get('formats'):
                seen = set()
                for f in reversed(info['formats']):
                    h = f.get('height')
                    if h and h not in seen and f.get('vcodec', '') != 'none':
                        seen.add(h)
                        formats.append({'quality': f'{h}p', 'format': f'{h}p', 'ext': 'mp4', 'filesize': f.get('filesize')})
                formats.sort(key=lambda x: int(x['quality'].replace('p','')), reverse=True)
            if not formats:
                formats.append({'quality': 'Best Quality', 'format': 'best', 'ext': 'mp4'})
            if platform not in ['spotify', 'applemusic']:
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
        try:
            with yt_dlp.YoutubeDL(get_ydl_opts(platform, 'best', '/tmp/dummy')) as ydl:
                info = ydl.extract_info(url, download=False)
                title = info.get("title", "video") if info else "video"
        except Exception:
            title = "video"

        uid = uuid.uuid4().hex[:8]
        ext = 'mp3' if is_audio else 'mp4'
        filename = f"{clean_filename(title)}.{ext}"
        output_template = f"/tmp/{uid}.%(ext)s"
        fmt = build_format(format, platform)
        opts = get_ydl_opts(platform, fmt, output_template)

        if is_audio and platform not in ['spotify', 'applemusic'] and 'postprocessors' not in opts:
            opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320' if format == 'mp3' else '128'}]

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        file_path = next((os.path.join("/tmp", f) for f in os.listdir("/tmp") if f.startswith(uid)), None)
        if not file_path:
            raise HTTPException(status_code=500, detail=f"Download failed for {platform}. Try again.")

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
        opts = get_ydl_opts(platform, build_format('best', platform), '/tmp/preview')
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {"video_url": info.get('url',''), "thumbnail": info.get('thumbnail',''), "title": info.get('title',''), "platform": platform}
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
    return {"status": "operational", "version": "2.1", "fetch_state": state}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
