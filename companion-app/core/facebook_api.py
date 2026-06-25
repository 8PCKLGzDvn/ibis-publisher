"""
Ibis Publisher · facebook_api.py  v1.12
Facebook Graph API client.

Two posting modes:
  - schedule_photo()  → uploads photo NOW, tells Facebook to publish it at a future time.
                        Computer does NOT need to be on at post time.
  - post_photo_now()  → publishes immediately (for "Post Now" button).
"""

import requests
import os
import mimetypes
from pathlib import Path
from typing import Tuple
from datetime import datetime, timezone

GRAPH_BASE = "https://graph.facebook.com/v25.0"


def _local_dt_to_utc(dt: datetime) -> float:
    """
    Convert a naive local datetime to a UTC Unix timestamp.

    Uses the IANA timezone from /etc/localtime (macOS) so the conversion
    is driven by the OS timezone database — not the process TZ env variable,
    which launchd can fail to propagate correctly.  Falls back to time.mktime
    if zoneinfo is unavailable.
    """
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+
        tz = ZoneInfo('America/Santiago')
        return dt.replace(tzinfo=tz).timestamp()
    except Exception:
        pass
    import time
    return time.mktime(dt.timetuple())


class FacebookAPIError(Exception):
    def __init__(self, message: str, http_status: int = 0):
        super().__init__(message)
        self.http_status = http_status

class FacebookAuthError(FacebookAPIError):
    pass

class FacebookRateLimitError(FacebookAPIError):
    pass


class FacebookClient:
    def __init__(self, page_id: str, page_access_token: str):
        self.page_id = page_id
        self.token   = page_access_token
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'IbisPublisher/1.12'})

    def _page_url(self, endpoint: str = '') -> str:
        return f"{GRAPH_BASE}/{self.page_id}{endpoint}"

    def _check_response(self, resp: requests.Response) -> dict:
        body = {}
        try:
            body = resp.json()
        except Exception:
            pass
        if resp.status_code == 401:
            raise FacebookAuthError("Token invalid or expired.", http_status=401)
        if resp.status_code == 403:
            msg = body.get('error', {}).get('message', 'Permission denied')
            raise FacebookAPIError(f"Permission error: {msg}", http_status=403)
        if resp.status_code == 429:
            raise FacebookRateLimitError("Rate limit reached.", http_status=429)
        if resp.status_code >= 500:
            raise FacebookAPIError(f"Facebook server error {resp.status_code}", http_status=resp.status_code)
        if resp.status_code >= 400:
            err = body.get('error', {})
            raise FacebookAPIError(err.get('message', f'HTTP {resp.status_code}'), http_status=resp.status_code)
        return body

    def _to_unix(self, scheduled_time_iso: str) -> int:
        """Convert 'YYYY-MM-DD HH:MM:00' (local) to UTC Unix timestamp."""
        dt = datetime.strptime(scheduled_time_iso[:16], '%Y-%m-%d %H:%M')
        return int(_local_dt_to_utc(dt))

    # ── Schedule a photo (Facebook handles the timing) ────────────
    def schedule_photo(self, photo_path: str, caption: str,
                       scheduled_time_iso: str) -> Tuple[str, str]:
        """
        Upload photo NOW, schedule via /feed with attached_media.
        Two-step method for better distribution:
        1. Upload photo as unpublished
        2. Create scheduled /feed post with attached photo
        Facebook handles publishing at the scheduled time.
        Computer does NOT need to be on at post time.
        """
        unix_ts = self._to_unix(scheduled_time_iso)
        now_ts  = int(datetime.now().timestamp())

        # Facebook requires at least 10 minutes in the future
        if unix_ts < now_ts + 600:
            unix_ts = now_ts + 600

        photo_fbid = self._upload_unpublished_photo(photo_path)

        resp = self.session.post(
            self._page_url('/feed'),
            data={
                'message':                caption,
                'access_token':           self.token,
                'attached_media':         f'[{{"media_fbid":"{photo_fbid}"}}]',
                'published':              'false',
                'scheduled_publish_time': str(unix_ts),
            },
            timeout=60,
        )

        body    = self._check_response(resp)
        post_id = body.get('id', '')
        return post_id, scheduled_time_iso

    # ── Post immediately ──────────────────────────────────────────
    def _upload_unpublished_photo(self, photo_path: str) -> str:
        """Upload a photo without publishing it. Returns photo_fbid."""
        path = Path(photo_path)
        if not path.exists():
            raise FacebookAPIError(f"Photo file not found: {photo_path}")
        mime = mimetypes.guess_type(str(path))[0] or 'image/jpeg'
        with open(path, 'rb') as fh:
            resp = self.session.post(
                self._page_url('/photos'),
                data={
                    'access_token': self.token,
                    'published':    'false',
                },
                files={'source': (path.name, fh, mime)},
                timeout=120,
            )
        body = self._check_response(resp)
        return body.get('id', '')

    def post_photo_now(self, photo_path: str, caption: str) -> Tuple[str, str]:
        """
        Publish a photo immediately using the two-step method:
        1. Upload photo as unpublished (gets a photo_fbid)
        2. Create a /feed post with attached_media
        This mimics how native Facebook posts work and gets better distribution
        than posting directly to /photos with published=true.
        """
        photo_fbid = self._upload_unpublished_photo(photo_path)

        resp = self.session.post(
            self._page_url('/feed'),
            data={
                'message':        caption,
                'access_token':   self.token,
                'attached_media': f'[{{"media_fbid":"{photo_fbid}"}}]',
            },
            timeout=60,
        )

        body    = self._check_response(resp)
        post_id = body.get('id', '')
        return post_id, datetime.utcnow().isoformat()

    def update_post_message(self, fb_post_id: str, new_caption: str) -> bool:
        """Update the caption of an existing scheduled Facebook post in-place."""
        resp = self.session.post(
            f"{GRAPH_BASE}/{fb_post_id}",
            data={
                'message':      new_caption,
                'access_token': self.token,
            },
            timeout=30,
        )
        self._check_response(resp)
        return True

    def get_post_thumbnail(self, fb_post_id: str) -> str:
        resp = self.session.get(
            f"{GRAPH_BASE}/{fb_post_id}",
            params={'access_token': self.token, 'fields': 'full_picture'},
            timeout=15,
        )
        data = self._check_response(resp)
        return data.get('full_picture', '')

    def verify_token(self) -> bool:
        try:
            resp = self.session.get(
                f"{GRAPH_BASE}/{self.page_id}",
                params={'access_token': self.token, 'fields': 'id,name'},
                timeout=15,
            )
            self._check_response(resp)
            return True
        except (FacebookAuthError, FacebookAPIError):
            return False


# ── Dispatch function used by scheduler and post-now route ───────

def attempt_post(client: FacebookClient, post: dict, force_immediate: bool = False) -> Tuple[bool, str, str]:
    """
    Upload a post to Facebook.

    force_immediate=True  → publish right now (used by Post Now button only)
    force_immediate=False → always use Facebook native scheduling, pushing time
                            forward if needed to meet the 11-minute minimum.

    Returns (success, facebook_post_id, error_message).
    """
    caption    = post.get('caption') or ''
    photo_path = post.get('photo_path') or ''

    if force_immediate:
        try:
            post_id, ts = client.post_photo_now(photo_path, caption)
            return True, post_id, ''
        except FacebookAuthError as e:
            return False, '', f'AUTH_ERROR: {e}'
        except FacebookAPIError as e:
            return False, '', f'API_ERROR ({e.http_status}): {e}'
        except Exception as e:
            return False, '', f'UNEXPECTED: {e}'

    # Always use Facebook native scheduling for queued posts
    scheduled_time = post.get('scheduled_time') or ''
    try:
        dt      = datetime.strptime(scheduled_time[:16], '%Y-%m-%d %H:%M')
        unix_ts = int(_local_dt_to_utc(dt))
        now_ts  = int(datetime.now().timestamp())
        days_out = (unix_ts - now_ts) / 86400

        if days_out > 29:
            # Too far out — not uploading yet, caller should not have called us
            return False, '', 'SKIP: post is beyond 29-day Facebook window'
    except Exception:
        pass

    # schedule_photo handles pushing time forward if < 11 mins away
    try:
        post_id, ts = client.schedule_photo(photo_path, caption, scheduled_time)
        return True, post_id, ''
    except FacebookAuthError as e:
        return False, '', f'AUTH_ERROR: {e}'
    except FacebookRateLimitError as e:
        return False, '', f'RATE_LIMIT: {e}'
    except FacebookAPIError as e:
        return False, '', f'API_ERROR ({e.http_status}): {e}'
    except FileNotFoundError:
        return False, '', f'FILE_NOT_FOUND: {photo_path}'
    except requests.exceptions.Timeout:
        return False, '', 'TIMEOUT: Request timed out'
    except requests.exceptions.ConnectionError:
        return False, '', 'NETWORK_ERROR: Could not connect to Facebook'
    except Exception as e:
        return False, '', f'UNEXPECTED: {e}'
