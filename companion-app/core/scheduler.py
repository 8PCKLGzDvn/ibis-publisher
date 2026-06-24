"""
Ibis Publisher · scheduler.py  v1.12
Background posting engine.

With Facebook native scheduling:
  - Posts ≤29 days away: uploaded immediately to Facebook with scheduled_publish_time.
    Facebook handles the actual publishing. Computer can be off.
  - Posts >29 days away: held in local queue until they enter the 29-day window,
    then uploaded to Facebook automatically.
  - "Post Now": published immediately.

The daemon runs every 60 seconds to:
  1. Upload newly-ready posts to Facebook (those entering the 29-day window)
  2. Handle any "post now" requests
  3. Retry failed uploads
  4. Notify on token expiry
"""

import threading
import time
import sqlite3
from datetime import datetime, timedelta
from typing import Callable, Optional

from core.db import (
    get_setting, set_setting, update_post_status,
    log_event, purge_old_logs, handle_missed_posts
)
from core.facebook_api import FacebookClient, FacebookAuthError, attempt_post
from core.notifications import send as notify

POLL_INTERVAL   = 60       # seconds between checks
UPLOAD_WINDOW   = 29       # days — upload to Facebook when post is within this many days
TOKEN_WARN_DAYS = 10       # warn user when token expires within this many days


class PostingEngine:
    def __init__(self, conn: sqlite3.Connection,
                 notify_fn: Callable[[str, str], None],
                 on_status_change: Callable = None):
        self.conn             = conn
        self.notify           = notify_fn
        self.on_status_change = on_status_change or (lambda: None)
        self._stop            = threading.Event()
        self._paused          = False
        self._client: Optional[FacebookClient] = None

    def start(self):
        t = threading.Thread(target=self._loop, daemon=True, name='IbisScheduler')
        t.start()

    def stop(self):
        self._stop.set()

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False
        self._rebuild_client()

    def _rebuild_client(self) -> bool:
        page_id = get_setting(self.conn, 'page_id')
        token   = get_setting(self.conn, 'page_access_token')
        if not page_id or not token:
            self._client = None
            return False
        self._client = FacebookClient(page_id, token)
        return True

    def _loop(self):
        self._rebuild_client()
        handle_missed_posts(self.conn)
        purge_old_logs(self.conn)
        self._cleanup_old_exports()
        self._check_token_expiry()

        last_token_check = datetime.utcnow()
        last_cleanup = datetime.utcnow()

        while not self._stop.wait(POLL_INTERVAL):
            if self._paused:
                continue

            if not self._client:
                if not self._rebuild_client():
                    continue

            # Token expiry check once per hour
            if (datetime.utcnow() - last_token_check).seconds > 3600:
                self._check_token_expiry()
                last_token_check = datetime.utcnow()

            # Daily cleanup of old exports
            if (datetime.utcnow() - last_cleanup).total_seconds() > 86400:
                self._cleanup_old_exports()
                last_cleanup = datetime.utcnow()

            self._process_posts()
            self.on_status_change()

    def _process_posts(self):
        """
        Find posts that need to be uploaded to Facebook now.
        This includes:
          - Posts with status='scheduled' that are within UPLOAD_WINDOW days
          - Posts with status='retrying' whose next_attempt_at has passed
          - Any post with status='posting' that got stuck (shouldn't happen)
        """
        now      = datetime.utcnow()
        window   = now + timedelta(days=UPLOAD_WINDOW)
        now_iso  = now.strftime('%Y-%m-%d %H:%M:%S')
        win_iso  = window.strftime('%Y-%m-%d %H:%M:%S')

        # Find posts ready to upload:
        # 1. Scheduled posts within the 29-day upload window
        # 2. Retrying posts whose retry time has passed
        posts = self.conn.execute("""
            SELECT * FROM posts
            WHERE (
                status = 'scheduled'
                AND scheduled_time <= ?
                AND scheduled_time > datetime('now', '-1 hour')
            ) OR (
                status = 'retrying'
                AND next_attempt_at <= ?
            )
            ORDER BY scheduled_time ASC
        """, (win_iso, now_iso)).fetchall()

        for row in posts:
            self._upload_post(dict(row))

    def _upload_post(self, post: dict):
        """Upload a single post to Facebook (with native scheduling or immediate)."""
        pid = post['id']

        # Mark as uploading
        update_post_status(self.conn, pid,
                           status='posting',
                           last_attempt_at=datetime.utcnow().isoformat())
        log_event(self.conn, pid, 'attempt')

        success, fb_id, error = attempt_post(self._client, post)

        if success:
            # Always fb_scheduled — scheduler never posts live, only to FB planner
            scheduled_time = post.get('scheduled_time') or ''
            update_post_status(self.conn, pid,
                               status='fb_scheduled',
                               facebook_post_id=fb_id,
                               posted_at=None,
                               last_error=None)
            log_event(self.conn, pid, 'success',
                      response_body='fb_post_id=' + fb_id)
            print('FB-scheduled #' + str(pid) + ' for ' + scheduled_time)
            if get_setting(self.conn, 'notify_on_success') == '1':
                cap = (post.get('caption') or '')[:60]
                notify('Ibis Publisher', 'Scheduled for ' + scheduled_time[:16])
        else:
            attempt_count = post.get('attempt_count', 0) + 1
            is_auth    = error.startswith('AUTH_ERROR')
            is_fatal   = is_auth or \
                         error.startswith('FILE_NOT_FOUND') or \
                         'HTTP 400' in error or \
                         'HTTP 403' in error or \
                         attempt_count >= 3

            if is_fatal:
                update_post_status(self.conn, pid,
                                   status='failed',
                                   attempt_count=attempt_count,
                                   last_error=error)
                log_event(self.conn, pid, 'failure', error_message=error)
                print(f'❌ Failed #{pid}: {error}')
                if get_setting(self.conn, 'notify_on_failure') == '1':
                    notify('Ibis Publisher — Upload Failed',
                           f'Post #{pid} failed: {error[:80]}')
                if is_auth:
                    self._paused = True
                    notify('Ibis Publisher — Token Expired',
                           'Go to Settings and reconnect your Facebook account.')
            else:
                # Retry with backoff
                delay    = [5, 10, 20][min(attempt_count - 1, 2)]
                next_try = (datetime.utcnow() + timedelta(minutes=delay)).isoformat()
                update_post_status(self.conn, pid,
                                   status='retrying',
                                   attempt_count=attempt_count,
                                   last_error=error,
                                   next_attempt_at=next_try)
                log_event(self.conn, pid, 'retry', error_message=error)
                print(f'🔄 Will retry #{pid} in {delay}min: {error}')

    def _cleanup_old_exports(self):
        """
        Delete exported files 30 days after the scheduled_time of the post.
        This ensures files stay available throughout the entire FB scheduling window
        and are only purged well after the post has gone live.
        """
        import os
        cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
        old_posts = self.conn.execute("""
            SELECT photo_path, photo_path_2 FROM posts
            WHERE status IN ('posted','fb_scheduled') AND scheduled_time < ?
        """, (cutoff,)).fetchall()
        for row in old_posts:
            for path in (row[0], row[1] if len(row) > 1 else None):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                        print(f'🗑 Purged: {os.path.basename(path)}')
                    except Exception:
                        pass

    def _check_token_expiry(self):
        expiry_str = get_setting(self.conn, 'token_expiry')
        if not expiry_str:
            return
        try:
            expiry = datetime.fromisoformat(expiry_str)
            days   = (expiry - datetime.utcnow()).days
            if days <= TOKEN_WARN_DAYS:
                self.notify(
                    'Ibis Publisher — Token Expiring',
                    f'Your Facebook token expires in {days} day(s).\n'
                    'Open Settings → Reconnect to refresh it.'
                )
        except Exception:
            pass
