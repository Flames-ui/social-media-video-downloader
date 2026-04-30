# PingTop video extraction
# Handles: https://sl.ping.top/xxxxx share URLs
# Strategy: Fetch page with browser headers, extract og:video or direct CDN URL

import re
import httpx
from typing import Optional

CHROME_UA = 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36'

async def get_pingtop_video(url: str) -> Optional[dict]:
    """
    Extract direct video URL from a PingTop share link.
    Returns dict with video_url, thumbnail, title or None if failed.
    """
    headers = {
        'User-Agent': CHROME_UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
        'Referer': 'https://www.pingtop.com/',
    }

    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers=headers
        ) as client:
            r = await client.get(url)
            html = r.text

            result = {
                'video_url': None,
                'thumbnail': None,
                'title': 'PingTop Video',
                'platform': 'pingtop',
            }

            # Extract og:video (direct MP4 URL)
            og_video = re.search(r'<meta[^>]+property=["\']og:video["\'][^>]+content=["\']([^"\']+)["\']', html)
            if not og_video:
                og_video = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:video["\']', html)
            if og_video:
                result['video_url'] = og_video.group(1)

            # Extract og:video:url as fallback
            if not result['video_url']:
                og_video_url = re.search(r'<meta[^>]+property=["\']og:video:url["\'][^>]+content=["\']([^"\']+)["\']', html)
                if not og_video_url:
                    og_video_url = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:video:url["\']', html)
                if og_video_url:
                    result['video_url'] = og_video_url.group(1)

            # Extract direct video URL from page source (CDN links)
            if not result['video_url']:
                # Look for common CDN video URL patterns
                cdn_patterns = [
                    r'(https?://[^"\'>\s]+\.mp4[^"\'>\s]*)',
                    r'(https?://[^"\'>\s]*cdn[^"\'>\s]+\.mp4[^"\'>\s]*)',
                    r'(https?://[^"\'>\s]*video[^"\'>\s]+\.mp4[^"\'>\s]*)',
                    r'"videoUrl"\s*:\s*"([^"]+)"',
                    r'"video_url"\s*:\s*"([^"]+)"',
                    r'"src"\s*:\s*"([^"]+\.mp4[^"]*)"',
                    r'source\s+src=["\']([^"\']+\.mp4[^"\']*)["\']',
                ]
                for pattern in cdn_patterns:
                    match = re.search(pattern, html)
                    if match:
                        result['video_url'] = match.group(1)
                        break

            # Extract thumbnail
            og_image = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html)
            if not og_image:
                og_image = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html)
            if og_image:
                result['thumbnail'] = og_image.group(1)

            # Extract title
            og_title = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html)
            if not og_title:
                og_title = re.search(r'<title>([^<]+)</title>', html)
            if og_title:
                result['title'] = og_title.group(1).strip()

            return result if result['video_url'] else None

    except Exception as e:
        print(f"PingTop extraction error: {e}")
        return None
