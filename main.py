import os
import uuid
import re
import unicodedata
import subprocess
import json
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
from dotenv import load_dotenv

app = FastAPI()

load_dotenv()

app.add_middleware(CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def clean_filename(filename):
    """Remove emojis and special characters from filename"""
    filename = unicodedata.normalize('NFKD', filename)
    filename = filename.encode('ASCII', 'ignore').decode('ASCII')
    filename = re.sub(r'[^\w\s-]', '', filename)
    filename = re.sub(r'[-\s]+', '_', filename)
    filename = filename.strip('_')
    if not filename:
        filename = "video"
    return filename

# ENHANCED: YouTube-specific options to avoid blocking
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

@app.get("/")
async def root():
    return {
        "message": "Welcome to ReelsDown Video Downloader API",
        "status": "operational",
        "endpoints": {
            "/info": "GET video metadata",
            "/download": "GET download video file"
        },
        "formats": ["best", "hd", "sd", "audio"],
        "supported_platforms": [
            "YouTube (may have restrictions)",
            "TikTok",
            "Instagram",
            "Facebook",
            "Twitter/X",
            "Reddit",
            "Vimeo"
        ],
        "note": "YouTube may block datacenter IPs. If downloads fail, try again later."
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
