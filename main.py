import os
import uuid
import re
import unicodedata
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
from dotenv import load_dotenv

app = FastAPI()

# Load environment variables from .env file
load_dotenv()

# CORS configuration - Allow all for testing, restrict in production
app.add_middleware(CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for now
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def clean_filename(filename):
    """Remove emojis and special characters from filename"""
    # Normalize unicode characters
    filename = unicodedata.normalize('NFKD', filename)
    # Encode to ASCII, ignore characters that can't be converted
    filename = filename.encode('ASCII', 'ignore').decode('ASCII')
    # Replace any remaining non-alphanumeric characters with underscores
    filename = re.sub(r'[^\w\s-]', '', filename)
    # Replace spaces and dashes with underscores
    filename = re.sub(r'[-\s]+', '_', filename)
    # Remove leading/trailing underscores
    filename = filename.strip('_')
    # If filename is empty, use a default
    if not filename:
        filename = "video"
    return filename

@app.get("/info")
async def get_video_info(url: str = Query(...)):
    """Get video metadata without downloading"""
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            return {
                "title": info.get("title", "Unknown"),
                "thumbnail": info.get("thumbnail", ""),
                "description": info.get("description", "")[:500],
                "duration": info.get("duration", 0),
                "uploader": info.get("uploader", ""),
                "view_count": info.get("view_count", 0),
                "like_count": info.get("like_count", 0),
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error fetching info: {str(e)}")

@app.get("/download")
async def download_video(url: str = Query(...), format: str = Query("best")):
    try:
        # First get metadata to get the clean title
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            original_title = info.get("title", "video")
            clean_title = clean_filename(original_title)
            extension = "mp4"
            filename = f"{clean_title}.{extension}"

        # Create a unique output template
        uid = uuid.uuid4().hex[:8]
        output_template = f"/tmp/{uid}.%(ext)s"

        # FIXED: Smart format selection with fallback
        if format == "best":
            format_string = "best[ext=mp4]/best"
        elif format == "hd":
            format_string = "best[height<=720]/best[ext=mp4]/best"
        elif format == "sd":
            format_string = "best[height<=480]/best[ext=mp4]/best"
        elif format == "audio":
            format_string = "bestaudio/best"
        else:
            format_string = format

        ydl_opts = {
            'format': format_string,
            'outtmpl': output_template,
            'quiet': True,
            'no_warnings': True,
            'merge_output_format': 'mp4',
            'ignoreerrors': True,  # Skip unavailable formats
        }

        # Download the video
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Find the downloaded file
        actual_file_path = None
        for f in os.listdir("/tmp"):
            if f.startswith(uid):
                actual_file_path = os.path.join("/tmp", f)
                break

        if not actual_file_path or not os.path.exists(actual_file_path):
            raise HTTPException(status_code=500, detail="Download failed or file not found.")

        # Stream file with clean filename
        def iterfile():
            with open(actual_file_path, "rb") as f:
                yield from f
            # Clean up after streaming
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
        raise HTTPException(status_code=500, detail=f"Error during download: {str(e)}")

@app.get("/")
async def root():
    return {
        "message": "Welcome to the Social Media Video Downloader API.",
        "endpoints": {
            "/info": "GET video metadata (title, thumbnail, description)",
            "/download": "GET download video file"
        },
        "usage": {
            "info": "/info?url=<video_url>",
            "download": "/download?url=<video_url>&format=<format>",
            "formats": "best, hd, sd, audio"
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
