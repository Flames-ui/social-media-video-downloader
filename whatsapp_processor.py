# whatsapp_processor.py
# WhatsApp video processing service
# Extends the existing ReelsDown backend with FFmpeg-based processing
# Converts, compresses, splits videos for WhatsApp sharing

import os
import asyncio
import subprocess
import uuid
import json
import time
from pathlib import Path
from typing import Optional

# ── Configuration ──────────────────────────────────────────────────────────────

PROCESS_DIR = Path("/tmp/reelsdown_whatsapp")
PROCESS_DIR.mkdir(exist_ok=True)

WHATSAPP_MAX_MB = 16          # WhatsApp file size limit in MB
WHATSAPP_MAX_BYTES = 16 * 1024 * 1024
WHATSAPP_MAX_WIDTH = 720      # Max video width for WhatsApp
WHATSAPP_STATUS_DURATION = 30 # Max seconds for WhatsApp Status clips
JOB_TTL_SECONDS = 3600        # Auto-delete files after 1 hour

# In-memory job store (use Redis in production)
jobs: dict = {}

# ── Job management ─────────────────────────────────────────────────────────────

def create_job(job_type: str, source_url: str = "") -> dict:
    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id,
        "type": job_type,
        "source_url": source_url,
        "status": "queued",       # queued → processing → done / failed
        "progress": 0,
        "created_at": time.time(),
        "outputs": {},
        "error": None,
    }
    jobs[job_id] = job
    return job

def update_job(job_id: str, **kwargs):
    if job_id in jobs:
        jobs[job_id].update(kwargs)

def get_job(job_id: str) -> Optional[dict]:
    return jobs.get(job_id)

def cleanup_old_jobs():
    """Remove jobs and files older than JOB_TTL_SECONDS"""
    now = time.time()
    to_delete = [jid for jid, j in jobs.items() if now - j["created_at"] > JOB_TTL_SECONDS]
    for jid in to_delete:
        job = jobs.pop(jid)
        # Delete associated files
        for key, path in job.get("outputs", {}).items():
            if isinstance(path, str) and os.path.exists(path):
                try:
                    os.unlink(path)
                except:
                    pass
        # Delete any temp files for this job
        for f in PROCESS_DIR.glob(f"{jid}*"):
            try:
                f.unlink()
            except:
                pass

# ── FFmpeg helpers ─────────────────────────────────────────────────────────────

def ffprobe_info(input_path: str) -> dict:
    """Get video metadata using ffprobe"""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        input_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        
        video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
        audio_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
        fmt = data.get("format", {})
        
        duration = float(fmt.get("duration", 0))
        size = int(fmt.get("size", 0))
        width = int(video_stream.get("width", 0))
        height = int(video_stream.get("height", 0))
        
        return {
            "duration": duration,
            "size_bytes": size,
            "size_mb": size / (1024 * 1024),
            "width": width,
            "height": height,
            "video_codec": video_stream.get("codec_name", ""),
            "audio_codec": audio_stream.get("codec_name", ""),
            "fps": eval(video_stream.get("r_frame_rate", "30/1")),
        }
    except Exception as e:
        print(f"ffprobe error: {e}")
        return {"duration": 0, "size_bytes": 0, "size_mb": 0, "width": 0, "height": 0}

def calculate_target_bitrate(duration: float, target_mb: float = 15.0) -> int:
    """
    Calculate video bitrate to hit target file size.
    Formula: (target_size_bits - audio_bits) / duration
    """
    if duration <= 0:
        return 1000  # 1 Mbps fallback
    
    target_bits = target_mb * 8 * 1024 * 1024
    audio_bits = 128 * 1024 * duration  # 128kbps audio
    video_bits = target_bits - audio_bits
    bitrate_kbps = int(video_bits / duration / 1024)
    
    # Clamp between 200kbps and 4000kbps
    return max(200, min(4000, bitrate_kbps))

async def run_ffmpeg(cmd: list, job_id: str = "", step: str = "") -> bool:
    """Run FFmpeg command asynchronously"""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        
        if proc.returncode != 0:
            print(f"FFmpeg error [{step}]: {stderr.decode()[:500]}")
            return False
        return True
    except asyncio.TimeoutError:
        print(f"FFmpeg timeout [{step}]")
        return False
    except Exception as e:
        print(f"FFmpeg exception [{step}]: {e}")
        return False

# ── Processing functions ───────────────────────────────────────────────────────

async def convert_to_mp4(input_path: str, output_path: str, job_id: str = "") -> bool:
    """
    Convert any video to MP4 with H.264 + AAC.
    Mobile-compatible, fast preset for speed.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-c:v", "libx264",      # H.264 codec — universal mobile support
        "-preset", "fast",       # Balance between speed and compression
        "-crf", "23",            # Quality factor (18=best, 28=worst, 23=default)
        "-c:a", "aac",           # AAC audio — WhatsApp compatible
        "-b:a", "128k",          # Audio bitrate
        "-movflags", "+faststart", # Move metadata to front for fast streaming
        "-pix_fmt", "yuv420p",   # Pixel format for max compatibility
        output_path
    ]
    return await run_ffmpeg(cmd, job_id, "convert_mp4")

async def compress_for_whatsapp(input_path: str, output_path: str, job_id: str = "") -> bool:
    """
    Compress video to fit WhatsApp 16MB limit.
    Scales down width to 720px max, calculates bitrate dynamically.
    """
    info = ffprobe_info(input_path)
    
    # Already small enough? Just convert codec if needed
    if info["size_mb"] <= 15 and info["width"] <= 720:
        return await convert_to_mp4(input_path, output_path, job_id)
    
    duration = info["duration"] or 60
    target_bitrate = calculate_target_bitrate(duration, target_mb=14.0)
    
    # Build scale filter — only scale down if wider than 720
    scale_filter = "scale='min(720,iw)':'-2'" if info["width"] > 720 else "scale=iw:ih"
    
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-c:v", "libx264",
        "-preset", "fast",
        "-b:v", f"{target_bitrate}k",  # Dynamic bitrate for target size
        "-maxrate", f"{target_bitrate * 2}k",
        "-bufsize", f"{target_bitrate * 4}k",
        "-vf", scale_filter,
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        output_path
    ]
    return await run_ffmpeg(cmd, job_id, "compress_whatsapp")

async def extract_audio_mp3(input_path: str, output_path: str, job_id: str = "") -> bool:
    """Extract audio track as MP3 at 320kbps"""
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vn",                    # No video
        "-c:a", "libmp3lame",     # MP3 codec
        "-b:a", "320k",           # 320kbps quality
        "-ar", "44100",           # Sample rate
        output_path
    ]
    return await run_ffmpeg(cmd, job_id, "extract_audio")

async def split_into_clips(input_path: str, job_id: str, clip_duration: int = 30) -> list:
    """
    Split video into clips of clip_duration seconds each.
    Used for WhatsApp Status (max 30 seconds per clip).
    Returns list of output file paths.
    """
    info = ffprobe_info(input_path)
    total_duration = info["duration"]
    
    if total_duration <= clip_duration:
        # No splitting needed — compress and return as single clip
        output = str(PROCESS_DIR / f"{job_id}_clip_1.mp4")
        success = await compress_for_whatsapp(input_path, output, job_id)
        return [output] if success else []
    
    clips = []
    num_clips = int(total_duration / clip_duration) + (1 if total_duration % clip_duration > 2 else 0)
    
    for i in range(num_clips):
        start = i * clip_duration
        output = str(PROCESS_DIR / f"{job_id}_clip_{i+1}.mp4")
        
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-ss", str(start),           # Start time
            "-t", str(clip_duration),    # Duration
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "26",                # Slightly lower quality for clips
            "-vf", "scale='min(720,iw)':'-2'",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            output
        ]
        
        success = await run_ffmpeg(cmd, job_id, f"split_clip_{i+1}")
        if success and os.path.exists(output):
            clips.append(output)
    
    return clips

# ── Main processing pipeline ───────────────────────────────────────────────────

async def process_video_pipeline(job_id: str, source_url: str, input_path: str):
    """
    Full processing pipeline:
    1. Convert to MP4 (H.264 + AAC)
    2. Compress for WhatsApp (<16MB, max 720p)
    3. Split into 30s clips if needed
    4. Extract MP3 audio
    """
    try:
        update_job(job_id, status="processing", progress=10)
        outputs = {}
        
        # ── Step 1: Convert to compatible MP4 ──────────────────────────────
        original_mp4 = str(PROCESS_DIR / f"{job_id}_original.mp4")
        update_job(job_id, progress=20)
        
        converted = await convert_to_mp4(input_path, original_mp4, job_id)
        if converted and os.path.exists(original_mp4):
            outputs["original"] = {
                "path": original_mp4,
                "url": f"/download-processed/{job_id}/original",
                "label": "Original MP4 (H.264)",
                "size_mb": round(os.path.getsize(original_mp4) / (1024 * 1024), 2),
            }
        else:
            # Use raw downloaded file if conversion failed
            outputs["original"] = {
                "path": input_path,
                "url": f"/download-processed/{job_id}/raw",
                "label": "Original Video",
                "size_mb": round(os.path.getsize(input_path) / (1024 * 1024), 2) if os.path.exists(input_path) else 0,
            }
        
        work_file = original_mp4 if os.path.exists(original_mp4) else input_path
        
        # ── Step 2: Compress for WhatsApp ──────────────────────────────────
        update_job(job_id, progress=40)
        whatsapp_mp4 = str(PROCESS_DIR / f"{job_id}_whatsapp.mp4")
        
        compressed = await compress_for_whatsapp(work_file, whatsapp_mp4, job_id)
        if compressed and os.path.exists(whatsapp_mp4):
            size_mb = os.path.getsize(whatsapp_mp4) / (1024 * 1024)
            outputs["whatsapp"] = {
                "path": whatsapp_mp4,
                "url": f"/download-processed/{job_id}/whatsapp",
                "label": f"WhatsApp Ready ({size_mb:.1f}MB)",
                "size_mb": round(size_mb, 2),
                "whatsapp_compatible": size_mb < 16,
            }
        
        # ── Step 3: Split into 30s clips ──────────────────────────────────
        update_job(job_id, progress=60)
        info = ffprobe_info(work_file)
        
        if info["duration"] > 30:
            clips = await split_into_clips(work_file, job_id, clip_duration=30)
            outputs["clips"] = [
                {
                    "path": clip,
                    "url": f"/download-processed/{job_id}/clip/{i+1}",
                    "label": f"WhatsApp Status Clip {i+1} (30s)",
                    "size_mb": round(os.path.getsize(clip) / (1024 * 1024), 2),
                }
                for i, clip in enumerate(clips)
                if os.path.exists(clip)
            ]
        else:
            outputs["clips"] = []
        
        # ── Step 4: Extract MP3 audio ──────────────────────────────────────
        update_job(job_id, progress=80)
        audio_mp3 = str(PROCESS_DIR / f"{job_id}_audio.mp3")
        
        audio_ok = await extract_audio_mp3(work_file, audio_mp3, job_id)
        if audio_ok and os.path.exists(audio_mp3):
            outputs["audio"] = {
                "path": audio_mp3,
                "url": f"/download-processed/{job_id}/audio",
                "label": "Audio MP3 (320kbps)",
                "size_mb": round(os.path.getsize(audio_mp3) / (1024 * 1024), 2),
            }
        
        # Clean up temp input file
        if os.path.exists(input_path) and input_path != original_mp4:
            try:
                os.unlink(input_path)
            except:
                pass
        
        update_job(job_id,
                   status="done",
                   progress=100,
                   outputs=outputs,
                   video_info={
                       "duration": info.get("duration", 0),
                       "width": info.get("width", 0),
                       "height": info.get("height", 0),
                   })
        
    except Exception as e:
        print(f"Pipeline error [{job_id}]: {e}")
        update_job(job_id, status="failed", error=str(e), progress=0)
