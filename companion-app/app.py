"""
Ibis Publisher · app.py  v1.9
Web companion app — Flask backend.
Queue shows photo thumbnails + editable captions.
"""

import sys, os, threading, webbrowser, time, json, base64, uuid
from pathlib import Path
from datetime import datetime, timedelta
from io import BytesIO

# Ensure the process uses the macOS system timezone, not whatever launchd inherits.
# Without this, time.mktime() (and therefore all Facebook timestamp conversions)
# can be 1-2 hours off because launchd doesn't always propagate TZ correctly.
def _sync_system_timezone():
    try:
        real = os.path.realpath('/etc/localtime')
        marker = 'zoneinfo/'
        idx = real.find(marker)
        if idx >= 0:
            tz_name = real[idx + len(marker):]
            os.environ['TZ'] = tz_name
            time.tzset()
    except Exception:
        pass

_sync_system_timezone()

sys.path.insert(0, str(Path(__file__).parent))

from core.db import (
    init_db, get_setting, set_setting, get_all_posts,
    delete_post, update_post_status, get_log, handle_missed_posts, log_event,
    get_thumbnail_dir
)
from core.notifications import send as notify
from core.facebook_api import FacebookClient, FacebookAPIError, attempt_post

try:
    from flask import Flask, request, jsonify, redirect, send_file, abort
    from PIL import Image
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'flask', 'Pillow', '--quiet', '--user'])
    from flask import Flask, request, jsonify, redirect, send_file, abort
    from PIL import Image

app  = Flask(__name__)
conn = None

# ── CSS ─────────────────────────────────────────────────────────
CSS = """
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#F0EDEA; color:#343434; display:flex; min-height:100vh; }
.sidebar { width:210px; min-height:100vh; background:#343434; flex-shrink:0;
           position:fixed; top:0; left:0; bottom:0; display:flex; flex-direction:column; }
.sidebar .brand { padding:30px 22px 24px; border-bottom:1px solid rgba(255,255,255,.07); }
.sidebar .brand .brand-name {
    display:block;
    font-size:30px; font-weight:400; letter-spacing:.22em; text-transform:uppercase;
    color:rgba(255,255,255,.28); line-height:1; }
.sidebar .brand .brand-sub {
    display:block; margin-top:5px;
    font-size:9.5px; letter-spacing:.22em; text-transform:uppercase;
    color:rgba(255,255,255,.28); font-weight:400; }
.sidebar nav { flex:1; padding-top:8px; }
.sidebar nav a { display:flex; align-items:center; gap:11px;
                 padding:12px 22px;
                 color:rgba(255,255,255,.48); text-decoration:none;
                 font-size:15px; letter-spacing:.01em;
                 border-left:2px solid transparent;
                 transition:color .15s, background .15s, border-color .15s; }
.sidebar nav a:hover { background:rgba(255,255,255,.04);
                       color:rgba(255,255,255,.82);
                       border-left-color:rgba(255,255,255,.15); }
.sidebar nav a.active { background:rgba(116,154,150,.14); color:white;
                        font-weight:500; border-left-color:#749A96; }
.sidebar nav a svg { flex-shrink:0; }
.sidebar .sidebar-foot { padding:18px 22px;
                          font-size:10px; letter-spacing:.1em; text-transform:uppercase;
                          color:rgba(255,255,255,.18); }
.main { margin-left:210px; padding:32px; flex:1; }
h2 { font-size:22px; font-weight:500; margin-bottom:24px; color:#343434; }
h3 { font-size:16px; font-weight:500; margin-bottom:12px; }
.card { background:white; border-radius:12px; border:1px solid #D6D0CA;
        padding:24px; margin-bottom:20px; }
.card-table { padding:0; overflow:hidden; }
.btn { display:inline-block; padding:8px 16px; border-radius:8px; border:none;
       cursor:pointer; font-size:14px; font-weight:500; text-decoration:none; line-height:1.4; }
.btn-primary   { background:#749A96; color:white; }
.btn-danger    { background:#B56152; color:white; }
.btn-secondary { background:#E5E0DA; color:#343434; }
.btn-success   { background:#749A96; color:white; }
.btn:hover { opacity:.85; }
.btn-sm { padding:5px 12px; font-size:12px; }
label { display:block; font-size:13px; font-weight:500; color:#948466;
        margin-bottom:4px; margin-top:14px; }
input[type=text],input[type=password],input[type=number],select,textarea {
  width:100%; padding:9px 12px; border:1px solid #D6D0CA; border-radius:8px;
  font-size:14px; font-family:inherit; }
input[type=checkbox] { width:auto; margin-right:6px; }
.form-row { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.alert { padding:12px 16px; border-radius:8px; margin-bottom:16px; font-size:14px; }
.alert-success { background:#d4edda; color:#155724; border:1px solid #c3e6cb; }
.alert-error   { background:#f8d7da; color:#721c24; border:1px solid #f5c6cb; }
.alert-info    { background:#d8e8e7; color:#3d6e6b; border:1px solid #b8d8d6; }
.empty { text-align:center; padding:48px 20px; color:#948466; font-size:14px; }
.tabs { display:flex; gap:4px; margin-bottom:20px; flex-wrap:wrap; }
.tabs a { padding:7px 16px; border-radius:8px; font-size:13px; font-weight:500;
          text-decoration:none; color:#948466; background:white; border:1px solid #D6D0CA; }
.tabs a.active { background:#749A96; color:white; border-color:#749A96; }
.badge { display:inline-block; padding:3px 8px; border-radius:4px;
         font-size:11px; font-weight:700; text-transform:uppercase; }
.badge-not-uploaded { background:#e4e0d8; color:#948466; }
.badge-uploading    { background:#fff3cd; color:#7a5c00; }
.badge-scheduled    { background:#d6e0ec; color:#4a6a8a; }
.badge-posted       { background:#d4edda; color:#2a6a3a; }
.badge-success      { background:#d4edda; color:#2a6a3a; }
.badge-failure      { background:#f5d8d4; color:#8a3a2e; }
.badge-deleting     { background:#f5d8d4; color:#8a3a2e; }
.badge-attempt      { background:#d3e4e2; color:#4a7a76; }
.badge-retry        { background:#fff3cd; color:#7a5c00; }

/* Queue table */
.queue-table { width:100%; border-collapse:collapse; }
.queue-table th { text-align:left; padding:10px 14px; background:#EDE9E5;
    font-size:11px; font-weight:600; text-transform:uppercase;
    letter-spacing:.05em; color:#948466; border-bottom:1px solid #D6D0CA; }
.td-photo { width:120px; padding:14px 12px; vertical-align:top; }
.td-caption { padding:14px; vertical-align:top; font-size:14px; line-height:1.6; }
.td-meta { font-size:12px; color:#948466; margin-top:6px; }
.td-actions { width:150px; padding:14px 12px; vertical-align:top; }
.td-actions .btn { display:block; width:100%; text-align:center; margin-bottom:6px; }
.thumb { width:96px; height:96px; object-fit:cover; border-radius:8px;
         border:1px solid #D6D0CA; cursor:pointer; display:block; }
.no-photo { width:96px; height:96px; background:#EDE9E5; border-radius:8px;
            display:flex; align-items:center; justify-content:center;
            font-size:11px; color:#948466; text-align:center; line-height:1.4; }
.caption-text { color:#343434; }
.caption-empty { color:#D6D0CA; font-style:italic; }
.caption-edit { display:none; }
.caption-edit textarea { width:100%; padding:8px 10px; border:1px solid #D6D0CA;
    border-radius:6px; font-size:14px; font-family:inherit; resize:vertical;
    line-height:1.5; }
.edit-actions { margin-top:8px; display:flex; gap:8px; }
.queue-row { border-bottom:1px solid #EDE9E5; transition:background 0.12s, opacity 0.12s, transform 0.12s; }
.queue-row:last-child { border-bottom:none; }
.queue-row.dragging { opacity:0.35; }
.queue-row.drop-target { background:#d8e8e7; box-shadow:inset 3px 0 0 #749A96; }
.queue-row.drop-target td { border-top:2px solid #749A96; }
#reorder-bar { position:sticky; bottom:0; left:0; right:0;
               background:#343434; color:white; padding:14px 24px;
               display:flex; align-items:center; gap:16px; z-index:100;
               box-shadow:0 -2px 12px rgba(0,0,0,.25); }
#reorder-bar.hidden { display:none; }
#reorder-toast { display:none; position:fixed; bottom:80px; right:24px;
                 background:#749A96; color:white; padding:10px 18px;
                 border-radius:8px; font-size:14px; z-index:200;
                 box-shadow:0 2px 8px rgba(0,0,0,.2); }
"""

def layout(page, content):
    pages = [
        ('queue',    '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/></svg>Queue', '/'),
        ('log',      '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"><line x1="8" y1="6" x2="20" y2="6"/><line x1="8" y1="12" x2="20" y2="12"/><line x1="8" y1="18" x2="20" y2="18"/><circle cx="4" cy="6" r="1" fill="currentColor"/><circle cx="4" cy="12" r="1" fill="currentColor"/><circle cx="4" cy="18" r="1" fill="currentColor"/></svg>Activity', '/log'),
        ('schedule', '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 15"/></svg>Schedule', '/schedule'),
        ('settings', '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>Settings', '/settings'),
    ]
    nav = ''
    for key, label, href in pages:
        active = ' class="active"' if key == page else ''
        nav += f'<a href="{href}"{active}>{label}</a>\n'

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ibis Publisher</title>
<style>{CSS}</style>
</head>
<body>
<div class="sidebar">
  <div class="brand">
    <span class="brand-name">Ibis</span>
    <span class="brand-sub">Publisher</span>
  </div>
  <nav>{nav}</nav>
  <div class="sidebar-foot">Ibis · v1.9</div>
</div>
<div class="main">{content}</div>
</body>
</html>'''


# ── Photo serving ────────────────────────────────────────────────


@app.route('/logo')
def serve_logo():
    logo_path = Path(__file__).parent / 'assets' / 'logo.png'
    if logo_path.exists():
        return send_file(str(logo_path), mimetype='image/png')
    abort(404)

def _cancel_fb_post(post_id_local):
    """Delete a scheduled post from Facebook if it exists. Returns True if deleted."""
    row = conn.execute("SELECT facebook_post_id, status FROM posts WHERE id=?", (post_id_local,)).fetchone()
    if not row or not row[0] or row[1] not in ('fb_scheduled', 'posted'):
        return False
    fb_post_id = row[0]
    page_id = get_setting(conn, 'page_id')
    token   = get_setting(conn, 'page_access_token')
    if not page_id or not token:
        return False
    try:
        import requests as _req
        resp = _req.delete(
            f"https://graph.facebook.com/v25.0/{fb_post_id}",
            params={'access_token': token},
            timeout=15
        )
        print(f'FB delete {fb_post_id}: {resp.status_code}')
        return True
    except Exception as e:
        print(f'Could not delete FB post {fb_post_id}: {e}')
        return False

def _reset_to_scheduled(post_id_local, new_time=None):
    """Cancel FB post and reset to local scheduled so scheduler re-uploads."""
    _cancel_fb_post(post_id_local)
    if new_time:
        conn.execute("""UPDATE posts SET scheduled_time=?, status='scheduled',
                        facebook_post_id=NULL, attempt_count=0, last_error=NULL
                        WHERE id=?""", (new_time, post_id_local))
    else:
        conn.execute("""UPDATE posts SET status='scheduled',
                        facebook_post_id=NULL, attempt_count=0, last_error=NULL
                        WHERE id=?""", (post_id_local,))
    conn.commit()


def _proxy_fb_image(url, thumbnail=False):
    import requests as req
    try:
        r = req.get(url, timeout=10, stream=True)
        if r.status_code != 200:
            abort(404)
        if thumbnail:
            img = Image.open(BytesIO(r.content))
            img.thumbnail((200, 200), Image.LANCZOS)
            buf = BytesIO()
            img.save(buf, format='JPEG', quality=85)
            buf.seek(0)
            return send_file(buf, mimetype='image/jpeg')
        return send_file(BytesIO(r.content), mimetype=r.headers.get('Content-Type', 'image/jpeg'))
    except Exception:
        abort(404)

@app.route('/thumb/<int:post_id>')
def thumb(post_id):
    row = conn.execute("SELECT photo_path, fb_thumbnail_url FROM posts WHERE id=?", (post_id,)).fetchone()
    if not row:
        abort(404)
    if row['photo_path'] and os.path.exists(row['photo_path']):
        try:
            img = Image.open(row['photo_path'])
            img.thumbnail((200, 200), Image.LANCZOS)
            buf = BytesIO()
            img.save(buf, format='JPEG', quality=85)
            buf.seek(0)
            return send_file(buf, mimetype='image/jpeg')
        except Exception:
            pass
    if row['fb_thumbnail_url'] and os.path.exists(row['fb_thumbnail_url']):
        return send_file(row['fb_thumbnail_url'], mimetype='image/jpeg')
    abort(404)

@app.route('/photo/<int:post_id>')
def full_photo(post_id):
    row = conn.execute("SELECT photo_path, fb_thumbnail_url FROM posts WHERE id=?", (post_id,)).fetchone()
    if not row:
        abort(404)
    if row['photo_path'] and os.path.exists(row['photo_path']):
        return send_file(row['photo_path'], mimetype='image/jpeg')
    if row['fb_thumbnail_url'] and os.path.exists(row['fb_thumbnail_url']):
        return send_file(row['fb_thumbnail_url'], mimetype='image/jpeg')
    abort(404)


# ── Queue (calendar grid home) ───────────────────────────────────

@app.route('/')
def queue():
    from datetime import date as dt_date, timedelta
    week_offset = int(request.args.get('week', 0))
    today = dt_date.today()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    days = [monday + timedelta(days=i) for i in range(7)]

    all_posts_raw = get_all_posts(conn, 'all')
    def _get_error(p):
        try:
            return p['last_error'] or ''
        except (IndexError, KeyError):
            return ''
    interrupted = [p for p in all_posts_raw
                   if p['status'] == 'failed'
                   and 'interrupted' in _get_error(p).lower()]
    fb_page_id = get_setting(conn, 'page_id') or ''

    import json as _json
    DOW_MAP = {0: 'MON', 1: 'TUE', 2: 'WED', 3: 'THU', 4: 'FRI', 5: 'SAT', 6: 'SUN'}
    schedule_slots = {d: [] for d in days}
    sched_row = conn.execute("SELECT * FROM schedule_patterns WHERE is_active=1 LIMIT 1").fetchone()
    if sched_row:
        try:
            raw = _json.loads(sched_row['times_of_day'] or '{}')
            if sched_row['pattern_type'] == 'custom' and isinstance(raw, dict):
                for d in days:
                    dk = DOW_MAP[d.weekday()]
                    schedule_slots[d] = sorted(raw.get(dk, []))
            elif sched_row['pattern_type'] in ('weekly', 'daily'):
                active_days = _json.loads(sched_row['days_of_week'] or '[]')
                flat = sorted(raw) if isinstance(raw, list) else []
                for d in days:
                    dk = DOW_MAP[d.weekday()]
                    if dk in active_days:
                        schedule_slots[d] = flat
        except Exception:
            pass

    def post_date(p):
        try:
            return dt_date.fromisoformat((p['scheduled_time'] or '')[:10])
        except Exception:
            return None

    def count_in_range(start, end):
        return sum(1 for p in all_posts_raw if start <= (post_date(p) or dt_date.min) <= end)

    by_day = {d: [] for d in days}
    for p in all_posts_raw:
        d = post_date(p)
        if d in by_day:
            by_day[d].append(dict(p))
    for d in days:
        by_day[d].sort(key=lambda p: p['scheduled_time'] or '')

    prev_week = week_offset - 1
    next_week = week_offset + 1
    prev_count = count_in_range(monday - timedelta(weeks=1), monday - timedelta(days=1))
    next_count = count_in_range(monday + timedelta(weeks=1), monday + timedelta(weeks=2, days=-1))

    title = f"{monday.strftime('%b %-d')} – {days[-1].strftime('%-d, %Y')}"
    if monday.month != days[-1].month:
        title = f"{monday.strftime('%b %-d')} – {days[-1].strftime('%b %-d, %Y')}"

    prev_label = f'← {prev_count} post{"s" if prev_count != 1 else ""}' if prev_count else '←'
    next_label = f'{next_count} post{"s" if next_count != 1 else ""} →' if next_count else '→'

    badge_cls = {
        'scheduled':    'not-uploaded',
        'retrying':     'not-uploaded',
        'failed':       'not-uploaded',
        'missed':       'not-uploaded',
        'posting':      'uploading',
        'fb_scheduled': 'scheduled',
        'posted':       'posted',
    }
    badge_lbl = {
        'scheduled':    'Not Uploaded',
        'retrying':     'Not Uploaded',
        'failed':       'Not Uploaded',
        'missed':       'Not Uploaded',
        'posting':      'Uploading',
        'fb_scheduled': 'Scheduled',
        'posted':       'Posted',
    }

    import html as _html
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cols = ''
    for d in days:
        is_today = (d == today)
        today_cls = ' today' if is_today else ''
        dow = d.strftime('%a')
        dom = d.strftime('%-d')
        date_str = d.strftime('%Y-%m-%d')

        day_posts = by_day[d]
        day_slots = schedule_slots[d]
        filled_times = set()
        for p in day_posts:
            t = (p['scheduled_time'] or '')[11:16]
            if t:
                filled_times.add(t)

        all_times = sorted(set(day_slots) | filled_times)
        posts_by_time = {}
        for p in day_posts:
            t = (p['scheduled_time'] or '')[11:16]
            posts_by_time.setdefault(t, []).append(p)

        posts_html = ''
        for slot_time in all_times:
            if slot_time in posts_by_time:
                for p in posts_by_time[slot_time]:
                    pid = p['id']
                    st  = p['status']
                    t   = (p['scheduled_time'] or '')[11:16]
                    cap = (p['caption'] or '').strip()
                    photo_path = p['photo_path'] or ''
                    fb_post_id = p['facebook_post_id'] or ''
                    is_draggable = st not in ('posted', 'posting')
                    bc  = badge_cls.get(st, 'not-uploaded')
                    bl  = badge_lbl.get(st, st)
                    full_dt = (p['scheduled_time'] or '')[:16]
                    cap_escaped = _html.escape(cap, quote=True).replace('\n', '&#10;')
                    fb_thumb = p['fb_thumbnail_url'] if 'fb_thumbnail_url' in p.keys() else ''
                    if (photo_path and os.path.exists(photo_path)) or fb_thumb:
                        img_html = f'<img src="/thumb/{pid}" alt="">'
                    else:
                        img_html = '<div class="card-no-img">No image</div>'

                    cap_html = f'<div class="card-cap">{cap[:80]}</div>' if cap else '<div class="card-cap no-cap">No caption</div>'
                    posted_cls = ' posted-card' if not is_draggable else ''
                    if is_draggable:
                        interact = f'onmousedown="calMouseDown(event,{pid},\'{full_dt}\')"'
                    else:
                        interact = f'onclick="openModal({pid})"'

                    posts_html += (
                        f'<div class="post-card{posted_cls}" id="cal-card-{pid}" '
                        f'data-pid="{pid}" data-time="{full_dt}" '
                        f'data-cap="{cap_escaped}" data-status="{st}" data-fbid="{fb_post_id}" '
                        f'{interact}>\n'
                        f'  <input type="checkbox" class="select-check" data-pid="{pid}" onclick="event.stopPropagation();toggleCardSelect({pid})">\n'
                        f'  {img_html}\n'
                        f'  <div class="card-body">\n'
                        f'    <div class="card-time">{t}</div>\n'
                        f'    {cap_html}\n'
                        f'  </div>\n'
                        f'  <div class="card-foot">\n'
                        f'    <span class="badge badge-{bc} post-badge" id="badge-{pid}">{bl}</span>\n'
                        f'  </div>\n'
                        f'</div>'
                    )
            else:
                sched_dt = f'{date_str} {slot_time}:00'
                if sched_dt >= now_str:
                    posts_html += (
                        f'<label class="empty-slot empty-slot-upload" data-time="{sched_dt}" title="Upload a photo for {slot_time}">'
                        f'<input type="file" accept="image/*" style="display:none" '
                        f'onchange="slotUpload(this,\'{sched_dt}\')">'
                        f'<span class="empty-slot-time">{slot_time}</span>'
                        f'<span class="empty-slot-icon">+</span>'
                        f'</label>'
                    )
                else:
                    posts_html += (
                        f'<div class="empty-slot" data-time="{sched_dt}">'
                        f'<span class="empty-slot-time">{slot_time}</span>'
                        f'</div>'
                    )

        unslotted = [p for p in day_posts if (p['scheduled_time'] or '')[11:16] not in set(all_times)]
        for p in unslotted:
            pid = p['id']
            st  = p['status']
            t   = (p['scheduled_time'] or '')[11:16]
            cap = (p['caption'] or '').strip()
            photo_path = p['photo_path'] or ''
            fb_post_id = p['facebook_post_id'] or ''
            is_draggable = st not in ('posted', 'posting')
            bc  = badge_cls.get(st, 'not-uploaded')
            bl  = badge_lbl.get(st, st)
            full_dt = (p['scheduled_time'] or '')[:16]
            cap_escaped = _html.escape(cap, quote=True).replace('\n', '&#10;')
            fb_thumb = p['fb_thumbnail_url'] if 'fb_thumbnail_url' in p.keys() else ''
            if (photo_path and os.path.exists(photo_path)) or fb_thumb:
                img_html = f'<img src="/thumb/{pid}" alt="">'
            else:
                img_html = '<div class="card-no-img">No image</div>'
            cap_html = f'<div class="card-cap">{cap[:80]}</div>' if cap else '<div class="card-cap no-cap">No caption</div>'
            posted_cls = ' posted-card' if not is_draggable else ''
            if is_draggable:
                interact = f'onmousedown="calMouseDown(event,{pid},\'{full_dt}\')"'
            else:
                interact = f'onclick="openModal({pid})"'
            posts_html += (
                f'<div class="post-card{posted_cls}" id="cal-card-{pid}" '
                f'data-pid="{pid}" data-time="{full_dt}" '
                f'data-cap="{cap_escaped}" data-status="{st}" data-fbid="{fb_post_id}" '
                f'{interact}>\n'
                f'  <input type="checkbox" class="select-check" data-pid="{pid}" onclick="event.stopPropagation();toggleCardSelect({pid})">\n'
                f'  {img_html}\n'
                f'  <div class="card-body">\n'
                f'    <div class="card-time">{t}</div>\n'
                f'    {cap_html}\n'
                f'  </div>\n'
                f'  <div class="card-foot">\n'
                f'    <span class="badge badge-{bc} post-badge" id="badge-{pid}">{bl}</span>\n'
                f'  </div>\n'
                f'</div>'
            )

        if not posts_html:
            if day_slots:
                for slot_time in day_slots:
                    sched_dt2 = f'{date_str} {slot_time}:00'
                    if sched_dt2 >= now_str:
                        posts_html += (
                            f'<label class="empty-slot empty-slot-upload" data-time="{sched_dt2}" title="Upload a photo for {slot_time}">'
                            f'<input type="file" accept="image/*" style="display:none" '
                            f'onchange="slotUpload(this,\'{sched_dt2}\')">'
                            f'<span class="empty-slot-time">{slot_time}</span>'
                            f'<span class="empty-slot-icon">+</span>'
                            f'</label>'
                        )
                    else:
                        posts_html += (
                            f'<div class="empty-slot" data-time="{sched_dt2}">'
                            f'<span class="empty-slot-time">{slot_time}</span>'
                            f'</div>'
                        )
            if not posts_html:
                posts_html = '<div class="day-empty">No posts</div>'

        cols += (
            f'<div class="day-col{today_cls}" id="day-{date_str}">\n'
            f'  <div class="day-hdr">\n'
            f'    <div class="dom">{dom}</div>\n'
            f'  </div>\n'
            f'  <div class="day-posts" id="posts-{date_str}">{posts_html}</div>\n'
            f'</div>'
        )

    nav = (f'<div class="cal-nav">'
           f'<a href="/?week={prev_week}">{prev_label}</a>'
           f'<span class="cal-title">{title}</span>'
           f'<a href="/?week={next_week}">{next_label}</a>'
           f'<button class="btn btn-secondary btn-sm" id="select-mode-btn" onclick="toggleSelectMode()" '
           f'style="margin-left:12px;font-size:12px;padding:6px 12px">Select</button>'
           f'</div>')

    modal_css = '''
.modal-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,.5);
                 z-index:500; align-items:center; justify-content:center; }
.modal-overlay.open { display:flex; }
.modal-box { background:white; border-radius:14px; width:480px; max-width:95vw;
             max-height:90vh; overflow-y:auto; box-shadow:0 8px 40px rgba(0,0,0,.3);
             display:flex; flex-direction:column; }
.modal-hdr { display:flex; align-items:center; justify-content:space-between;
             padding:16px 20px 12px; border-bottom:1px solid #eee; }
.modal-hdr h3 { font-size:15px; font-weight:600; margin:0; }
.modal-close { background:none; border:none; font-size:20px; cursor:pointer;
               color:#999; line-height:1; padding:0 4px; }
.modal-close:hover { color:#333; }
.modal-body { padding:20px; display:flex; flex-direction:column; gap:16px; }
.modal-img { width:100%; height:auto; object-fit:cover;
             border-radius:8px; border:1px solid #D6D0CA; display:block; }
.modal-img-none { width:100%; height:140px; background:#EDE9E5; border-radius:8px;
                  display:flex; align-items:center; justify-content:center;
                  color:#948466; font-size:13px; }
.modal-section { display:flex; flex-direction:column; gap:8px; }
.modal-label { font-size:11px; font-weight:700; color:#948466; text-transform:uppercase;
               letter-spacing:.06em; margin-bottom:2px; }
.modal-caption-text { font-size:14px; line-height:1.6; color:#343434; white-space:pre-wrap;
                      min-height:20px; }
.modal-caption-empty { font-size:14px; color:#948466; font-style:italic; }
.modal-textarea { width:100%; padding:10px 12px; border:1px solid #D6D0CA; border-radius:8px;
                  font-size:14px; font-family:inherit; resize:vertical; min-height:80px;
                  line-height:1.5; box-sizing:border-box; }
.modal-textarea:focus { outline:none; border-color:#749A96; box-shadow:0 0 0 3px rgba(116,154,150,.15); }
.modal-btn-row { display:flex; gap:8px; }
.modal-btn-row .btn { height:36px; padding:0 16px; font-size:13px; display:flex;
                      align-items:center; justify-content:center; gap:4px; }
.modal-sched-display { display:flex; align-items:center; justify-content:space-between;
    padding:10px 14px; background:#F8F6F4; border:1px solid #D6D0CA; border-radius:8px;
    cursor:pointer; transition:all .15s; }
.modal-sched-display:hover { background:#EDE9E5; border-color:#948466; }
.modal-sched-display span:first-child { font-size:14px; font-weight:500; color:#343434; }
.modal-sched-edit-icon { font-size:13px; opacity:.5; }
.modal-sched-picker { background:#F8F6F4; border:1px solid #D6D0CA; border-radius:10px;
    padding:14px; display:flex; flex-direction:column; gap:12px; }
.modal-sched-row { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.modal-sched-input::-webkit-datetime-edit { color:#343434; }
.modal-sched-input::-webkit-calendar-picker-indicator { filter:sepia(.6) saturate(.3) brightness(.85); cursor:pointer; }
.modal-sched-field { display:flex; flex-direction:column; gap:4px; }
.modal-sched-field-label { font-size:11px; font-weight:600; color:#948466;
    text-transform:uppercase; letter-spacing:.05em; }
.modal-sched-input { height:38px; padding:0 12px; border:1px solid #D6D0CA; border-radius:8px;
    font-size:14px; font-family:inherit; box-sizing:border-box; background:white; }
.modal-sched-input:focus { outline:none; border-color:#749A96; box-shadow:0 0 0 3px rgba(116,154,150,.15); }
.modal-sched-actions { display:flex; gap:8px; justify-content:flex-end; }
.modal-sched-actions .btn { height:34px; padding:0 16px; font-size:13px; }
.modal-footer { display:flex; align-items:center; justify-content:space-between;
                padding:16px 20px; border-top:1px solid #E5E0DA; gap:10px; }
.modal-footer-left { display:flex; gap:8px; }
.modal-footer .btn { height:38px; padding:0 18px; font-size:14px; display:flex;
                     align-items:center; justify-content:center; gap:6px; }
'''

    page_js = f'''
<div id="reorder-bar" class="hidden">
  <span style="font-size:15px;font-weight:500">📋 You have unsaved schedule changes</span>
  <button id="submit-reorder-btn" class="btn btn-success" onclick="submitCalReorder()">✓ Submit Changes</button>
  <button class="btn btn-secondary" onclick="cancelCalReorder()" style="background:rgba(255,255,255,.15);color:white;border:1px solid rgba(255,255,255,.3)">✕ Cancel</button>
</div>
<div id="multi-select-bar">
  <span class="select-count" id="select-count">0 selected</span>
  <button class="btn btn-danger" id="multi-delete-btn" onclick="multiDeleteStart()">Delete Selected</button>
  <div id="multi-delete-confirm" style="display:none;align-items:center;gap:8px;">
    <span style="font-size:13px;color:#f5d8d4">Delete these posts? This will also remove them from Facebook.</span>
    <button class="btn btn-danger" id="multi-delete-confirm-btn" onclick="multiDeleteConfirm()">Yes, delete</button>
    <button class="btn btn-secondary" onclick="multiDeleteCancel()" style="background:rgba(255,255,255,.15);color:white;border:1px solid rgba(255,255,255,.3)">Cancel</button>
  </div>
  <span style="flex:1"></span>
  <button class="btn btn-secondary" onclick="exitSelectMode()" style="background:rgba(255,255,255,.15);color:white;border:1px solid rgba(255,255,255,.3)">✕ Cancel</button>
</div>
<div id="reorder-toast"></div>

<!-- Modal -->
<div class="modal-overlay" id="post-modal" onclick="modalBackdropClick(event)">
  <div class="modal-box">
    <div class="modal-hdr">
      <h3 id="modal-title">Post Details</h3>
      <button class="modal-close" onclick="closeModal()">✕</button>
    </div>
    <div class="modal-body">
      <div id="modal-img-wrap"></div>

      <div class="modal-section">
        <div class="modal-label">Caption</div>
        <div id="modal-caption-view"></div>
        <div id="modal-caption-edit" style="display:none">
          <textarea class="modal-textarea" id="modal-caption" rows="4" spellcheck="true" lang="en"></textarea>
        </div>
        <div class="modal-btn-row">
          <button class="btn btn-secondary" id="modal-edit-cap-btn" onclick="modalStartEditCaption()">✏️ Edit Caption</button>
          <button class="btn btn-primary" id="modal-save-cap-btn" onclick="modalSaveCaption()" style="display:none">Save Caption</button>
          <button class="btn btn-secondary" id="modal-cancel-cap-btn" onclick="modalCancelEditCaption()" style="display:none">Cancel</button>
        </div>
      </div>

      <div class="modal-section" id="modal-schedule-section">
        <div class="modal-label">Scheduled Time</div>
        <div class="modal-sched-display" id="modal-sched-display" onclick="modalToggleDatePicker()">
          <span id="modal-sched-text"></span>
          <span class="modal-sched-edit-icon">✏️</span>
        </div>
        <div class="modal-sched-picker" id="modal-sched-picker" style="display:none">
          <div class="modal-sched-row">
            <div class="modal-sched-field">
              <label class="modal-sched-field-label">Date</label>
              <input type="date" class="modal-sched-input" id="modal-sched-date">
            </div>
            <div class="modal-sched-field">
              <label class="modal-sched-field-label">Time</label>
              <input type="time" class="modal-sched-input" id="modal-sched-time">
            </div>
          </div>
          <div class="modal-sched-actions">
            <button class="btn btn-primary" onclick="modalSaveDatetime()">Save</button>
            <button class="btn btn-secondary" onclick="modalCancelDatePicker()">Cancel</button>
          </div>
        </div>
      </div>
    </div>

    <div class="modal-footer">
      <div class="modal-footer-left">
        <button class="btn btn-success" id="modal-post-now-btn" onclick="modalPostNow()">⚡ Post Now</button>
        <button class="btn btn-primary" id="modal-retry-btn" style="display:none" onclick="modalRetry()">↩ Retry Upload</button>
        <div id="modal-post-confirm" style="display:none;align-items:center;gap:8px;">
          <span style="font-size:13px;color:#948466">Post to Facebook now?</span>
          <button class="btn btn-success" id="modal-post-confirm-btn" onclick="modalPostNowConfirm()">Yes, post</button>
          <button class="btn btn-secondary" onclick="modalPostNowCancel()">Cancel</button>
        </div>
        <a class="btn btn-secondary" id="modal-view-fb-btn" href="" target="_blank" style="display:none">View on Facebook ↗</a>
      </div>
      <div id="modal-delete-wrap">
        <button class="btn btn-danger" id="modal-delete-btn" onclick="modalDelete()">Delete</button>
        <div id="modal-delete-confirm" style="display:none;align-items:center;gap:8px;">
          <span style="font-size:13px;color:#8a3a2e">Delete this post?</span>
          <button class="btn btn-danger" id="modal-delete-confirm-btn" onclick="modalDeleteConfirm()">Yes, delete</button>
          <button class="btn btn-secondary" onclick="modalDeleteCancel()">Cancel</button>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
let calDragPid = null;
let calDragOrigDatetime = null;
let calDragGhost = null, calDragOffsetX = 0, calDragOffsetY = 0;
let calDragStartX = 0, calDragStartY = 0, calDragging = false;
let calPending = {{}};  // pid → {{newDatetime, origDatetime}}
let calCurrentWeek = {week_offset};
let calWeekLoading = false;
let calEdgeTimer = null;
let modalPid = null;

// ── Toast ─────────────────────────────────────────────────
function showToast(msg) {{
    const t = document.getElementById('reorder-toast');
    t.textContent = msg;
    t.style.display = 'block';
    setTimeout(() => {{ t.style.display = 'none'; }}, 2800);
}}

// ── Modal ─────────────────────────────────────────────────
function openModal(pid) {{
    if (selectMode) {{ toggleCardSelect(pid); return; }}
    const card = document.getElementById('cal-card-'+pid);
    if (!card) return;
    modalPid = pid;
    const dt   = card.dataset.time || '';
    const cap  = card.dataset.cap  || '';
    const st   = card.dataset.status || '';
    const fbid = card.dataset.fbid || '';

    document.getElementById('modal-title').textContent = 'Post #' + pid;
    document.getElementById('modal-caption').value = cap;
    const capView = document.getElementById('modal-caption-view');
    capView.className = cap ? 'modal-caption-text' : 'modal-caption-empty';
    capView.textContent = cap || 'No caption';
    document.getElementById('modal-caption-edit').style.display = 'none';
    capView.style.display = '';
    document.getElementById('modal-edit-cap-btn').style.display = '';
    document.getElementById('modal-save-cap-btn').style.display = 'none';
    document.getElementById('modal-cancel-cap-btn').style.display = 'none';
    // Populate schedule display & picker
    const dtDate = dt.substring(0,10);
    const dtTime = dt.substring(11,16);
    const dateObj = new Date(dtDate + 'T' + dtTime);
    const dayNames = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    const monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const friendlyDate = dayNames[dateObj.getDay()] + ', ' + monthNames[dateObj.getMonth()] + ' '
        + dateObj.getDate() + ' at ' + dtTime;
    document.getElementById('modal-sched-text').textContent = friendlyDate;
    document.getElementById('modal-sched-date').value = dtDate;
    document.getElementById('modal-sched-time').value = dtTime;
    document.getElementById('modal-sched-display').style.display = '';
    document.getElementById('modal-sched-picker').style.display = 'none';

    // Reset post-now confirm state
    document.getElementById('modal-post-now-btn').style.display = '';
    document.getElementById('modal-post-confirm').style.display = 'none';
    const confirmBtn = document.getElementById('modal-post-confirm-btn');
    confirmBtn.disabled = false; confirmBtn.style.pointerEvents = ''; confirmBtn.textContent = 'Yes, post';

    // Reset delete confirm state
    document.getElementById('modal-delete-btn').style.display = '';
    document.getElementById('modal-delete-confirm').style.display = 'none';
    const delConfirmBtn = document.getElementById('modal-delete-confirm-btn');
    delConfirmBtn.disabled = false; delConfirmBtn.textContent = 'Yes, delete';

    // Thumbnail
    const imgWrap = document.getElementById('modal-img-wrap');
    const hasPhoto = card.querySelector('img') !== null;
    if (hasPhoto) {{
        imgWrap.innerHTML = '<img class="modal-img" src="/photo/' + pid + '" alt="photo">';
        const modalImg = imgWrap.querySelector('img');
        modalImg.onload = function() {{
            const r = this.naturalWidth / this.naturalHeight;
            if (r < 2/3) {{
                this.style.aspectRatio = '2/3';  // too tall — crop to 2:3
            }} else if (r > 3/2) {{
                this.style.aspectRatio = '3/2';  // too wide — crop to 3:2
            }} else {{
                this.style.aspectRatio = '';      // natural ratio
            }}
        }};
    }} else {{
        imgWrap.innerHTML = '<div class="modal-img-none">No image</div>';
    }}


    // Post Now / Retry visibility
    const postNowBtn = document.getElementById('modal-post-now-btn');
    const retryBtn   = document.getElementById('modal-retry-btn');
    postNowBtn.style.display = (st === 'posted' || st === 'posting' || st === 'failed') ? 'none' : '';
    retryBtn.style.display   = (st === 'failed') ? '' : 'none';
    retryBtn.disabled = false; retryBtn.textContent = '↩ Retry Upload';

    // View on Facebook for posted/scheduled posts that have a fb post id
    const viewFbBtn = document.getElementById('modal-view-fb-btn');
    if (fbid) {{
        viewFbBtn.href = 'https://www.facebook.com/' + fbid;
        viewFbBtn.style.display = '';
    }} else {{
        viewFbBtn.style.display = 'none';
    }}

    // Reset button states
    const capBtn = document.getElementById('modal-save-cap-btn');
    capBtn.disabled = false; capBtn.textContent = 'Save Caption';
    // Hide schedule section for posted posts
    const schedSection = document.getElementById('modal-schedule-section');
    schedSection.style.display = (st === 'posted') ? 'none' : '';

    document.getElementById('post-modal').classList.add('open');
}}

function modalStartEditCaption() {{
    document.getElementById('modal-caption-view').style.display = 'none';
    document.getElementById('modal-edit-cap-btn').style.display = 'none';
    document.getElementById('modal-caption-edit').style.display = '';
    document.getElementById('modal-save-cap-btn').style.display = '';
    document.getElementById('modal-cancel-cap-btn').style.display = '';
    document.getElementById('modal-caption').focus();
}}

function modalCancelEditCaption() {{
    document.getElementById('modal-caption-edit').style.display = 'none';
    document.getElementById('modal-caption-view').style.display = '';
    document.getElementById('modal-edit-cap-btn').style.display = '';
    document.getElementById('modal-save-cap-btn').style.display = 'none';
    document.getElementById('modal-cancel-cap-btn').style.display = 'none';
}}

function closeModal() {{
    document.getElementById('post-modal').classList.remove('open');
    modalPid = null;
}}

function modalBackdropClick(e) {{
    if (e.target === document.getElementById('post-modal')) closeModal();
}}

async function modalSaveCaption() {{
    if (!modalPid) return;
    const btn = document.getElementById('modal-save-cap-btn');
    if (btn.disabled) return;
    btn.disabled = true; btn.textContent = 'Saving…';

    const card = document.getElementById('cal-card-'+modalPid);
    const badge = document.getElementById('badge-'+modalPid);
    const prevBadgeClass = badge ? badge.className : '';
    const prevBadgeText  = badge ? badge.textContent : '';
    if (badge) {{ badge.className = 'badge badge-uploading post-badge'; badge.textContent = 'Saving'; }}

    const text = document.getElementById('modal-caption').value.trim();
    try {{
        const r = await fetch('/update-caption/'+modalPid, {{
            method:'POST', headers:{{'Content-Type':'application/json'}},
            body: JSON.stringify({{caption: text}})
        }});
        const d = await r.json();
        if (d.ok) {{
            // Update card caption display
            if (card) {{
                card.dataset.cap = text;
                const capEl = card.querySelector('.card-cap');
                if (capEl) {{
                    capEl.textContent = text ? text.substring(0,80) : 'No caption';
                    capEl.className = 'card-cap' + (text ? '' : ' no-cap');
                }}
            }}
            if (badge && d.badge) {{
                badge.className = 'badge badge-' + d.badge + ' post-badge';
                badge.textContent = d.label || d.badge;
            }}
            btn.disabled = false; btn.textContent = 'Save Caption';
            // Return to locked view with updated text
            const viewEl = document.getElementById('modal-caption-view');
            viewEl.className = text ? 'modal-caption-text' : 'modal-caption-empty';
            viewEl.textContent = text || 'No caption';
            modalCancelEditCaption();
            showToast('✓ Caption saved');
        }} else {{
            if (badge) {{ badge.className = prevBadgeClass; badge.textContent = prevBadgeText; }}
            alert(d.error || 'Failed to save caption.');
            btn.disabled = false; btn.textContent = 'Save Caption';
        }}
    }} catch(e) {{
        if (badge) {{ badge.className = prevBadgeClass; badge.textContent = prevBadgeText; }}
        alert('Network error — please try again.');
        btn.disabled = false; btn.textContent = 'Save Caption';
    }}
}}

function modalToggleDatePicker() {{
    document.getElementById('modal-sched-display').style.display = 'none';
    document.getElementById('modal-sched-picker').style.display = '';
}}
function modalCancelDatePicker() {{
    document.getElementById('modal-sched-picker').style.display = 'none';
    document.getElementById('modal-sched-display').style.display = '';
}}

async function modalSaveDatetime() {{
    if (!modalPid) return;
    const dateVal = document.getElementById('modal-sched-date').value.trim();
    const timeVal = document.getElementById('modal-sched-time').value.trim();
    if (!dateVal || !timeVal) return;

    const newTime = dateVal + ' ' + timeVal + ':00';
    const saveBtn = document.querySelector('#modal-sched-picker .btn-primary');
    saveBtn.disabled = true; saveBtn.textContent = 'Saving…';

    try {{
        const r = await fetch('/reschedule/'+modalPid, {{
            method:'POST', headers:{{'Content-Type':'application/json'}},
            body: JSON.stringify({{scheduled_time: newTime}})
        }});
        const d = await r.json();
        if (d.ok) {{
            const card = document.getElementById('cal-card-'+modalPid);
            const newDate = newTime.substring(0,10);
            const newTimeStr = newTime.substring(11,16);
            const oldDate = (card ? card.dataset.time : '').substring(0,10);

            if (card) {{
                card.dataset.time = newTime.substring(0,16);
                const timeEl = card.querySelector('.card-time');
                if (timeEl) timeEl.textContent = newTimeStr;
            }}

            if (card && newDate !== oldDate) {{
                const srcPosts = document.getElementById('posts-'+oldDate);
                const dstPosts = document.getElementById('posts-'+newDate);
                if (dstPosts) {{
                    const empty = dstPosts.querySelector('.day-empty');
                    if (empty) empty.remove();
                    dstPosts.appendChild(card);
                }} else {{
                    if (srcPosts) srcPosts.removeChild(card);
                }}
                if (srcPosts && srcPosts.querySelectorAll('.post-card').length === 0) {{
                    srcPosts.innerHTML = '<div class="day-empty">No posts</div>';
                }}
            }}

            const origEntry = calPending[modalPid];
            const origDatetime = origEntry ? origEntry.origDatetime : (card ? card.dataset.time : newTime.substring(0,16));
            if (newTime.substring(0,16) !== origDatetime) {{
                calPending[modalPid] = {{ newDatetime: newTime.substring(0,16), origDatetime }};
            }} else {{
                delete calPending[modalPid];
            }}
            checkCalPending();

            // Update display text
            const dateObj = new Date(dateVal + 'T' + timeVal);
            const dayNames = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
            const monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
            document.getElementById('modal-sched-text').textContent =
                dayNames[dateObj.getDay()] + ', ' + monthNames[dateObj.getMonth()] + ' '
                + dateObj.getDate() + ' at ' + timeVal;

            modalCancelDatePicker();
            saveBtn.disabled = false; saveBtn.textContent = 'Save';
            showToast('✓ Time updated');
        }} else {{
            alert(d.error || 'Could not reschedule.');
            saveBtn.disabled = false; saveBtn.textContent = 'Save';
        }}
    }} catch(e) {{
        alert('Network error — please try again.');
        saveBtn.disabled = false; saveBtn.textContent = 'Save';
    }}
}}

function modalPostNow() {{
    if (!modalPid) return;
    document.getElementById('modal-post-now-btn').style.display = 'none';
    document.getElementById('modal-post-confirm').style.display = 'flex';
}}
function modalPostNowCancel() {{
    document.getElementById('modal-post-confirm').style.display = 'none';
    document.getElementById('modal-post-now-btn').style.display = '';
}}
async function modalPostNowConfirm() {{
    if (!modalPid) return;
    const pid = modalPid;
    const btn = document.getElementById('modal-post-confirm-btn');
    btn.disabled = true;
    btn.style.pointerEvents = 'none';
    btn.textContent = 'Posting…';
    document.getElementById('modal-post-confirm').querySelector('.btn-secondary').style.display = 'none';

    const card = document.getElementById('cal-card-' + pid);
    const badge = document.getElementById('badge-' + pid);
    if (badge) {{ badge.className = 'badge badge-uploading post-badge'; badge.textContent = 'Posting'; }}
    closeModal();

    try {{
        const r = await fetch('/post-now/' + pid, {{ headers: {{ 'X-Requested-With': 'fetch' }} }});
        const d = await r.json();
        if (d.ok) {{
            if (badge) {{ badge.className = 'badge badge-posted post-badge'; badge.textContent = 'Posted'; }}
            if (card) {{ card.dataset.status = 'posted'; }}
            showToast('Posted to Facebook ✓');
            setTimeout(() => location.reload(), 1200);
        }} else {{
            if (badge) {{ badge.className = 'badge badge-failure post-badge'; badge.textContent = 'Failed'; }}
            if (card) {{ card.dataset.status = 'failed'; }}
            showToast('Upload failed: ' + (d.error || 'Unknown error'));
        }}
    }} catch(e) {{
        if (badge) {{ badge.className = 'badge badge-failure post-badge'; badge.textContent = 'Failed'; }}
        showToast('Network error — please try again.');
    }}
}}

function modalDelete() {{
    if (!modalPid) return;
    document.getElementById('modal-delete-btn').style.display = 'none';
    document.getElementById('modal-delete-confirm').style.display = 'flex';
}}
function modalDeleteCancel() {{
    document.getElementById('modal-delete-confirm').style.display = 'none';
    document.getElementById('modal-delete-btn').style.display = '';
}}
async function modalDeleteConfirm() {{
    if (!modalPid) return;
    const pid = modalPid;
    const btn = document.getElementById('modal-delete-confirm-btn');
    btn.disabled = true;
    btn.textContent = 'Deleting…';
    document.getElementById('modal-delete-confirm').querySelector('.btn-secondary').style.display = 'none';

    const card = document.getElementById('cal-card-' + pid);
    const badge = document.getElementById('badge-' + pid);
    closeModal();

    if (badge) {{ badge.className = 'badge badge-deleting post-badge'; badge.textContent = 'Deleting'; }}

    try {{
        const r = await fetch('/delete/' + pid, {{ method: 'POST', headers: {{ 'X-Requested-With': 'fetch' }} }});
        const d = await r.json();
        if (d.ok && card) {{
            const cardTime = card.dataset.time || '';
            const parent = card.parentNode;
            card.style.transition = 'opacity 0.5s ease';
            card.style.opacity = '0';
            setTimeout(() => {{
                card.remove();
                if (cardTime && parent) {{
                    const now = new Date();
                    const slotDate = new Date(cardTime.replace(' ','T'));
                    const hhmm = cardTime.substring(11,16);
                    let slot;
                    if (slotDate > now) {{
                        slot = document.createElement('label');
                        slot.className = 'empty-slot empty-slot-upload';
                        slot.title = 'Upload a photo for ' + hhmm;
                        slot.dataset.time = cardTime;
                        slot.innerHTML = '<input type="file" accept="image/*" style="display:none" '
                            + 'onchange="slotUpload(this,\\''+cardTime+'\\')">'
                            + '<span class="empty-slot-time">'+hhmm+'</span>'
                            + '<span class="empty-slot-icon">+</span>';
                    }} else {{
                        slot = document.createElement('div');
                        slot.className = 'empty-slot';
                        slot.dataset.time = cardTime;
                        slot.innerHTML = '<span class="empty-slot-time">'+hhmm+'</span>';
                    }}
                    insertCardSorted(parent, slot);
                    if (parent.querySelector('.day-empty') && parent.querySelector('.post-card, .empty-slot'))
                        parent.querySelector('.day-empty')?.remove();
                }}
            }}, 500);
        }}
    }} catch(e) {{
        if (badge) {{ badge.className = 'badge badge-failure post-badge'; badge.textContent = 'Error'; }}
        showToast('Delete failed — please try again.');
    }}
}}

// ── Multi-select ─────────────────────────────────────
let selectMode = false;
let selectedPids = new Set();

function toggleSelectMode() {{
    if (selectMode) {{ exitSelectMode(); return; }}
    selectMode = true;
    document.querySelector('.cal-grid').classList.add('select-mode');
    document.getElementById('select-mode-btn').textContent = 'Cancel Select';
    document.getElementById('multi-select-bar').classList.add('visible');
    updateSelectCount();
}}

function exitSelectMode() {{
    selectMode = false;
    selectedPids.clear();
    document.querySelector('.cal-grid').classList.remove('select-mode');
    document.getElementById('select-mode-btn').textContent = 'Select';
    document.getElementById('multi-select-bar').classList.remove('visible');
    document.querySelectorAll('.post-card.selected').forEach(c => c.classList.remove('selected'));
    document.querySelectorAll('.select-check').forEach(cb => cb.checked = false);
    multiDeleteCancel();
}}

function toggleCardSelect(pid) {{
    const card = document.getElementById('cal-card-' + pid);
    const cb = card?.querySelector('.select-check');
    if (!card || !selectMode) return;
    if (selectedPids.has(pid)) {{
        selectedPids.delete(pid);
        card.classList.remove('selected');
        if (cb) cb.checked = false;
    }} else {{
        selectedPids.add(pid);
        card.classList.add('selected');
        if (cb) cb.checked = true;
    }}
    updateSelectCount();
}}

function updateSelectCount() {{
    const n = selectedPids.size;
    document.getElementById('select-count').textContent = n + ' selected';
    document.getElementById('multi-delete-btn').disabled = (n === 0);
    document.getElementById('multi-delete-btn').style.opacity = n === 0 ? '.5' : '1';
}}

function multiDeleteStart() {{
    if (selectedPids.size === 0) return;
    document.getElementById('multi-delete-btn').style.display = 'none';
    document.getElementById('multi-delete-confirm').style.display = 'flex';
}}

function multiDeleteCancel() {{
    document.getElementById('multi-delete-confirm').style.display = 'none';
    document.getElementById('multi-delete-btn').style.display = '';
}}

async function multiDeleteConfirm() {{
    const pids = [...selectedPids];
    if (pids.length === 0) return;

    const btn = document.getElementById('multi-delete-confirm-btn');
    btn.disabled = true; btn.textContent = 'Deleting…';
    document.getElementById('multi-delete-confirm').querySelector('.btn-secondary').style.display = 'none';

    // Set all selected badges to "Deleting"
    for (const pid of pids) {{
        const badge = document.getElementById('badge-' + pid);
        if (badge) {{ badge.className = 'badge badge-deleting post-badge'; badge.textContent = 'Deleting'; }}
    }}

    try {{
        const r = await fetch('/delete-multiple', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' }},
            body: JSON.stringify({{ ids: pids }})
        }});
        const d = await r.json();
        if (d.ok) {{
            for (const pid of pids) {{
                const card = document.getElementById('cal-card-' + pid);
                if (card) {{
                    card.style.transition = 'opacity 0.5s ease';
                    card.style.opacity = '0';
                    setTimeout(() => {{
                        const parent = card.parentNode;
                        const cardTime = card.dataset.time || '';
                        card.remove();
                        if (parent) {{
                            const hhmm = cardTime.substring(11,16);
                            const now = new Date();
                            const slotDate = new Date(cardTime.replace(' ','T'));
                            let slot;
                            if (slotDate > now) {{
                                slot = document.createElement('label');
                                slot.className = 'empty-slot empty-slot-upload';
                                slot.title = 'Upload a photo for ' + hhmm;
                                slot.dataset.time = cardTime;
                                slot.innerHTML = '<input type="file" accept="image/*" style="display:none" '
                                    + 'onchange="slotUpload(this,\\''+cardTime+'\\')">'
                                    + '<span class="empty-slot-time">'+hhmm+'</span>'
                                    + '<span class="empty-slot-icon">+</span>';
                            }} else {{
                                slot = document.createElement('div');
                                slot.className = 'empty-slot';
                                slot.dataset.time = cardTime;
                                slot.innerHTML = '<span class="empty-slot-time">'+hhmm+'</span>';
                            }}
                            insertCardSorted(parent, slot);
                            if (!parent.querySelector('.post-card') && !parent.querySelector('.empty-slot'))
                                parent.innerHTML = '<div class="day-empty">No posts</div>';
                        }}
                    }}, 500);
                }}
            }}
            showToast('✓ ' + pids.length + ' post(s) deleted');
        }} else {{
            for (const pid of pids) {{
                const badge = document.getElementById('badge-' + pid);
                if (badge) {{ badge.className = 'badge badge-failure post-badge'; badge.textContent = 'Error'; }}
            }}
            showToast(d.error || 'Delete failed');
        }}
    }} catch(e) {{
        for (const pid of pids) {{
            const badge = document.getElementById('badge-' + pid);
            if (badge) {{ badge.className = 'badge badge-failure post-badge'; badge.textContent = 'Error'; }}
        }}
        showToast('Delete failed — please try again.');
    }}
    exitSelectMode();
}}

async function modalRetry() {{
    if (!modalPid) return;
    const btn = document.getElementById('modal-retry-btn');
    btn.disabled = true; btn.style.pointerEvents = 'none'; btn.textContent = 'Re-queuing…';
    try {{
        const r = await fetch('/retry/' + modalPid, {{ method: 'POST' }});
        const d = await r.json();
        if (d.ok) {{
            document.getElementById('interrupted-alert-' + modalPid)?.remove();
            const card = document.getElementById('cal-card-' + modalPid);
            if (card) {{
                const badge = card.querySelector('.post-badge');
                if (badge) {{ badge.className = 'badge badge-not-uploaded post-badge'; badge.textContent = 'Not Uploaded'; }}
                card.dataset.status = 'scheduled';
            }}
            closeModal();
            showToast('↩ Post re-queued — will upload shortly');
        }} else {{
            btn.disabled = false; btn.style.pointerEvents = ''; btn.textContent = '↩ Retry Upload';
            alert(d.error || 'Could not re-queue post.');
        }}
    }} catch(e) {{
        btn.disabled = false; btn.style.pointerEvents = ''; btn.textContent = '↩ Retry Upload';
        alert('Network error — please try again.');
    }}
}}

// ── Mouse-based drag ──────────────────────────────────────
function calMouseDown(e, pid, datetime) {{
    if (selectMode) {{ toggleCardSelect(pid); return; }}
    if (e.button !== 0) return;
    e.preventDefault();
    calDragPid = pid;
    calDragOrigDatetime = datetime;
    calDragging = false;
    calDragStartX = e.clientX;
    calDragStartY = e.clientY;
    const card = document.getElementById('cal-card-' + pid);
    if (!card) return;
    const rect = card.getBoundingClientRect();
    calDragOffsetX = e.clientX - rect.left;
    calDragOffsetY = e.clientY - rect.top;
    document.addEventListener('mousemove', calMouseMove);
    document.addEventListener('mouseup', calMouseUp);
}}

function calMouseMove(e) {{
    if (!calDragPid) return;
    const dx = e.clientX - calDragStartX, dy = e.clientY - calDragStartY;
    if (!calDragging && dx*dx + dy*dy < 25) return;

    if (!calDragging) {{
        calDragging = true;
        const card = document.getElementById('cal-card-' + calDragPid);
        if (card) card.classList.add('dragging');
        document.querySelector('.cal-grid')?.classList.add('drag-in-progress');
        createEdgeIndicators();
        calDragGhost = card.cloneNode(true);
        calDragGhost.removeAttribute('id');
        calDragGhost.style.cssText = (
            'position:fixed;pointer-events:none;z-index:9999;'
            + 'width:' + card.offsetWidth + 'px;'
            + 'border-radius:8px;overflow:hidden;'
            + 'box-shadow:0 8px 24px rgba(0,0,0,.22);'
            + 'opacity:0.96;'
        );
        document.body.appendChild(calDragGhost);
    }}

    calDragGhost.style.left = (e.clientX - calDragOffsetX) + 'px';
    calDragGhost.style.top  = (e.clientY - calDragOffsetY) + 'px';

    checkDragEdge(e.clientX);

    document.querySelectorAll('.drop-over,.card-drop-over').forEach(el =>
        el.classList.remove('drop-over','card-drop-over'));
    calDragGhost.style.display = 'none';
    const el = document.elementFromPoint(e.clientX, e.clientY);
    calDragGhost.style.display = '';
    if (el) {{
        const dropCard = el.closest('.post-card');
        if (dropCard && dropCard.dataset.pid != calDragPid && !dropCard.classList.contains('posted-card')) {{
            dropCard.classList.add('card-drop-over');
        }} else {{
            const col = el.closest('.day-col');
            if (col) col.classList.add('drop-over');
        }}
    }}
}}

function calMouseUp(e) {{
    document.removeEventListener('mousemove', calMouseMove);
    document.removeEventListener('mouseup', calMouseUp);
    const pid = calDragPid;
    const origDatetime = calDragOrigDatetime;

    if (!calDragging) {{
        calDragPid = null; calDragOrigDatetime = null;
        openModal(pid);
        return;
    }}

    if (calEdgeTimer) {{ clearTimeout(calEdgeTimer); calEdgeTimer = null; }}
    if (calDragGhost) {{ document.body.removeChild(calDragGhost); calDragGhost = null; }}
    const card = document.getElementById('cal-card-' + pid);
    if (card) card.classList.remove('dragging');
    document.querySelector('.cal-grid')?.classList.remove('drag-in-progress');
    hideEdgeIndicators();
    removeEdgeIndicators();
    document.querySelectorAll('.drop-over,.card-drop-over').forEach(el =>
        el.classList.remove('drop-over','card-drop-over'));

    const el = document.elementFromPoint(e.clientX, e.clientY);
    calDragPid = null; calDragOrigDatetime = null; calDragging = false;

    let droppedOnAdjacentCol = false;
    if (el) {{
        const dropCard = el.closest('.post-card');
        if (dropCard && dropCard.dataset.pid != pid && !dropCard.classList.contains('posted-card')) {{
            doCardSwap(pid, parseInt(dropCard.dataset.pid), origDatetime);
            droppedOnAdjacentCol = !!el.closest('.day-col');
        }} else {{
            const col = el.closest('.day-col');
            if (col) {{
                doColDrop(pid, col.id.replace('day-',''), origDatetime);
                droppedOnAdjacentCol = true;
            }}
        }}
    }}

    if (calSlid) {{
        if (droppedOnAdjacentCol) {{
            commitWeekSlide();
            cleanupSlideState();
            window.location.href = '/?week=' + calCurrentWeek;
        }} else {{
            revertWeekSlide();
        }}
    }}
}}

function doCardSwap(dragPid, targetPid, dragOrigDatetime) {{
    const dragCard = document.getElementById('cal-card-'+dragPid);
    const dropCard = document.getElementById('cal-card-'+targetPid);
    if (!dragCard || !dropCard) return;

    const dragDatetime = dragCard.dataset.time;
    const dropDatetime = dropCard.dataset.time;

    dragCard.dataset.time = dropDatetime;
    dropCard.dataset.time = dragDatetime;

    const dt1 = dragCard.querySelector('.card-time');
    const dt2 = dropCard.querySelector('.card-time');
    if (dt1) dt1.textContent = dropDatetime.substring(11,16);
    if (dt2) dt2.textContent = dragDatetime.substring(11,16);

    const dragParent = dragCard.parentNode;
    const dropParent = dropCard.parentNode;

    if (dragParent === dropParent) {{
        insertCardSorted(dragParent, dragCard);
        insertCardSorted(dragParent, dropCard);
    }} else {{
        const dragEmpty = dragParent.querySelector('.day-empty');
        if (dragEmpty) dragEmpty.remove();
        const dropEmpty = dropParent.querySelector('.day-empty');
        if (dropEmpty) dropEmpty.remove();
        insertCardSorted(dropParent, dragCard);
        insertCardSorted(dragParent, dropCard);
        if (!dragParent.querySelector('.post-card'))
            dragParent.innerHTML = '<div class="day-empty">No posts</div>';
        if (!dropParent.querySelector('.post-card'))
            dropParent.innerHTML = '<div class="day-empty">No posts</div>';
    }}

    function recordPending(pid, newDt, fallbackOrig) {{
        const orig = calPending[pid] ? calPending[pid].origDatetime : fallbackOrig;
        if (newDt !== orig) calPending[pid] = {{ newDatetime: newDt, origDatetime: orig }};
        else delete calPending[pid];
    }}
    recordPending(dragPid,   dropDatetime, dragOrigDatetime);
    recordPending(targetPid, dragDatetime, dropDatetime);
    checkCalPending();
}}

function insertCardSorted(container, card) {{
    const siblings = [...container.querySelectorAll('.post-card, .empty-slot')];
    const t = card.dataset.time || '';
    const before = siblings.find(s => s !== card && (s.dataset.time || '') > t);
    if (before) container.insertBefore(card, before);
    else container.appendChild(card);
}}

function doColDrop(pid, targetDate, origDatetime) {{
    const origDate = origDatetime.substring(0,10);
    if (targetDate === origDate) return;
    const card = document.getElementById('cal-card-'+pid);
    const targetPosts = document.getElementById('posts-'+targetDate);
    if (!card || !targetPosts) return;

    const empty = targetPosts.querySelector('.day-empty');
    if (empty) empty.remove();
    const srcPosts = card.parentNode;
    const newDatetime = targetDate + ' ' + origDatetime.substring(11,16);
    card.dataset.time = newDatetime;
    insertCardSorted(targetPosts, card);
    if (srcPosts && !srcPosts.querySelector('.post-card'))
        srcPosts.innerHTML = '<div class="day-empty">No posts</div>';

    const prevEntry = calPending[pid];
    const trueOrig  = prevEntry ? prevEntry.origDatetime : origDatetime;
    if (newDatetime !== trueOrig) calPending[pid] = {{ newDatetime, origDatetime: trueOrig }};
    else delete calPending[pid];
    checkCalPending();
}}

let calAdjacentCols = null;   // cached fetched columns
let calAdjacentDir = 0;       // -1 or 1
let calSlid = false;          // whether we've slid to show adjacent cols

function createEdgeIndicators() {{
    if (document.getElementById('edge-ind-left')) return;
    const wrap = document.querySelector('.cal-grid-wrap');
    if (!wrap) return;
    ['left','right'].forEach(side => {{
        const ind = document.createElement('div');
        ind.id = 'edge-ind-' + side;
        ind.className = 'edge-indicator edge-indicator-' + side;
        ind.innerHTML = side === 'left'
            ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="15 6 9 12 15 18"/></svg><span>Previous week</span>'
            : '<span>Next week</span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="9 6 15 12 9 18"/></svg>';
        wrap.appendChild(ind);
    }});
}}

function removeEdgeIndicators() {{
    document.getElementById('edge-ind-left')?.remove();
    document.getElementById('edge-ind-right')?.remove();
}}

function showEdgeIndicator(side) {{
    const el = document.getElementById('edge-ind-' + side);
    if (el) el.classList.add('visible');
}}

function hideEdgeIndicators() {{
    document.querySelectorAll('.edge-indicator').forEach(el => el.classList.remove('visible'));
}}

async function loadAdjacentWeek(direction) {{
    if (calWeekLoading) return;
    calWeekLoading = true;
    const newWeek = calCurrentWeek + direction;
    try {{
        const r = await fetch('/api/week-columns?week=' + newWeek);
        const d = await r.json();
        if (!d.ok) {{ calWeekLoading = false; return; }}

        const grid = document.querySelector('.cal-grid');
        const dowRow = document.querySelector('.cal-dow-row');
        if (!grid || !dowRow) {{ calWeekLoading = false; return; }}

        const tmpDiv = document.createElement('div');
        tmpDiv.innerHTML = d.cols;
        const newCols = [...tmpDiv.children];

        const tmpDow = document.createElement('div');
        tmpDow.innerHTML = d.dow;
        const newDowCells = [...tmpDow.children];

        const peekCount = 2;
        const totalCols = 7 + peekCount;

        if (direction > 0) {{
            for (let i = 0; i < peekCount; i++) {{
                if (newCols[i]) grid.appendChild(newCols[i]);
                if (newDowCells[i]) dowRow.appendChild(newDowCells[i]);
            }}
        }} else {{
            for (let i = peekCount - 1; i >= 0; i--) {{
                const colIdx = 7 - peekCount + i;
                if (newCols[colIdx]) grid.insertBefore(newCols[colIdx], grid.firstChild);
                if (newDowCells[colIdx]) dowRow.insertBefore(newDowCells[colIdx], dowRow.firstChild);
            }}
        }}

        grid.style.gridTemplateColumns = `repeat(${{totalCols}}, 1fr)`;
        dowRow.style.gridTemplateColumns = `repeat(${{totalCols}}, 1fr)`;

        if (direction < 0) {{
            const shiftPct = (peekCount / totalCols) * 100;
            grid.style.transform = `translateX(-${{shiftPct}}%)`;
            dowRow.style.transform = `translateX(-${{shiftPct}}%)`;
        }}

        requestAnimationFrame(() => {{
            grid.style.transition = 'transform .8s cubic-bezier(.25,.1,.25,1)';
            dowRow.style.transition = 'transform .8s cubic-bezier(.25,.1,.25,1)';
            if (direction > 0) {{
                const shiftPct = (peekCount / totalCols) * 100;
                grid.style.transform = `translateX(-${{shiftPct}}%)`;
                dowRow.style.transform = `translateX(-${{shiftPct}}%)`;
            }} else {{
                grid.style.transform = 'translateX(0)';
                dowRow.style.transform = 'translateX(0)';
            }}
        }});

        calAdjacentDir = direction;
        calAdjacentCols = d;
        calSlid = true;

        setTimeout(() => {{ calWeekLoading = false; }}, 850);
    }} catch(e) {{
        calWeekLoading = false;
    }}
}}

function commitWeekSlide() {{
    if (!calSlid || !calAdjacentCols) return;
    const newWeek = calCurrentWeek + calAdjacentDir;
    calCurrentWeek = newWeek;
    const titleEl = document.querySelector('.cal-title');
    if (titleEl) titleEl.textContent = calAdjacentCols.title;
    const navLinks = document.querySelectorAll('.cal-nav a');
    if (navLinks[0]) navLinks[0].href = '/?week=' + (newWeek - 1);
    if (navLinks[1]) navLinks[1].href = '/?week=' + (newWeek + 1);
}}

function revertWeekSlide() {{
    if (!calSlid) return;
    const grid = document.querySelector('.cal-grid');
    const dowRow = document.querySelector('.cal-dow-row');
    if (!grid || !dowRow) return;

    const peekCount = 2;
    if (calAdjacentDir > 0) {{
        grid.style.transition = 'transform .5s ease';
        dowRow.style.transition = 'transform .5s ease';
        grid.style.transform = 'translateX(0)';
        dowRow.style.transform = 'translateX(0)';
        setTimeout(() => {{
            for (let i = 0; i < peekCount; i++) {{
                if (grid.lastElementChild) grid.lastElementChild.remove();
                if (dowRow.lastElementChild) dowRow.lastElementChild.remove();
            }}
            grid.style.cssText = '';
            dowRow.style.cssText = '';
        }}, 520);
    }} else {{
        grid.style.transition = 'transform .5s ease';
        dowRow.style.transition = 'transform .5s ease';
        const totalCols = 7 + peekCount;
        const shiftPct = (peekCount / totalCols) * 100;
        grid.style.transform = `translateX(-${{shiftPct}}%)`;
        dowRow.style.transform = `translateX(-${{shiftPct}}%)`;
        setTimeout(() => {{
            for (let i = 0; i < peekCount; i++) {{
                if (grid.firstElementChild) grid.firstElementChild.remove();
                if (dowRow.firstElementChild) dowRow.firstElementChild.remove();
            }}
            grid.style.cssText = '';
            dowRow.style.cssText = '';
        }}, 520);
    }}
    calSlid = false;
    calAdjacentCols = null;
    calAdjacentDir = 0;
}}

function cleanupSlideState() {{
    const grid = document.querySelector('.cal-grid');
    const dowRow = document.querySelector('.cal-dow-row');
    if (grid) grid.style.cssText = '';
    if (dowRow) dowRow.style.cssText = '';
    calSlid = false;
    calAdjacentCols = null;
    calAdjacentDir = 0;
    calWeekLoading = false;
}}

function checkDragEdge(clientX) {{
    const wrap = document.querySelector('.cal-grid-wrap');
    if (!wrap) return;
    const rect = wrap.getBoundingClientRect();
    const edgeZone = 70;
    const nearLeft = clientX < rect.left + edgeZone;
    const nearRight = clientX > rect.right - edgeZone;

    if (nearLeft) showEdgeIndicator('left');
    else if (nearRight) showEdgeIndicator('right');

    if (!nearLeft && !nearRight) {{
        hideEdgeIndicators();
        if (calEdgeTimer) {{ clearTimeout(calEdgeTimer); calEdgeTimer = null; }}
        return;
    }}
    if (calEdgeTimer || calSlid) return;
    const dir = nearRight ? 1 : -1;
    calEdgeTimer = setTimeout(() => {{
        calEdgeTimer = null;
        if (calDragging && !calSlid) {{
            hideEdgeIndicators();
            loadAdjacentWeek(dir);
        }}
    }}, 700);
}}

async function retryPost(pid, btn) {{
    btn.disabled = true; btn.textContent = 'Re-queuing…';
    try {{
        const r = await fetch('/retry/' + pid, {{ method: 'POST' }});
        const d = await r.json();
        if (d.ok) {{
            document.getElementById('interrupted-alert-' + pid)?.remove();
            const card = document.getElementById('cal-card-' + pid);
            if (card) {{
                const badge = card.querySelector('.post-badge');
                if (badge) {{ badge.className = 'badge badge-not-uploaded post-badge'; badge.textContent = 'Not Uploaded'; }}
                card.dataset.status = 'scheduled';
            }}
            showToast('↩ Post re-queued — will upload shortly');
        }} else {{
            btn.disabled = false; btn.textContent = '↩ Retry Upload';
            alert(d.error || 'Could not re-queue post.');
        }}
    }} catch(e) {{
        btn.disabled = false; btn.textContent = '↩ Retry Upload';
        alert('Network error — please try again.');
    }}
}}

async function markPosted(pid, btn) {{
    btn.disabled = true; btn.textContent = 'Saving…';
    try {{
        const r = await fetch('/mark-posted/' + pid, {{ method: 'POST' }});
        const d = await r.json();
        if (d.ok) {{
            document.getElementById('interrupted-alert-' + pid)?.remove();
            const card = document.getElementById('cal-card-' + pid);
            if (card) {{
                const badge = card.querySelector('.post-badge');
                if (badge) {{ badge.className = 'badge badge-posted post-badge'; badge.textContent = 'Posted'; }}
                card.dataset.status = 'posted';
                card.classList.add('posted-card');
            }}
            showToast('✓ Marked as posted');
        }} else {{
            btn.disabled = false; btn.textContent = '✓ Already on Facebook';
            alert(d.error || 'Could not update post.');
        }}
    }} catch(e) {{
        btn.disabled = false; btn.textContent = '✓ Already on Facebook';
        alert('Network error — please try again.');
    }}
}}

function dismissAlert(pid) {{
    document.getElementById('interrupted-alert-' + pid)?.remove();
}}

function checkCalPending() {{
    const hasChanges = Object.keys(calPending).length > 0;
    document.getElementById('reorder-bar').classList.toggle('hidden', !hasChanges);
}}

function cancelCalReorder() {{
    const cardEls = {{}};
    const affectedCols = new Set();
    for (const pid in calPending) {{
        const card = document.getElementById('cal-card-'+pid);
        if (!card) continue;
        cardEls[pid] = {{ el: card, from: card.getBoundingClientRect() }};
        affectedCols.add(card.parentNode);
    }}

    for (const pid in calPending) {{
        const card = cardEls[pid]?.el;
        if (!card) continue;
        const origDatetime = calPending[pid].origDatetime;
        const origDate = origDatetime.substring(0,10);
        const origTime = origDatetime.substring(11,16);
        const srcPosts = card.parentNode;
        const dstPosts = document.getElementById('posts-'+origDate);
        if (dstPosts && srcPosts !== dstPosts) {{
            const empty = dstPosts.querySelector('.day-empty');
            if (empty) empty.remove();
            dstPosts.appendChild(card);
            if (srcPosts && srcPosts.querySelectorAll('.post-card').length === 0)
                srcPosts.innerHTML = '<div class="day-empty">No posts</div>';
            affectedCols.add(dstPosts);
        }}
        card.dataset.time = origDatetime;
        const timeEl = card.querySelector('.card-time');
        if (timeEl) timeEl.textContent = origTime;
    }}

    affectedCols.forEach(col => {{
        const items = [...col.querySelectorAll('.post-card, .empty-slot')];
        items.sort((a,b) => (a.dataset.time||'').localeCompare(b.dataset.time||''));
        items.forEach(c => col.appendChild(c));
    }});

    for (const pid in cardEls) {{
        const card = cardEls[pid].el;
        const from = cardEls[pid].from;
        const to = card.getBoundingClientRect();
        const dx = from.left - to.left;
        const dy = from.top - to.top;
        if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
        card.style.transform = `translate(${{dx}}px, ${{dy}}px)`;
        card.style.transition = 'none';
        requestAnimationFrame(() => {{
            requestAnimationFrame(() => {{
                card.style.transition = 'transform 0.35s cubic-bezier(.25,.8,.25,1)';
                card.style.transform = '';
                card.addEventListener('transitionend', function handler() {{
                    card.style.transition = '';
                    card.removeEventListener('transitionend', handler);
                }});
            }});
        }});
    }}

    calPending = {{}};
    checkCalPending();
}}

async function submitCalReorder() {{
    const btn = document.getElementById('submit-reorder-btn');
    if (btn.disabled) return;
    btn.disabled = true;

    const pids = Object.keys(calPending);
    const swaps = pids.map(pid => ({{
        post_id: parseInt(pid),
        new_time: calPending[pid].newDatetime + ':00'
    }}));

    document.getElementById('reorder-bar').classList.add('hidden');
    pids.forEach(pid => {{
        const badge = document.getElementById('badge-'+pid);
        if (badge) {{ badge.className = 'badge badge-uploading post-badge'; badge.textContent = 'Updating'; }}
    }});

    try {{
        const r = await fetch('/reorder', {{
            method:'POST', headers:{{'Content-Type':'application/json'}},
            body: JSON.stringify({{swaps}})
        }});
        const d = await r.json();
        if (d.ok) {{
            pids.forEach(pid => {{
                const badge = document.getElementById('badge-'+pid);
                if (badge) {{ badge.className = 'badge badge-scheduled post-badge'; badge.textContent = 'Scheduled'; }}
            }});
            calPending = {{}};
            showToast('✓ Schedule updated');
        }} else {{
            alert(d.error || 'Could not update schedule.');
            btn.disabled = false;
            document.getElementById('reorder-bar').classList.remove('hidden');
        }}
    }} catch(err) {{
        alert('Network error — please try again.');
        btn.disabled = false;
        document.getElementById('reorder-bar').classList.remove('hidden');
    }}
}}

// ── Single slot upload ────────────────────────────────────
let slotUploadFile = null;
let slotUploadTime = null;

function slotUpload(input, schedTime) {{
    const file = input.files[0];
    if (!file) return;
    input.value = '';
    slotUploadFile = file;
    slotUploadTime = schedTime;

    // Show caption modal for single file
    batchFiles = [file];
    batchCaptions = [''];
    batchIndex = 0;

    const overlay = document.getElementById('batch-caption-overlay');
    const img = document.getElementById('batch-caption-img');
    const textarea = document.getElementById('batch-caption-text');
    const counter = document.getElementById('batch-caption-counter');
    const title = document.getElementById('batch-caption-title');

    title.textContent = file.name;
    counter.textContent = '';
    textarea.value = '';

    const url = URL.createObjectURL(file);
    img.onload = function() {{ URL.revokeObjectURL(url); }};
    img.src = url;

    // Override the next button to do single-slot upload
    document.getElementById('batch-caption-next-btn').onclick = async function() {{
        const caption = textarea.value.trim();
        overlay.classList.remove('open');
        const fd = new FormData();
        fd.append('photo', slotUploadFile);
        fd.append('scheduled_time', slotUploadTime);
        fd.append('caption', caption);
        try {{
            const r = await fetch('/upload-photo', {{ method:'POST', body:fd }});
            const d = await r.json();
            if (d.ok) {{
                window.location.href = '/';
            }} else {{
                alert(d.error || 'Upload failed');
            }}
        }} catch(e) {{
            alert('Upload error — please try again.');
        }}
        slotUploadFile = null;
        slotUploadTime = null;
    }};

    overlay.classList.add('open');
    textarea.focus();
}}

// ── Upload ───────────────────────────────────────────────
let batchFiles = [];
let batchCaptions = [];
let batchIndex = 0;

function openBatchUpload() {{
    batchFiles = [];
    batchCaptions = [];
    batchIndex = 0;
    document.getElementById('batch-file-list').innerHTML = '';
    document.getElementById('batch-overlay').classList.add('open');
}}
function closeBatchUpload(clearFiles) {{
    document.getElementById('batch-overlay').classList.remove('open');
    if (clearFiles) batchFiles = [];
}}
function batchBackdropClick(e) {{
    if (e.target === document.getElementById('batch-overlay')) closeBatchUpload(true);
}}
function batchFilesSelected(input) {{
    for (const f of input.files) batchFiles.push(f);
    renderBatchFiles();
    input.value = '';
}}
function renderBatchFiles() {{
    const el = document.getElementById('batch-file-list');
    if (!batchFiles.length) {{ el.innerHTML = ''; return; }}
    el.innerHTML = batchFiles.map((f,i) =>
        '<div style="display:flex;justify-content:space-between;padding:4px 0">' +
        '<span>' + f.name + '</span>' +
        '<button onclick="batchFiles.splice('+i+',1);renderBatchFiles()" style="background:none;border:none;color:#c00;cursor:pointer;font-size:12px">✕</button>' +
        '</div>'
    ).join('');
}}

function startBatchCaptions() {{
    if (!batchFiles.length) {{ alert('Please select photos first.'); return; }}
    batchCaptions = batchFiles.map(() => '');
    batchIndex = 0;
    closeBatchUpload();
    showBatchCaption();
}}

function showBatchCaption() {{
    const file = batchFiles[batchIndex];
    const overlay = document.getElementById('batch-caption-overlay');
    const img = document.getElementById('batch-caption-img');
    const textarea = document.getElementById('batch-caption-text');
    const counter = document.getElementById('batch-caption-counter');
    const title = document.getElementById('batch-caption-title');

    title.textContent = file.name;
    counter.textContent = (batchIndex + 1) + ' of ' + batchFiles.length;
    textarea.value = batchCaptions[batchIndex] || '';

    const url = URL.createObjectURL(file);
    img.onload = function() {{ URL.revokeObjectURL(url); }};
    img.src = url;

    const nextBtn = document.getElementById('batch-caption-next-btn');
    nextBtn.onclick = batchCaptionNext;
    nextBtn.textContent = (batchIndex < batchFiles.length - 1) ? 'Next →' : 'Add to Queue';
    document.getElementById('batch-caption-prev-btn').style.display = batchIndex > 0 ? '' : 'none';

    overlay.classList.add('open');
    textarea.focus();
}}

function batchCaptionNext() {{
    batchCaptions[batchIndex] = document.getElementById('batch-caption-text').value.trim();
    if (batchIndex < batchFiles.length - 1) {{
        batchIndex++;
        showBatchCaption();
    }} else {{
        document.getElementById('batch-caption-overlay').classList.remove('open');
        submitBatchWithCaptions();
    }}
}}

function batchCaptionPrev() {{
    batchCaptions[batchIndex] = document.getElementById('batch-caption-text').value.trim();
    if (batchIndex > 0) {{
        batchIndex--;
        showBatchCaption();
    }}
}}

function batchCaptionSkip() {{
    batchCaptions[batchIndex] = '';
    if (batchIndex < batchFiles.length - 1) {{
        batchIndex++;
        showBatchCaption();
    }} else {{
        document.getElementById('batch-caption-overlay').classList.remove('open');
        submitBatchWithCaptions();
    }}
}}

async function batchCaptionPostNow() {{
    const caption = document.getElementById('batch-caption-text').value.trim();
    const file = batchFiles[batchIndex];
    const btn = document.getElementById('batch-caption-postnow-btn');
    btn.disabled = true; btn.textContent = 'Posting…';
    const fd = new FormData();
    fd.append('photo', file);
    fd.append('caption', caption);
    try {{
        const r = await fetch('/upload-and-post-now', {{ method:'POST', body:fd }});
        const d = await r.json();
        if (d.ok) {{
            showToast('✓ Posted to Facebook');
            // Remove this file from batch and continue
            batchFiles.splice(batchIndex, 1);
            batchCaptions.splice(batchIndex, 1);
            if (batchFiles.length === 0) {{
                document.getElementById('batch-caption-overlay').classList.remove('open');
                window.location.reload();
            }} else {{
                if (batchIndex >= batchFiles.length) batchIndex = batchFiles.length - 1;
                showBatchCaption();
            }}
        }} else {{
            alert(d.error || 'Post failed');
        }}
    }} catch(e) {{
        alert('Network error — please try again.');
    }}
    btn.disabled = false; btn.textContent = '⚡ Post Now';
}}

function closeBatchCaption() {{
    document.getElementById('batch-caption-overlay').classList.remove('open');
    batchFiles = [];
    batchCaptions = [];
}}

async function submitBatchWithCaptions() {{
    const fd = new FormData();
    for (let i = 0; i < batchFiles.length; i++) {{
        fd.append('photos', batchFiles[i]);
        fd.append('captions', batchCaptions[i] || '');
    }}
    try {{
        const r = await fetch('/upload-batch', {{ method:'POST', body:fd }});
        const d = await r.json();
        if (d.ok) {{
            showToast('✓ ' + d.count + ' photo(s) added to queue');
            window.location.reload();
        }} else {{
            alert(d.error || 'Upload failed');
        }}
    }} catch(e) {{
        alert('Upload error — please try again.');
    }}
    batchFiles = [];
    batchCaptions = [];
}}

// ── Auto-open modal after upload redirect ─────────────────
document.addEventListener('DOMContentLoaded', function() {{
    const params = new URLSearchParams(window.location.search);
    const openId = params.get('openModal');
    if (openId) {{
        openModal(parseInt(openId));
        modalStartEditCaption();
    }}
}});

document.addEventListener('keydown', function(e) {{
    if (e.key === 'Escape') {{
        const captionOverlay = document.getElementById('batch-caption-overlay');
        if (captionOverlay && captionOverlay.classList.contains('open')) {{
            captionOverlay.classList.remove('open');
            return;
        }}
        const modal = document.getElementById('post-modal');
        if (modal && modal.classList.contains('open')) {{
            closeModal();
        }}
    }}
}});

// ── Status polling ───────────────────────────────────────
let pollTimer = null;
function pollStatuses() {{
    const cards = document.querySelectorAll('.post-card');
    const watchIds = [];
    cards.forEach(c => {{
        const st = c.dataset.status;
        if (st === 'scheduled' || st === 'retrying' || st === 'posting') {{
            watchIds.push(c.dataset.pid);
        }}
    }});
    if (!watchIds.length) return;

    fetch('/api/post-statuses?ids=' + watchIds.join(','))
        .then(r => r.json())
        .then(data => {{
            for (const [pid, info] of Object.entries(data)) {{
                const card = document.getElementById('cal-card-' + pid);
                const badge = document.getElementById('badge-' + pid);
                if (!card || !badge) continue;
                if (card.dataset.status !== info.status) {{
                    card.dataset.status = info.status;
                    card.dataset.fbid = info.fbid || '';
                    badge.className = 'badge badge-' + info.badge_cls + ' post-badge';
                    badge.textContent = info.badge_lbl;
                    if (info.has_fb_thumb) {{
                        const img = card.querySelector('img');
                        if (img) img.src = '/thumb/' + pid + '?t=' + Date.now();
                    }}
                }}
            }}
        }})
        .catch(() => {{}});
}}
pollTimer = setInterval(pollStatuses, 5000);
</script>

<!-- Upload Modal -->
<div class="batch-overlay" id="batch-overlay" onclick="batchBackdropClick(event)">
  <div class="batch-box">
    <div class="batch-hdr">
      <h3>Upload Photos</h3>
      <button class="modal-close" onclick="closeBatchUpload(true)">&#x2715;</button>
    </div>
    <div class="batch-body">
      <label class="batch-dropzone" id="batch-dropzone"
        ondragover="event.preventDefault();this.classList.add('drag-over')"
        ondragleave="this.classList.remove('drag-over')"
        ondrop="event.preventDefault();this.classList.remove('drag-over');for(const f of event.dataTransfer.files)batchFiles.push(f);renderBatchFiles();">
        <input type="file" accept="image/*" multiple style="display:none" onchange="batchFilesSelected(this)">
        Click or drag photos here
      </label>
      <div id="batch-file-list" class="batch-file-list"></div>
    </div>
    <div class="batch-footer">
      <button class="btn btn-primary" onclick="startBatchCaptions()">Continue</button>
    </div>
  </div>
</div>

<!-- Caption entry modal (shown per-photo before saving) -->
<div class="modal-overlay" id="batch-caption-overlay" onclick="if(event.target===this)closeBatchCaption()">
  <div class="modal-box">
    <div class="modal-hdr">
      <h3 id="batch-caption-title">Add Caption</h3>
      <div style="display:flex;align-items:center;gap:12px">
        <span id="batch-caption-counter" style="font-size:12px;color:#948466"></span>
        <button class="modal-close" onclick="closeBatchCaption()">✕</button>
      </div>
    </div>
    <div class="modal-body">
      <img id="batch-caption-img" class="modal-img" src="" alt="preview">
      <div class="modal-section">
        <div class="modal-label">Caption</div>
        <textarea class="modal-textarea" id="batch-caption-text" rows="4" spellcheck="true" lang="en" placeholder="Enter a caption…"></textarea>
      </div>
    </div>
    <div class="modal-footer">
      <div class="modal-footer-left">
        <button class="btn btn-secondary" onclick="batchCaptionPrev()" id="batch-caption-prev-btn">← Back</button>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn btn-secondary" onclick="batchCaptionSkip()">Skip</button>
        <button class="btn btn-success" id="batch-caption-postnow-btn" onclick="batchCaptionPostNow()">⚡ Post Now</button>
        <button class="btn btn-primary" id="batch-caption-next-btn" onclick="batchCaptionNext()">Add to Queue</button>
      </div>
    </div>
  </div>
</div>'''

    content = f'''<style>{CAL_CSS}
.post-card {{ padding-bottom:2px; }}
.post-card .card-body {{ padding:10px 10px 8px; }}
.post-card .card-foot {{ padding:6px 10px 10px; }}
.interrupted-alert {{
    display:flex;align-items:center;gap:12px;
    background:white;border-radius:10px;padding:14px 16px;
    margin-bottom:12px;
    box-shadow:0 2px 8px rgba(0,0,0,.08);
    border-left:3px solid #B56152;
}}
.cal-grid.drag-in-progress .post-card:not(.dragging):not(.card-drop-over) {{
    opacity:0.35; transition:opacity .1s;
}}
.post-card .select-check {{
    display:none; position:absolute; top:6px; left:6px; z-index:10;
    width:20px; height:20px; accent-color:#749A96; cursor:pointer;
}}
.select-mode .post-card {{ position:relative; cursor:pointer !important; }}
.select-mode .post-card .select-check {{ display:block; }}
.select-mode .post-card.selected {{ outline:2px solid #749A96; outline-offset:-2px; border-radius:10px; }}
#multi-select-bar {{
    position:sticky; bottom:0; left:0; right:0;
    background:#343434; color:white; padding:14px 24px;
    display:none; align-items:center; gap:16px; z-index:100;
    box-shadow:0 -2px 12px rgba(0,0,0,.25);
}}
#multi-select-bar.visible {{ display:flex; }}
#multi-select-bar .select-count {{ font-size:15px; font-weight:500; }}
#multi-select-bar .btn {{ height:36px; padding:0 16px; font-size:13px;
    display:flex; align-items:center; justify-content:center; gap:4px; }}
.card-drop-over {{
    opacity:1 !important;
    box-shadow:0 4px 20px rgba(0,0,0,.18) !important;
    transform:scale(1.02);
    transition:transform .1s, box-shadow .1s;
}}
{modal_css}
.fab-upload {{
  position:fixed; bottom:32px; left:242px; z-index:400;
  width:56px; height:56px; border-radius:50%; border:none;
  background:#749A96; color:white; font-size:28px; line-height:1;
  cursor:pointer; box-shadow:0 4px 14px rgba(0,0,0,.25);
  transition:transform .15s, box-shadow .15s;
  display:flex; align-items:center; justify-content:center;
}}
.fab-upload:hover {{
  transform:scale(1.08);
  box-shadow:0 6px 20px rgba(0,0,0,.3);
}}
.fab-upload:active {{ transform:scale(.95); }}
</style>
<h2>Queue</h2>
{''.join(f"""
<div class="interrupted-alert" id="interrupted-alert-{p['id']}">
  <div style="flex:1;min-width:0;">
    <div style="font-weight:600;font-size:13px;margin-bottom:3px">Upload interrupted</div>
    <div style="font-size:12px;color:#948466;margin-bottom:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{(p['caption'] or 'No caption')[:60]} · {(p['scheduled_time'] or '')[:16]}</div>
    <div style="font-size:11px;color:#B56152;font-style:italic">⚠ The upload may have already completed. Check Facebook before retrying to avoid a duplicate post.</div>
  </div>
  <div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end;flex-shrink:0;">
    <div style="display:flex;gap:6px;">
      {'<a href="https://www.facebook.com/' + fb_page_id + '" target="_blank" class="btn btn-secondary" style="font-size:12px;height:32px;padding:0 12px">Check Facebook ↗</a>' if fb_page_id else ''}
      <button class="btn btn-primary" style="font-size:12px;height:32px;padding:0 12px"
              onclick="retryPost({p['id']}, this)">↩ Retry Upload</button>
    </div>
    <div style="display:flex;gap:6px;">
      <button class="btn btn-secondary" style="font-size:11px;height:28px;padding:0 10px;color:#749A96"
              onclick="markPosted({p['id']}, this)">✓ Already on Facebook</button>
      <button class="btn btn-secondary" style="font-size:11px;height:28px;padding:0 10px"
              onclick="dismissAlert({p['id']})">Dismiss</button>
    </div>
  </div>
</div>
""" for p in interrupted) if interrupted else ''}
{nav}
<div class="cal-grid-wrap">
  <div class="cal-dow-row">
    <div class="cal-dow-cell">Mon</div>
    <div class="cal-dow-cell">Tue</div>
    <div class="cal-dow-cell">Wed</div>
    <div class="cal-dow-cell">Thu</div>
    <div class="cal-dow-cell">Fri</div>
    <div class="cal-dow-cell">Sat</div>
    <div class="cal-dow-cell">Sun</div>
  </div>
  <div class="cal-grid">{cols}</div>
</div>
<button class="fab-upload" onclick="openBatchUpload()" title="Upload photos">+</button>
{page_js}'''

    return layout('queue', content)


@app.route('/update-caption/<int:post_id>', methods=['POST'])
def update_caption(post_id):
    data = request.get_json()
    caption = data.get('caption', '').strip()
    row = conn.execute("SELECT status, facebook_post_id FROM posts WHERE id=?", (post_id,)).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'Post not found'})

    if row['status'] in ('fb_scheduled', 'posted') and row['facebook_post_id']:
        page_id = get_setting(conn, 'page_id')
        token   = get_setting(conn, 'page_access_token')
        if page_id and token:
            try:
                client = FacebookClient(page_id, token)
                client.update_post_message(row['facebook_post_id'], caption)
                conn.execute("UPDATE posts SET caption=? WHERE id=?", (caption, post_id))
                conn.commit()
                badge = 'posted' if row['status'] == 'posted' else 'scheduled'
                label = 'Posted' if row['status'] == 'posted' else 'Scheduled'
                log_event(conn, post_id, 'caption_updated', description='Caption updated on Facebook successfully')
                return jsonify({'ok': True, 'badge': badge, 'label': label})
            except Exception as e:
                return jsonify({'ok': False, 'error': str(e)})

    status = row['status']
    badge_map = {'scheduled': ('scheduled', 'Scheduled'), 'posted': ('posted', 'Posted'),
                 'fb_scheduled': ('scheduled', 'Scheduled')}
    badge, label = badge_map.get(status, ('not-uploaded', 'Not Uploaded'))
    conn.execute("UPDATE posts SET caption=? WHERE id=?", (caption, post_id))
    conn.commit()
    return jsonify({'ok': True, 'badge': badge, 'label': label})


@app.route('/reschedule/<int:post_id>', methods=['POST'])
def reschedule(post_id):
    data = request.get_json()
    new_time = data.get('scheduled_time', '').strip()
    if not new_time:
        return jsonify({'ok': False, 'error': 'No time provided'})

    row = conn.execute("SELECT status FROM posts WHERE id=?", (post_id,)).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'Post not found'})

    if row[0] == 'posted':
        return jsonify({'ok': False, 'error': 'Already live on Facebook — cannot reschedule'})

    if row[0] == 'fb_scheduled':
        _reset_to_scheduled(post_id, new_time)
        log_event(conn, post_id, 'rescheduled', description='Post rescheduled successfully')
        return jsonify({'ok': True, 'fb_rescheduled': True})
    else:
        conn.execute("UPDATE posts SET scheduled_time=? WHERE id=?", (new_time, post_id))
        conn.commit()
        log_event(conn, post_id, 'rescheduled', description='Post rescheduled successfully')
        return jsonify({'ok': True, 'fb_rescheduled': False})


@app.route('/reorder', methods=['POST'])
def reorder():
    data = request.get_json()
    swaps = data.get('swaps', [])
    if not swaps:
        return jsonify({'ok': True})
    for swap in swaps:
        pid = int(swap['post_id'])
        new_time = swap['new_time'].strip()
        if not new_time:
            continue
        row = conn.execute("SELECT status FROM posts WHERE id=?", (pid,)).fetchone()
        if not row or row[0] == 'posted':
            continue
        if row[0] == 'fb_scheduled':
            _reset_to_scheduled(pid, new_time)
        else:
            conn.execute("UPDATE posts SET scheduled_time=? WHERE id=?", (new_time, pid))
        log_event(conn, pid, 'rescheduled', description='Post rescheduled via queue reorder')
    conn.commit()
    return jsonify({'ok': True})


@app.route('/api/week-columns')
def api_week_columns():
    from datetime import date as dt_date, timedelta
    import json as _json
    import html as _html
    week_offset = int(request.args.get('week', 0))
    today = dt_date.today()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    days = [monday + timedelta(days=i) for i in range(7)]

    all_posts_raw = get_all_posts(conn, 'all')
    DOW_MAP = {0:'MON',1:'TUE',2:'WED',3:'THU',4:'FRI',5:'SAT',6:'SUN'}
    schedule_slots = {d: [] for d in days}
    sched_row = conn.execute("SELECT * FROM schedule_patterns WHERE is_active=1 LIMIT 1").fetchone()
    if sched_row:
        try:
            raw = _json.loads(sched_row['times_of_day'] or '{}')
            if sched_row['pattern_type'] == 'custom' and isinstance(raw, dict):
                for d in days:
                    schedule_slots[d] = sorted(raw.get(DOW_MAP[d.weekday()], []))
            elif sched_row['pattern_type'] in ('weekly', 'daily'):
                active_days = _json.loads(sched_row['days_of_week'] or '[]')
                flat = sorted(raw) if isinstance(raw, list) else []
                for d in days:
                    if DOW_MAP[d.weekday()] in active_days:
                        schedule_slots[d] = flat
        except Exception:
            pass

    def post_date(p):
        try:
            return dt_date.fromisoformat((p['scheduled_time'] or '')[:10])
        except Exception:
            return None

    by_day = {d: [] for d in days}
    for p in all_posts_raw:
        d = post_date(p)
        if d in by_day:
            by_day[d].append(dict(p))
    for d in days:
        by_day[d].sort(key=lambda p: p['scheduled_time'] or '')

    badge_cls = {
        'scheduled':'not-uploaded','retrying':'not-uploaded','failed':'not-uploaded',
        'missed':'not-uploaded','posting':'uploading','fb_scheduled':'scheduled','posted':'posted',
    }
    badge_lbl = {
        'scheduled':'Not Uploaded','retrying':'Not Uploaded','failed':'Not Uploaded',
        'missed':'Not Uploaded','posting':'Uploading','fb_scheduled':'Scheduled','posted':'Posted',
    }
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cols = ''
    for d in days:
        is_today = (d == today)
        today_cls = ' today' if is_today else ''
        dom = d.strftime('%-d')
        date_str = d.strftime('%Y-%m-%d')
        day_posts = by_day[d]
        day_slots = schedule_slots[d]
        filled_times = set()
        for p in day_posts:
            t = (p['scheduled_time'] or '')[11:16]
            if t:
                filled_times.add(t)
        all_times = sorted(set(day_slots) | filled_times)
        posts_by_time = {}
        for p in day_posts:
            t = (p['scheduled_time'] or '')[11:16]
            posts_by_time.setdefault(t, []).append(p)
        posts_html = ''
        for slot_time in all_times:
            if slot_time in posts_by_time:
                for p in posts_by_time[slot_time]:
                    pid = p['id']
                    st = p['status']
                    t = (p['scheduled_time'] or '')[11:16]
                    cap = (p['caption'] or '').strip()
                    photo_path = p['photo_path'] or ''
                    fb_post_id = p['facebook_post_id'] or ''
                    is_draggable = st not in ('posted', 'posting')
                    bc = badge_cls.get(st, 'not-uploaded')
                    bl = badge_lbl.get(st, st)
                    full_dt = (p['scheduled_time'] or '')[:16]
                    cap_escaped = _html.escape(cap, quote=True).replace('\n', '&#10;')
                    fb_thumb = p.get('fb_thumbnail_url', '')
                    if (photo_path and os.path.exists(photo_path)) or fb_thumb:
                        img_html = f'<img src="/thumb/{pid}" alt="">'
                    else:
                        img_html = '<div class="card-no-img">No image</div>'
                    cap_html = f'<div class="card-cap">{cap[:80]}</div>' if cap else '<div class="card-cap no-cap">No caption</div>'
                    posted_cls2 = ' posted-card' if not is_draggable else ''
                    if is_draggable:
                        interact = f'onmousedown="calMouseDown(event,{pid},\'{full_dt}\')"'
                    else:
                        interact = f'onclick="openModal({pid})"'
                    posts_html += (
                        f'<div class="post-card{posted_cls2}" id="cal-card-{pid}" '
                        f'data-pid="{pid}" data-time="{full_dt}" '
                        f'data-cap="{cap_escaped}" data-status="{st}" data-fbid="{fb_post_id}" '
                        f'{interact}>\n'
                        f'  <input type="checkbox" class="select-check" data-pid="{pid}" onclick="event.stopPropagation();toggleCardSelect({pid})">\n'
                        f'  {img_html}\n'
                        f'  <div class="card-body">\n'
                        f'    <div class="card-time">{t}</div>\n'
                        f'    {cap_html}\n'
                        f'  </div>\n'
                        f'  <div class="card-foot">\n'
                        f'    <span class="badge badge-{bc} post-badge" id="badge-{pid}">{bl}</span>\n'
                        f'  </div>\n'
                        f'</div>'
                    )
            else:
                sched_dt = f'{date_str} {slot_time}:00'
                if sched_dt >= now_str:
                    posts_html += (
                        f'<label class="empty-slot empty-slot-upload" data-time="{sched_dt}" title="Upload a photo for {slot_time}">'
                        f'<input type="file" accept="image/*" style="display:none" '
                        f'onchange="slotUpload(this,\'{sched_dt}\')">'
                        f'<span class="empty-slot-time">{slot_time}</span>'
                        f'<span class="empty-slot-icon">+</span>'
                        f'</label>'
                    )
                else:
                    posts_html += (
                        f'<div class="empty-slot" data-time="{sched_dt}">'
                        f'<span class="empty-slot-time">{slot_time}</span>'
                        f'</div>'
                    )
        if not posts_html:
            if day_slots:
                for slot_time in day_slots:
                    sched_dt2 = f'{date_str} {slot_time}:00'
                    if sched_dt2 >= now_str:
                        posts_html += (
                            f'<label class="empty-slot empty-slot-upload" data-time="{sched_dt2}" title="Upload a photo for {slot_time}">'
                            f'<input type="file" accept="image/*" style="display:none" '
                            f'onchange="slotUpload(this,\'{sched_dt2}\')">'
                            f'<span class="empty-slot-time">{slot_time}</span>'
                            f'<span class="empty-slot-icon">+</span>'
                            f'</label>'
                        )
                    else:
                        posts_html += (
                            f'<div class="empty-slot" data-time="{sched_dt2}">'
                            f'<span class="empty-slot-time">{slot_time}</span>'
                            f'</div>'
                        )
            if not posts_html:
                posts_html = '<div class="day-empty">No posts</div>'
        cols += (
            f'<div class="day-col{today_cls}" id="day-{date_str}">\n'
            f'  <div class="day-hdr">\n'
            f'    <div class="dom">{dom}</div>\n'
            f'  </div>\n'
            f'  <div class="day-posts" id="posts-{date_str}">{posts_html}</div>\n'
            f'</div>'
        )

    title = f"{monday.strftime('%b %-d')} – {days[-1].strftime('%-d, %Y')}"
    if monday.month != days[-1].month:
        title = f"{monday.strftime('%b %-d')} – {days[-1].strftime('%b %-d, %Y')}"
    dow_html = ''.join(f'<div class="cal-dow-cell">{d.strftime("%a")}</div>' for d in days)
    return jsonify(ok=True, cols=cols, title=title, dow=dow_html, week=week_offset)


@app.route('/api/post-statuses')
def api_post_statuses():
    pids = request.args.get('ids', '')
    if not pids:
        return jsonify({})
    pid_list = [int(x) for x in pids.split(',') if x.strip().isdigit()]
    if not pid_list:
        return jsonify({})
    placeholders = ','.join('?' * len(pid_list))
    rows = conn.execute(
        f"SELECT id, status, facebook_post_id, fb_thumbnail_url FROM posts WHERE id IN ({placeholders})", pid_list
    ).fetchall()
    badge_cls = {
        'scheduled': 'not-uploaded', 'retrying': 'not-uploaded',
        'failed': 'not-uploaded', 'missed': 'not-uploaded',
        'posting': 'uploading', 'fb_scheduled': 'scheduled', 'posted': 'posted',
    }
    badge_lbl = {
        'scheduled': 'Not Uploaded', 'retrying': 'Not Uploaded',
        'failed': 'Not Uploaded', 'missed': 'Not Uploaded',
        'posting': 'Uploading', 'fb_scheduled': 'Scheduled', 'posted': 'Posted',
    }
    result = {}
    for r in rows:
        result[str(r['id'])] = {
            'status': r['status'],
            'badge_cls': badge_cls.get(r['status'], 'not-uploaded'),
            'badge_lbl': badge_lbl.get(r['status'], r['status']),
            'fbid': r['facebook_post_id'] or '',
            'has_fb_thumb': bool(r['fb_thumbnail_url']),
        }
    return jsonify(result)


@app.route('/upload-photo', methods=['POST'])
def upload_photo():
    from core.db import get_export_dir
    export_dir = get_export_dir()
    export_dir.mkdir(parents=True, exist_ok=True)

    scheduled_time = request.form.get('scheduled_time', '').strip()
    if not scheduled_time:
        return jsonify({'ok': False, 'error': 'No scheduled time'})

    file = request.files.get('photo')
    if not file or not file.filename:
        return jsonify({'ok': False, 'error': 'No photo selected'})

    ext = os.path.splitext(file.filename)[1] or '.jpg'
    dest_name = f"{uuid.uuid4().hex}{ext}"
    dest_path = str(export_dir / dest_name)
    file.save(dest_path)

    caption = request.form.get('caption', '').strip()

    conn.execute(
        "INSERT INTO posts (scheduled_time, status, caption, photo_path) VALUES (?, 'scheduled', ?, ?)",
        (scheduled_time, caption, dest_path)
    )
    conn.commit()
    post_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    log_event(conn, post_id, 'attempt', description='Photo uploaded via web app')

    return jsonify({'ok': True, 'post_id': post_id})


@app.route('/api/schedule-post', methods=['POST'])
def api_schedule_post():
    data = request.get_json()
    if not data:
        return jsonify({'ok': False, 'error': 'Invalid JSON'})
    scheduled_time = (data.get('scheduled_time') or '').strip()
    caption = (data.get('caption') or '').strip()
    photo_path = (data.get('photo_path') or '').strip()
    if not scheduled_time:
        return jsonify({'ok': False, 'error': 'No scheduled time'})
    if not photo_path or not os.path.exists(photo_path):
        return jsonify({'ok': False, 'error': 'Photo not found'})
    conn.execute(
        "INSERT INTO posts (scheduled_time, status, caption, photo_path) VALUES (?, 'scheduled', ?, ?)",
        (scheduled_time, caption, photo_path)
    )
    conn.commit()
    post_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    log_event(conn, post_id, 'created', description='Post scheduled via Lightroom plugin')
    return jsonify({'ok': True, 'post_id': post_id})


@app.route('/upload-and-post-now', methods=['POST'])
def upload_and_post_now():
    from core.db import get_export_dir
    export_dir = get_export_dir()
    export_dir.mkdir(parents=True, exist_ok=True)

    file = request.files.get('photo')
    if not file or not file.filename:
        return jsonify({'ok': False, 'error': 'No photo selected'})

    caption = request.form.get('caption', '').strip()
    ext = os.path.splitext(file.filename)[1] or '.jpg'
    dest_name = f"{uuid.uuid4().hex}{ext}"
    dest_path = str(export_dir / dest_name)
    file.save(dest_path)

    scheduled_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute(
        "INSERT INTO posts (scheduled_time, status, caption, photo_path) VALUES (?, 'posting', ?, ?)",
        (scheduled_time, caption, dest_path)
    )
    conn.commit()
    post_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    page_id = get_setting(conn, 'page_id')
    token = get_setting(conn, 'page_access_token')
    if not page_id or not token:
        update_post_status(conn, post_id, status='failed', last_error='Facebook not connected')
        return jsonify({'ok': False, 'error': 'Facebook not connected — go to Settings to connect.'})

    try:
        client = FacebookClient(page_id, token)
        fb_id, ts = client.post_photo_now(dest_path, caption)
        update_post_status(conn, post_id, status='posted',
                           facebook_post_id=fb_id,
                           posted_at=datetime.now().isoformat(),
                           last_error=None)
        log_event(conn, post_id, 'success', response_body=f'post_id={fb_id}',
                  description='Photo posted immediately via web app')
        if _fetch_fb_thumbnail(conn, client, post_id, fb_id):
            _purge_local_photo(conn, post_id, dest_path)
        return jsonify({'ok': True, 'post_id': post_id, 'fb_id': fb_id})
    except Exception as e:
        update_post_status(conn, post_id, status='failed',
                           attempt_count=1, last_error=str(e))
        log_event(conn, post_id, 'failure', error_message=str(e),
                  description='Immediate post failed')
        return jsonify({'ok': False, 'error': str(e)})


def _next_schedule_slot(conn, offset=0):
    import json as _json
    from datetime import date as dt_date

    row = conn.execute("SELECT * FROM schedule_patterns WHERE is_active=1 LIMIT 1").fetchone()
    if not row:
        slot = datetime.now() + timedelta(days=1+offset)
        return slot.strftime('%Y-%m-%d 08:00:00')

    try:
        days_of_week = _json.loads(row['days_of_week'] or '[]')
        times_raw = _json.loads(row['times_of_day'] or '[]')
        if isinstance(times_raw, dict):
            times_by_dow = {k: sorted(v) for k, v in times_raw.items()}
        else:
            flat = sorted(times_raw) if times_raw else ['08:00']
            times_by_dow = None
    except Exception:
        slot = datetime.now() + timedelta(days=1+offset)
        return slot.strftime('%Y-%m-%d 08:00:00')

    day_map = {'MON': 0, 'TUE': 1, 'WED': 2, 'THU': 3, 'FRI': 4, 'SAT': 5, 'SUN': 6}
    day_map_rev = {0: 'MON', 1: 'TUE', 2: 'WED', 3: 'THU', 4: 'FRI', 5: 'SAT', 6: 'SUN'}
    active_days = sorted([day_map[d] for d in days_of_week if d in day_map])
    if not active_days:
        active_days = list(range(7))

    existing = conn.execute(
        "SELECT scheduled_time FROM posts WHERE status IN ('scheduled','fb_scheduled','posting') ORDER BY scheduled_time"
    ).fetchall()
    taken = set()
    for r in existing:
        taken.add((r[0] or '')[:16])

    now = datetime.now()
    slots_found = 0
    for day_offset in range(365):
        candidate_date = now + timedelta(days=day_offset)
        if candidate_date.weekday() not in active_days:
            continue
        dow_str = day_map_rev[candidate_date.weekday()]
        if times_by_dow is not None:
            times = times_by_dow.get(dow_str, [])
        else:
            times = flat
        for t in times:
            candidate = candidate_date.strftime(f'%Y-%m-%d {t}:00')
            candidate_key = candidate[:16]
            if day_offset == 0 and candidate <= now.strftime('%Y-%m-%d %H:%M:%S'):
                continue
            if candidate_key not in taken:
                if slots_found == offset:
                    return candidate
                slots_found += 1
                taken.add(candidate_key)

    fallback = now + timedelta(days=offset+1)
    return fallback.strftime('%Y-%m-%d 08:00:00')


@app.route('/upload-batch', methods=['POST'])
def upload_batch():
    from core.db import get_export_dir
    export_dir = get_export_dir()
    export_dir.mkdir(parents=True, exist_ok=True)

    files = request.files.getlist('photos')
    captions = request.form.getlist('captions')
    if not files:
        return jsonify({'ok': False, 'error': 'No photos selected'})

    created_ids = []
    for i, file in enumerate(files):
        if not file.filename:
            continue
        ext = os.path.splitext(file.filename)[1] or '.jpg'
        dest_name = f"{uuid.uuid4().hex}{ext}"
        dest_path = str(export_dir / dest_name)
        file.save(dest_path)

        caption = captions[i].strip() if i < len(captions) else ''
        scheduled_time = _next_schedule_slot(conn, 0)

        conn.execute(
            "INSERT INTO posts (scheduled_time, status, caption, photo_path) VALUES (?, 'scheduled', ?, ?)",
            (scheduled_time, caption, dest_path)
        )
        conn.commit()
        post_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        log_event(conn, post_id, 'attempt', description='Photo uploaded via web app')
        created_ids.append(post_id)

    return jsonify({'ok': True, 'post_ids': created_ids, 'count': len(created_ids)})


@app.route('/delete/<int:post_id>', methods=['GET','POST'])
def delete_post_route(post_id):
    row = conn.execute("SELECT photo_path FROM posts WHERE id=?", (post_id,)).fetchone()
    _cancel_fb_post(post_id)
    try:
        log_event(conn, post_id, 'deleted', description='Post deleted from queue')
    except Exception:
        pass
    photo_path = (row['photo_path'] if row else '') or ''
    if photo_path and os.path.exists(photo_path):
        try:
            os.remove(photo_path)
        except Exception:
            pass
    delete_post(conn, post_id)
    if request.headers.get('X-Requested-With') == 'fetch':
        return jsonify(ok=True)
    return redirect('/')


@app.route('/delete-multiple', methods=['POST'])
def delete_multiple():
    data = request.get_json(silent=True) or {}
    ids = data.get('ids', [])
    if not ids or not isinstance(ids, list):
        return jsonify(ok=False, error='No posts specified')
    deleted = 0
    for post_id in ids:
        try:
            post_id = int(post_id)
            row = conn.execute("SELECT photo_path FROM posts WHERE id=?", (post_id,)).fetchone()
            if not row:
                continue
            _cancel_fb_post(post_id)
            try:
                log_event(conn, post_id, 'deleted', description='Post deleted (bulk delete)')
            except Exception:
                pass
            photo_path = (row['photo_path'] if row else '') or ''
            if photo_path and os.path.exists(photo_path):
                try:
                    os.remove(photo_path)
                except Exception:
                    pass
            delete_post(conn, post_id)
            deleted += 1
        except Exception:
            continue
    return jsonify(ok=True, deleted=deleted)


@app.route('/retry-failed')
def retry_failed():
    conn.execute("UPDATE posts SET status='scheduled', attempt_count=0, last_error=NULL, next_attempt_at=NULL WHERE status='failed'")
    conn.commit()
    return redirect('/')


@app.route('/post-now/<int:post_id>')
def post_now(post_id):
    page_id = get_setting(conn, 'page_id')
    token   = get_setting(conn, 'page_access_token')
    is_fetch = request.headers.get('X-Requested-With') == 'fetch'
    if not page_id or not token:
        if is_fetch:
            return jsonify(ok=False, error='Facebook not configured')
        return redirect('/settings')

    row = conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if not row:
        if is_fetch:
            return jsonify(ok=False, error='Post not found')
        return redirect('/')

    old_time = dict(row)['scheduled_time']
    client = FacebookClient(page_id, token)
    post   = dict(row)

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    update_post_status(conn, post_id, status='posting',
                       last_attempt_at=datetime.now().isoformat())
    conn.execute("UPDATE posts SET scheduled_time=? WHERE id=?", (now_str, post_id))
    conn.commit()
    log_event(conn, post_id, 'attempt', description='Post Now clicked — uploading to Facebook')

    # Force immediate posting regardless of scheduled time
    try:
        fb_id, ts = client.post_photo_now(post['photo_path'], post['caption'] or '')
        success, error = True, ''
    except Exception as e:
        fb_id, success, error = '', False, str(e)
    # Note: post_photo_now always publishes live, not to planner

    if success:
        update_post_status(conn, post_id, status='posted',
                           facebook_post_id=fb_id,
                           posted_at=datetime.now().isoformat(),
                           last_error=None)
        log_event(conn, post_id, 'success', response_body=f'post_id={fb_id}',
                  description='Post uploaded to Facebook successfully')
        notify('Ibis Publisher — Posted ✓', (post.get('caption') or '')[:60])
        if _fetch_fb_thumbnail(conn, client, post_id, fb_id):
            _purge_local_photo(conn, post_id, post.get('photo_path', ''))
    else:
        update_post_status(conn, post_id, status='failed',
                           attempt_count=1, last_error=error)
        log_event(conn, post_id, 'failure', error_message=error,
                  description='Upload failed')

    if is_fetch:
        return jsonify(ok=success, posted_at=now_str, old_time=old_time,
                       error=error if not success else None)
    return redirect('/')


@app.route('/retry/<int:post_id>', methods=['POST'])
def retry_post(post_id):
    """Re-queue a failed post so the scheduler picks it up again."""
    row = conn.execute("SELECT id FROM posts WHERE id=? AND status='failed'", (post_id,)).fetchone()
    if not row:
        return jsonify(ok=False, error='Post not found or not in failed state')
    conn.execute(
        "UPDATE posts SET status='scheduled', last_error=NULL, attempt_count=0 WHERE id=?",
        (post_id,)
    )
    conn.commit()
    log_event(conn, post_id, 'rescheduled', description='Manually re-queued after failed/interrupted upload')
    return jsonify(ok=True)


@app.route('/mark-posted/<int:post_id>', methods=['POST'])
def mark_posted(post_id):
    """Mark an interrupted post as already posted — avoids re-uploading a duplicate."""
    row = conn.execute("SELECT id FROM posts WHERE id=? AND status='failed'", (post_id,)).fetchone()
    if not row:
        return jsonify(ok=False, error='Post not found or not in failed state')
    conn.execute(
        "UPDATE posts SET status='posted', last_error=NULL, posted_at=? WHERE id=?",
        (datetime.now().isoformat(), post_id)
    )
    conn.commit()
    log_event(conn, post_id, 'success', description='Manually marked as posted — upload had already completed')
    return jsonify(ok=True)


# ── Calendar ─────────────────────────────────────────────────────

CAL_CSS = """
.cal-nav { display:flex; align-items:center; gap:0; margin-bottom:20px; }
.cal-nav a { display:flex; align-items:center; gap:6px; padding:8px 14px;
             background:white; border:1px solid #D6D0CA; color:#948466; text-decoration:none;
             font-size:13px; font-weight:500; }
.cal-nav a:first-of-type { border-radius:8px 0 0 8px; }
.cal-nav a:nth-of-type(2) { border-radius:0 8px 8px 0; }
.cal-nav a:hover { background:#EDE9E5; color:#343434; }
.cal-nav .cal-title { flex:1; text-align:center; font-size:15px; font-weight:600;
                      color:#343434; padding:8px 0; }
.cal-grid-wrap { overflow:hidden; position:relative; }
.edge-indicator { position:absolute; top:0; bottom:0; width:60px; z-index:100;
                  display:flex; flex-direction:column; align-items:center; justify-content:center;
                  gap:6px; opacity:0; pointer-events:none;
                  transition:opacity .3s ease; }
.edge-indicator.visible { opacity:1; }
.edge-indicator-left { left:0;
    background:linear-gradient(to right, rgba(116,154,150,.25), transparent); }
.edge-indicator-right { right:0;
    background:linear-gradient(to left, rgba(116,154,150,.25), transparent); }
.edge-indicator svg { width:28px; height:28px; color:#749A96;
    animation:edgePulse 1.2s ease-in-out infinite; }
.edge-indicator span { font-size:10px; font-weight:600; color:#749A96;
    text-transform:uppercase; letter-spacing:.06em; writing-mode:vertical-lr;
    text-orientation:mixed; }
@keyframes edgePulse {
    0%,100% { opacity:.4; transform:scale(.9); }
    50% { opacity:1; transform:scale(1.1); }
}
.cal-dow-row { display:grid; grid-template-columns:repeat(7,1fr); gap:8px;
               min-width:840px; margin-bottom:4px; }
.cal-dow-cell { text-align:center; font-size:11px; font-weight:700; text-transform:uppercase;
                letter-spacing:.07em; color:#948466; padding:6px 0; }
.cal-grid { display:grid; grid-template-columns:repeat(7,1fr); gap:8px;
            min-width:840px; }
.day-col { background:#F8F6F4; border-radius:10px; border:none;
           box-shadow:0 1px 3px rgba(0,0,0,.06);
           min-height:160px; display:flex; flex-direction:column; }
.day-col.today { box-shadow:0 2px 10px rgba(0,0,0,.13); }
.day-col.drop-over { background:#EDE9E5; box-shadow:0 4px 16px rgba(0,0,0,.18); }
.day-hdr { padding:8px 10px; text-align:center; border-bottom:1px solid #D6D0CA;
           background:#D9D3CC; border-radius:10px 10px 0 0; }
.day-col.today .day-hdr { background:#343434; color:white; border-radius:10px 10px 0 0; }
.day-hdr .dom { font-size:20px; font-weight:600; line-height:1.2; color:inherit; }
.day-posts { padding:6px; display:flex; flex-direction:column; gap:6px; flex:1; }
.day-empty { flex:1; display:flex; align-items:center; justify-content:center;
             color:#D6D0CA; font-size:12px; padding:16px; }
.post-card { border:none; border-radius:8px; overflow:hidden;
             cursor:grab; user-select:none; background:white;
             box-shadow:0 1px 3px rgba(0,0,0,.08);
             transition:box-shadow .1s, opacity .1s; }
.post-card:active { cursor:grabbing; }
.post-card.dragging { opacity:.35; box-shadow:none; }
.post-card.posted-card { cursor:default; }
.post-card img { width:100%; height:80px; object-fit:cover; display:block; pointer-events:none; }
.post-card .card-no-img { width:100%; height:80px; background:#EDE9E5;
                           display:flex; align-items:center; justify-content:center;
                           font-size:11px; color:#948466; }
.post-card .card-body { padding:6px 8px; }
.post-card .card-time { font-size:11px; font-weight:700; color:#749A96; margin-bottom:2px; }
.post-card .card-cap  { font-size:11px; color:#948466; line-height:1.4;
                         display:-webkit-box; -webkit-line-clamp:2;
                         -webkit-box-orient:vertical; overflow:hidden; }
.post-card .card-cap.no-cap { color:#D6D0CA; font-style:italic; }
.empty-slot { border:1.5px dashed #D6D0CA; border-radius:8px; padding:14px 8px;
              display:flex; align-items:center; justify-content:center; gap:6px; }
.empty-slot-upload { cursor:pointer; transition:all .15s; }
.empty-slot-upload:hover { border-color:#749A96; background:rgba(116,154,150,.06); }
.empty-slot-time { font-size:11px; font-weight:600; color:#C8C2BC; letter-spacing:.03em; }
.empty-slot-upload:hover .empty-slot-time { color:#749A96; }
.empty-slot-icon { width:18px; height:18px; border-radius:4px; border:1.5px dashed #D6D0CA;
  display:flex; align-items:center; justify-content:center; font-size:12px; color:#C8C2BC;
  flex-shrink:0; transition:all .15s; }
.empty-slot-upload:hover .empty-slot-icon { border-color:#749A96; color:#749A96; background:#d3e4e2; }
.batch-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,.5);
  z-index:600; align-items:center; justify-content:center; }
.batch-overlay.open { display:flex; }
.batch-box { background:white; border-radius:14px; width:440px; max-width:95vw;
  box-shadow:0 8px 40px rgba(0,0,0,.3); }
.batch-hdr { display:flex; align-items:center; justify-content:space-between;
  padding:16px 20px 12px; border-bottom:1px solid #eee; }
.batch-hdr h3 { font-size:15px; font-weight:600; margin:0; }
.batch-body { padding:20px; }
.batch-dropzone { border:2px dashed #ddd; border-radius:10px; padding:32px 20px;
  text-align:center; color:#999; font-size:13px; cursor:pointer; transition:all .15s; display:block; }
.batch-dropzone:hover, .batch-dropzone.drag-over { border-color:#749A96; color:#749A96; background:#f0f5f4; }
.batch-file-list { margin-top:12px; font-size:12px; color:#555; max-height:120px; overflow-y:auto; }
.batch-footer { display:flex; gap:8px; padding:16px 20px; border-top:1px solid #eee; justify-content:flex-end; }
.post-card .card-foot { display:flex; align-items:center; justify-content:space-between;
                         padding:4px 8px 6px; }
"""

@app.route('/api/next-slots')
def api_next_slots():
    import json as _json
    from datetime import date as dt_date, timedelta as td
    count = int(request.args.get('count', 1))
    count = max(1, min(count, 200))

    sched = conn.execute("SELECT * FROM schedule_patterns WHERE is_active=1 LIMIT 1").fetchone()
    if not sched:
        return jsonify({'ok': False, 'error': 'No active schedule'})

    DOW_MAP = {0: 'MON', 1: 'TUE', 2: 'WED', 3: 'THU', 4: 'FRI', 5: 'SAT', 6: 'SUN'}
    try:
        raw = _json.loads(sched['times_of_day'] or '{}')
    except Exception:
        return jsonify({'ok': False, 'error': 'Bad schedule data'})

    def slots_for_day(dk):
        if sched['pattern_type'] == 'custom' and isinstance(raw, dict):
            return sorted(raw.get(dk, []))
        elif sched['pattern_type'] in ('weekly', 'daily'):
            active = _json.loads(sched['days_of_week'] or '[]')
            if dk in active:
                return sorted(raw) if isinstance(raw, list) else []
        return []

    filled = set()
    rows = conn.execute(
        "SELECT scheduled_time FROM posts WHERE status NOT IN ('failed','missed')"
    ).fetchall()
    for r in rows:
        st = (r['scheduled_time'] or '')[:16]
        if st:
            filled.add(st)

    now = datetime.now()
    result = []
    d = dt_date.today()
    max_days = count * 30
    for _ in range(max_days):
        dk = DOW_MAP[d.weekday()]
        for t in slots_for_day(dk):
            slot_dt = f"{d.isoformat()} {t}:00"
            slot_key = slot_dt[:16]
            slot_datetime = datetime.strptime(slot_dt, '%Y-%m-%d %H:%M:%S')
            if slot_datetime > now and slot_key not in filled:
                result.append(slot_dt)
                if len(result) >= count:
                    break
        if len(result) >= count:
            break
        d += td(days=1)

    return jsonify({'ok': True, 'slots': result})


@app.route('/calendar')
def calendar():
    week = request.args.get('week', '0')
    return redirect(f'/?week={week}')



# ── Timezone debug ───────────────────────────────────────────────

@app.route('/tz-debug')
def tz_debug():
    import time, os, subprocess
    from datetime import datetime, timezone
    lines = []

    # 1. Process-level TZ
    lines.append(f"os.environ TZ: {os.environ.get('TZ', '(not set)')}")
    lines.append(f"time.tzname: {time.tzname}")
    lines.append(f"time.timezone (UTC offset secs, std): {time.timezone}  = UTC{-time.timezone/3600:+.1f}h")
    lines.append(f"time.daylight: {time.daylight}")
    if time.daylight:
        lines.append(f"time.altzone (UTC offset secs, DST): {time.altzone}  = UTC{-time.altzone/3600:+.1f}h")

    # 2. /etc/localtime symlink
    try:
        real = os.path.realpath('/etc/localtime')
        lines.append(f"/etc/localtime → {real}")
        marker = 'zoneinfo/'
        idx = real.find(marker)
        tz_name = real[idx+len(marker):] if idx >= 0 else '(unknown)'
        lines.append(f"Derived TZ name: {tz_name}")
    except Exception as e:
        lines.append(f"/etc/localtime read error: {e}")

    # 3. Test conversion for a fixed reference time using both methods
    test_dt = datetime(2025, 6, 21, 17, 0)   # Saturday June 21 5PM — what user reported
    mktime_ts = int(time.mktime(test_dt.timetuple()))
    lines.append(f"\nTest: convert 2025-06-21 17:00 local")
    lines.append(f"  time.mktime → {mktime_ts} = UTC {datetime.utcfromtimestamp(mktime_ts).strftime('%H:%M')}")

    try:
        from zoneinfo import ZoneInfo
        real = os.path.realpath('/etc/localtime')
        idx = real.find('zoneinfo/')
        tz = ZoneInfo(real[idx+9:]) if idx >= 0 else None
        if tz:
            zi_ts = int(test_dt.replace(tzinfo=tz).timestamp())
            lines.append(f"  zoneinfo ({real[idx+9:]}) → {zi_ts} = UTC {datetime.utcfromtimestamp(zi_ts).strftime('%H:%M')}")
    except Exception as e:
        lines.append(f"  zoneinfo error: {e}")

    # 4. macOS date command
    try:
        out = subprocess.check_output(
            ['date', '-jf', '%Y-%m-%d %H:%M', '2025-06-21 17:00', '+%s'],
            stderr=subprocess.DEVNULL, text=True).strip()
        mac_ts = int(out)
        lines.append(f"  macOS date cmd → {mac_ts} = UTC {datetime.utcfromtimestamp(mac_ts).strftime('%H:%M')}")
    except Exception as e:
        lines.append(f"  macOS date cmd error: {e}")

    # 5. Current datetime.now() info
    now = datetime.now()
    now_utc = datetime.now(timezone.utc)
    lines.append(f"\ndatetime.now() = {now}")
    lines.append(f"datetime.now(utc) = {now_utc}")
    offset_secs = time.timezone - (time.daylight * 3600 if time.daylight and time.localtime().tm_isdst else 0)
    lines.append(f"\nCurrent UTC offset: {-offset_secs/3600:+.1f}h")

    # 6. Facebook page timezone
    lines.append("\n── Facebook page timezone ──")
    try:
        page_id = get_setting(conn, 'page_id')
        token   = get_setting(conn, 'page_access_token')
        if page_id and token:
            import requests as _req
            r = _req.get(
                f"https://graph.facebook.com/v25.0/{page_id}",
                params={'fields': 'timezone,offset_time_zone,name', 'access_token': token},
                timeout=10
            )
            data = r.json()
            lines.append(f"  Raw response: {data}")
            lines.append(f"  Page name: {data.get('name', '?')}")
            fb_tz_offset = data.get('timezone') or data.get('offset_time_zone')
            lines.append(f"  Page timezone offset (from FB API): {fb_tz_offset if fb_tz_offset is not None else 'not returned'}")
            if fb_tz_offset is not None:
                our_offset = offset_secs / 3600
                diff = our_offset - float(fb_tz_offset)
                if abs(diff) < 0.01:
                    lines.append(f"  ✓ Mac timezone matches Facebook page timezone")
                else:
                    lines.append(f"  ✗ MISMATCH: Mac is UTC{our_offset:+.1f}h, Facebook page is UTC{float(fb_tz_offset):+.1f}h")
                    lines.append(f"    This {abs(diff):.0f}-hour difference causes times to appear wrong on Facebook.")
                    lines.append(f"    Fix: go to your Facebook Page settings → General → Page timezone")
                    lines.append(f"    and set it to match your Mac (UTC{our_offset:+.1f}h).")
        else:
            lines.append("  (no page credentials configured)")
    except Exception as e:
        lines.append(f"  Error fetching page info: {e}")

    return '<pre style="font-family:monospace;padding:20px;font-size:13px">' + '\n'.join(lines) + '</pre>'


# ── Log ──────────────────────────────────────────────────────────

@app.route('/log')
def log():
    logs = get_log(conn, limit=300)
    rows = ''
    if not logs:
        rows = '<tr><td colspan="5"><div class="empty">No activity yet.</div></td></tr>'
    else:
        fallback_desc = {
            'success':     'Post uploaded to Facebook successfully',
            'attempt':     'Upload attempt started',
            'failure':     'Upload failed',
            'retry':       'Upload failed — retrying later',
            'rescheduled': 'Post rescheduled',
            'deleted':     'Post deleted from queue',
        }
        for e in logs:
            et = e['event_type'] or ''
            badge_map = {'success':'posted','failure':'not-uploaded',
                         'attempt':'uploading','retry':'not-uploaded',
                         'rescheduled':'scheduled','deleted':'not-uploaded'}
            bc = badge_map.get(et, 'scheduled')
            pid = e['post_id']
            post_row = conn.execute("SELECT photo_path, fb_thumbnail_url FROM posts WHERE id=?", (pid,)).fetchone()
            photo_path = (post_row['photo_path'] if post_row else '') or ''
            fb_thumb = (post_row['fb_thumbnail_url'] if post_row else '') or ''
            if (photo_path and os.path.exists(photo_path)) or fb_thumb:
                thumb = f'<img src="/thumb/{pid}" style="width:48px;height:48px;object-fit:cover;border-radius:6px;border:1px solid #D6D0CA;">'
            else:
                thumb = '<div style="width:48px;height:48px;background:#EDE9E5;border-radius:6px;"></div>'
            desc = e['description'] or fallback_desc.get(et, et)
            error = (e['error_message'] or '').strip()
            caption_preview = (e['caption'] or '')[:60]
            logged_at = (e['logged_at'] or '')[:16]

            sub_lines = []
            if caption_preview:
                sub_lines.append(f'<span style="color:#948466">{caption_preview}</span>')
            if error:
                sub_lines.append(f'<span style="color:#B56152;font-size:11px">{error[:120]}</span>')
            sub_html = ('<br>' + ' &nbsp;·&nbsp; '.join(sub_lines)) if sub_lines else ''

            rows += f'''<tr style="border-bottom:1px solid #E5E0DA">
              <td style="padding:10px 12px;width:60px;vertical-align:middle">{thumb}</td>
              <td style="padding:10px 12px;white-space:nowrap;color:#948466;font-size:12px;vertical-align:middle">{logged_at}</td>
              <td style="padding:10px 12px;vertical-align:middle">
                <span class="badge badge-{bc}" style="white-space:nowrap">{desc}</span>{sub_html}
              </td>
            </tr>'''

    content = f'''<h2>Activity Log</h2>
    <div class="card card-table">
      <table style="width:100%;border-collapse:collapse">
        <thead><tr>
          <th style="padding:10px 14px;background:#EDE9E5;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:#948466;border-bottom:1px solid #D6D0CA;width:60px">Photo</th>
          <th style="padding:10px 14px;background:#EDE9E5;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:#948466;border-bottom:1px solid #D6D0CA;white-space:nowrap">Time</th>
          <th style="padding:10px 14px;background:#EDE9E5;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:#948466;border-bottom:1px solid #D6D0CA">What happened</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>'''
    return layout('log', content)


# ── Settings ─────────────────────────────────────────────────────

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    alert = ''
    if request.method == 'POST':
        section = request.form.get('section')
        if section == 'connection':
            token     = request.form.get('page_access_token', '').strip()
            page_id   = request.form.get('page_id', '').strip()
            page_name = request.form.get('page_name', '').strip() or 'My Page'
            if token and page_id:
                try:
                    client = FacebookClient(page_id, token)
                    if client.verify_token():
                        set_setting(conn, 'page_access_token', token)
                        set_setting(conn, 'page_id', page_id)
                        set_setting(conn, 'page_name', page_name)
                        alert = f'<div class="alert alert-success">✅ Connected to <strong>{page_name}</strong>!</div>'
                    else:
                        alert = '<div class="alert alert-error">❌ Could not verify token.</div>'
                except Exception as e:
                    alert = f'<div class="alert alert-error">❌ Error: {e}</div>'
            else:
                alert = '<div class="alert alert-error">Please fill in both fields.</div>'
        elif section == 'notifications':
            set_setting(conn, 'notify_on_success', '1' if request.form.get('notify_success') else '0')
            set_setting(conn, 'notify_on_failure', '1' if request.form.get('notify_failure') else '0')
            alert = '<div class="alert alert-success">Notification settings saved.</div>'
        elif section == 'test':
            page_id = get_setting(conn, 'page_id')
            token   = get_setting(conn, 'page_access_token')
            try:
                client = FacebookClient(page_id, token)
                alert = '<div class="alert alert-success">✅ Connection is working!</div>' if client.verify_token()                         else '<div class="alert alert-error">❌ Token verification failed.</div>'
            except Exception as e:
                alert = f'<div class="alert alert-error">❌ {e}</div>'

    page_id   = get_setting(conn, 'page_id')
    page_name = get_setting(conn, 'page_name')
    n_ok  = 'checked' if get_setting(conn, 'notify_on_success', '1') == '1' else ''
    n_err = 'checked' if get_setting(conn, 'notify_on_failure',  '1') == '1' else ''

    conn_status = f'<div class="alert alert-success">✅ Connected to <strong>{page_name}</strong> (Page ID: {page_id})</div>' \
                  if page_id and page_name else \
                  '<div class="alert alert-error">❌ Not connected to Facebook yet.</div>'

    from core.db import get_export_dir, get_thumbnail_dir
    photo_dir = str(get_export_dir())
    photo_count = len([f for f in os.listdir(photo_dir) if not f.startswith('.')]) if os.path.isdir(photo_dir) else 0
    thumb_dir = str(get_thumbnail_dir())
    thumb_count = len([f for f in os.listdir(thumb_dir) if not f.startswith('.')]) if os.path.isdir(thumb_dir) else 0

    content = f'''
    <h2>Settings</h2>
    {alert}
    <div class="card">
      <h3>📂 Photo Storage</h3>
      <p style="font-size:13px;color:#666;margin-bottom:8px;">
        Uploaded photos are stored locally until posted to Facebook, then replaced with Facebook-hosted thumbnails.
      </p>
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">
        <code style="font-size:12px;background:#f5f2ee;padding:6px 10px;border-radius:6px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{photo_dir}</code>
        <a href="/open-photo-folder" class="btn btn-secondary" style="font-size:12px;white-space:nowrap;">Open in Finder</a>
      </div>
      <p style="font-size:12px;color:#999;margin:0;">{photo_count} file{"s" if photo_count != 1 else ""} currently stored</p>
    </div>
    <div class="card">
      <h3>🖼 Temporary Thumbnail Storage</h3>
      <p style="font-size:13px;color:#666;margin-bottom:8px;">
        After a photo is posted to Facebook, a small thumbnail is saved locally so it can be displayed in the queue.
        Thumbnails are automatically purged 90 days after the post goes live.
      </p>
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">
        <code style="font-size:12px;background:#f5f2ee;padding:6px 10px;border-radius:6px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{thumb_dir}</code>
        <a href="/open-thumbnail-folder" class="btn btn-secondary" style="font-size:12px;white-space:nowrap;">Open in Finder</a>
      </div>
      <p style="font-size:12px;color:#999;margin:0;">{thumb_count} thumbnail{"s" if thumb_count != 1 else ""} currently stored</p>
    </div>
    <div class="card">
      <h3>🔗 Facebook Connection</h3>
      {conn_status}
      <form method="POST">
        <input type="hidden" name="section" value="connection">
        <label>Page Access Token</label>
        <input type="password" name="page_access_token" placeholder="Paste your Page Access Token here">
        <label>Facebook Page ID</label>
        <input type="text" name="page_id" value="{page_id}">
        <label>Page Name</label>
        <input type="text" name="page_name" value="{page_name}">
        <div style="margin-top:16px;display:flex;gap:8px;">
          <button type="submit" class="btn btn-primary">Save & Connect</button>
        </div>
      </form>
      <form method="POST" style="margin-top:10px;">
        <input type="hidden" name="section" value="test">
        <button type="submit" class="btn btn-secondary">Test Connection</button>
      </form>
    </div>
    <div class="card">
      <h3>🔔 Notifications</h3>
      <form method="POST">
        <input type="hidden" name="section" value="notifications">
        <label><input type="checkbox" name="notify_success" {n_ok}> Notify on successful post</label>
        <label><input type="checkbox" name="notify_failure" {n_err}> Notify on failed post</label>
        <button type="submit" class="btn btn-primary" style="margin-top:14px;">Save</button>
      </form>
    </div>
    <div class="card">
      <h3>ℹ️ About</h3>
      <p style="font-size:13px;color:#666;margin-bottom:10px;">Ibis Publisher v1.18 · Running at http://localhost:8765</p>
      <div class="alert alert-info" style="font-size:13px;">
        <strong>How scheduling works:</strong><br>
        Ibis Publisher uploads photos to Facebook immediately and tells Facebook when to publish them.
        <strong>Your computer does not need to be on at the scheduled time.</strong>
        Posts more than 29 days away are held locally and uploaded automatically when they enter the 29-day window.
      </div>
    </div>'''
    return layout('settings', content)


@app.route('/open-photo-folder')
def open_photo_folder():
    import subprocess
    from core.db import get_export_dir
    folder = str(get_export_dir())
    subprocess.Popen(['open', folder])
    return redirect('/settings')


@app.route('/open-thumbnail-folder')
def open_thumbnail_folder():
    import subprocess
    from core.db import get_thumbnail_dir
    folder = str(get_thumbnail_dir())
    os.makedirs(folder, exist_ok=True)
    subprocess.Popen(['open', folder])
    return redirect('/settings')


@app.route('/schedule', methods=['GET', 'POST'])
def schedule():
    import json as _json
    DAY_KEYS = ['MON','TUE','WED','THU','FRI','SAT','SUN']
    DAY_LABELS = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        name = data.get('name', '').strip() or 'Custom Schedule'
        days_dict = data.get('days', {})
        if days_dict:
            active_days = [dk for dk in DAY_KEYS if dk in days_dict]
            conn.execute("UPDATE schedule_patterns SET is_active=0")
            existing = conn.execute("SELECT id FROM schedule_patterns WHERE name=?", (name,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE schedule_patterns SET pattern_type=?,days_of_week=?,times_of_day=?,is_active=1 WHERE id=?",
                    ('custom', _json.dumps(active_days), _json.dumps(days_dict), existing['id']))
            else:
                conn.execute(
                    "INSERT INTO schedule_patterns (name,pattern_type,days_of_week,times_of_day,is_active) VALUES (?,?,?,?,1)",
                    (name, 'custom', _json.dumps(active_days), _json.dumps(days_dict)))
            conn.commit()
            return jsonify({'ok': True})
        return jsonify({'ok': False, 'error': 'Add at least one time slot.'})

    row = conn.execute("SELECT * FROM schedule_patterns WHERE is_active=1 LIMIT 1").fetchone()
    times_by_day = {dk: [] for dk in DAY_KEYS}
    pattern_name = 'My Schedule'

    if row:
        pattern_name = row['name'] or 'My Schedule'
        try:
            raw = _json.loads(row['times_of_day'] or '{}')
            if row['pattern_type'] == 'custom' and isinstance(raw, dict):
                for dk, tv in raw.items():
                    if dk in times_by_day:
                        times_by_day[dk] = sorted(tv) if isinstance(tv, list) else []
            elif row['pattern_type'] in ('weekly', 'daily'):
                flat = sorted(raw) if isinstance(raw, list) else []
                active = _json.loads(row['days_of_week'] or '[]')
                for dk in active:
                    if dk in times_by_day:
                        times_by_day[dk] = flat
        except Exception:
            pass

    rows_html = ''
    for dk, label in zip(DAY_KEYS, DAY_LABELS):
        chips = ''
        for t in times_by_day[dk]:
            chips += ('<div class="time-chip" data-time="' + t + '">'
                      '<span class="chip-time" onclick="editTimeSlot(this)">' + t + '</span>'
                      '<button class="chip-remove" onclick="removeChip(this)">&times;</button>'
                      '</div>')
        rows_html += (
            '<div class="schedule-row" data-day="' + dk + '">'
            '<span class="day-label">' + label + '</span>'
            '<div class="time-chips">' + chips + '</div>'
            '<button class="add-time-btn" onclick="addTimeSlot(this)" title="Add time">+</button>'
            '<button class="copy-to-btn" onclick="showCopyPop(this,\'' + dk + '\')" title="Copy times to other days">Copy &#8594;</button>'
            '</div>'
        )

    content = '''
    <h2>Posting Schedule</h2>
    <div id="sched-alert"></div>
    <div id="tpp" style="display:none;position:fixed;z-index:99999;background:white;border-radius:14px;box-shadow:0 10px 36px rgba(0,0,0,.16);padding:20px 22px">
      <div style="display:flex;align-items:flex-end;gap:6px;margin-bottom:18px">
        <div style="display:flex;flex-direction:column;align-items:center;gap:6px">
          <span class="tpp-lbl">Hour</span>
          <input type="number" id="tpp-h" class="tpp-num" min="0" max="23" step="1" placeholder="9">
          <span class="tpp-hint">24 hr</span>
        </div>
        <span style="font-size:28px;font-weight:300;color:#C8C2BC;padding-bottom:22px;line-height:1">:</span>
        <div style="display:flex;flex-direction:column;align-items:center;gap:6px">
          <span class="tpp-lbl">Min</span>
          <input type="number" id="tpp-m" class="tpp-num" min="0" max="59" step="5" placeholder="0">
          <span class="tpp-hint">&nbsp;</span>
        </div>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn btn-primary" style="flex:1;padding:8px 0;font-size:14px" onclick="tppConfirm()">Set</button>
        <button class="btn btn-secondary" style="padding:8px 14px;font-size:14px" onclick="tppCancel()">Cancel</button>
      </div>
    </div>
    <div id="copy-pop" style="display:none;position:fixed;z-index:99998;background:white;border-radius:12px;box-shadow:0 8px 28px rgba(0,0,0,.14);padding:16px 18px"></div>
    <div class="card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:24px;flex-wrap:wrap;gap:12px">
        <div>
          <h3 style="margin-bottom:4px">Custom Schedule</h3>
          <p style="font-size:13px;color:#948466;margin:0">Click <strong>+</strong> on any day to add a posting time.</p>
        </div>
        <div style="display:flex;align-items:center;gap:10px">
          <input type="text" id="schedule-name" value="''' + pattern_name + '''" placeholder="Schedule name"
            style="padding:8px 12px;border:1px solid #D9D3CC;border-radius:8px;font-size:14px;width:180px;color:#343434;background:white">
          <button class="btn btn-primary" onclick="saveSchedule()">Save &amp; Activate</button>
        </div>
      </div>
      <div class="schedule-grid">''' + rows_html + '''</div>
    </div>
    <style>
      .schedule-grid{display:flex;flex-direction:column}
      .schedule-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:13px 0;border-bottom:1px solid #F0EDEA}
      .schedule-row:last-child{border-bottom:none}
      .day-label{width:36px;font-size:12px;font-weight:700;color:#948466;flex-shrink:0;text-transform:uppercase;letter-spacing:.06em}
      .time-chips{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
      .time-chip{display:inline-flex;align-items:center;gap:4px;padding:5px 8px 5px 12px;background:white;border:1px solid #D9D3CC;border-radius:20px;font-size:13px;font-weight:500;color:#343434}
      .chip-time{cursor:pointer;transition:color .1s}
      .chip-time:hover{color:#749A96}
      .chip-remove{background:none;border:none;cursor:pointer;padding:0 2px;font-size:15px;color:#B56152;line-height:1;opacity:.55;transition:opacity .1s}
      .chip-remove:hover{opacity:1}
      .add-time-btn{width:28px;height:28px;border:1.5px dashed #C8C2BC;border-radius:50%;background:none;cursor:pointer;font-size:17px;color:#948466;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;transition:all .15s;padding:0;line-height:1}
      .add-time-btn:hover{background:#F0EDEA;border-color:#749A96;color:#749A96}
      .tpp-lbl{font-size:10px;text-transform:uppercase;letter-spacing:.1em;color:#948466;font-weight:700}
      .tpp-hint{font-size:10px;color:#C8C2BC;letter-spacing:.04em}
      .tpp-num{border:1.5px solid #D9D3CC;border-radius:10px;padding:10px 0;font-size:24px;font-weight:500;color:#343434;background:white;text-align:center;width:72px;outline:none;transition:border-color .15s;-moz-appearance:textfield}
      .tpp-num::-webkit-inner-spin-button,.tpp-num::-webkit-outer-spin-button{opacity:.5;height:28px}
      .tpp-num:focus{border-color:#749A96}
      .copy-to-btn{padding:0 10px;height:26px;border:1px solid #D9D3CC;border-radius:13px;background:none;cursor:pointer;font-size:11px;font-weight:600;color:#948466;letter-spacing:.03em;transition:all .15s;white-space:nowrap;display:inline-flex;align-items:center}
      .copy-to-btn:hover{background:#F0EDEA;border-color:#749A96;color:#749A96}
      .copy-day-pill{padding:5px 11px;border:1.5px solid #D9D3CC;border-radius:20px;background:white;cursor:pointer;font-size:12px;font-weight:700;color:#343434;transition:all .15s}
      .copy-day-pill:hover{border-color:#749A96;color:#749A96;background:#F8F6F4}
      .copy-day-pill.copied{background:#749A96;border-color:#749A96;color:white}
    </style>
    <script>
    // ── Dismiss popovers on outside click ───────────────────────────
    document.addEventListener('mousedown', function(e) {
      var tpp = document.getElementById('tpp');
      if (tpp.style.display !== 'none' && !tpp.contains(e.target)) tppCancel();
      var cp = document.getElementById('copy-pop');
      if (cp.style.display !== 'none' && !cp.contains(e.target)) cp.style.display = 'none';
    });

    function positionPop(id, nearEl) {
      var pop = document.getElementById(id);
      pop.style.display = 'block';
      var rect = nearEl.getBoundingClientRect();
      var pw = pop.offsetWidth, ph = pop.offsetHeight;
      var left = rect.left, top = rect.bottom + 8;
      if (left + pw > window.innerWidth - 16) left = window.innerWidth - pw - 16;
      if (top + ph > window.innerHeight - 16) top = rect.top - ph - 8;
      pop.style.left = left + 'px';
      pop.style.top  = top  + 'px';
    }

    var _tppCb = null;
    function openPicker(nearEl, currentVal, callback) {
      _tppCb = callback;
      var h = '', m = '';
      if (currentVal && currentVal.indexOf(':') >= 0) {
        var parts = currentVal.split(':');
        h = parseInt(parts[0], 10) || 0;
        m = parseInt(parts[1], 10) || 0;
      }
      var hEl = document.getElementById('tpp-h');
      var mEl = document.getElementById('tpp-m');
      hEl.value = h;
      mEl.value = m;
      positionPop('tpp', nearEl);
      setTimeout(function(){ hEl.focus(); hEl.select(); }, 0);
    }
    document.addEventListener('keydown', function(e) {
      if (document.getElementById('tpp').style.display === 'none') return;
      if (e.key === 'Enter') { e.preventDefault(); tppConfirm(); }
      if (e.key === 'Escape') { e.preventDefault(); tppCancel(); }
      if (e.key === 'Tab') {
        var hEl = document.getElementById('tpp-h');
        var mEl = document.getElementById('tpp-m');
        if (document.activeElement === hEl && !e.shiftKey) {
          e.preventDefault(); mEl.focus(); mEl.select();
        } else if (document.activeElement === mEl && e.shiftKey) {
          e.preventDefault(); hEl.focus(); hEl.select();
        } else { e.preventDefault(); }
      }
    });
    function tppConfirm() {
      var h = Math.max(0, Math.min(23, parseInt(document.getElementById('tpp-h').value, 10) || 0));
      var m = Math.max(0, Math.min(59, parseInt(document.getElementById('tpp-m').value, 10) || 0));
      var val = h.toString().padStart(2,'0') + ':' + m.toString().padStart(2,'0');
      document.getElementById('tpp').style.display = 'none';
      if (_tppCb) { _tppCb(val); _tppCb = null; }
    }
    function tppCancel() {
      document.getElementById('tpp').style.display = 'none';
      _tppCb = null;
    }

    // ── Chip actions ─────────────────────────────────────────────────
    function addTimeSlot(btn) {
      var row = btn.closest('.schedule-row');
      openPicker(btn, '', function(val) {
        var chips = row.querySelector('.time-chips');
        var dup = false;
        chips.querySelectorAll('.time-chip').forEach(function(c){ if (c.dataset.time === val) dup = true; });
        if (dup) return;
        var chip = document.createElement('div');
        chip.className = 'time-chip';
        chip.dataset.time = val;
        chip.innerHTML = '<span class="chip-time" onclick="editTimeSlot(this)">' + val + '</span>'
          + '<button class="chip-remove" onclick="removeChip(this)">&times;</button>';
        chips.appendChild(chip);
        sortChips(chips);
      });
    }
    function editTimeSlot(span) {
      var chip = span.closest('.time-chip');
      openPicker(chip, chip.dataset.time, function(val) {
        var chips = chip.parentElement;
        var dup = false;
        chips.querySelectorAll('.time-chip').forEach(function(c){ if (c !== chip && c.dataset.time === val) dup = true; });
        if (dup) return;
        chip.dataset.time = val;
        span.textContent = val;
        sortChips(chips);
      });
    }
    function removeChip(btn) { btn.closest('.time-chip').remove(); }
    function sortChips(chips) {
      var els = Array.from(chips.querySelectorAll('.time-chip'));
      els.sort(function(a,b){ return a.dataset.time.localeCompare(b.dataset.time); });
      els.forEach(function(c){ chips.appendChild(c); });
    }

    // ── Copy to days ─────────────────────────────────────────────────
    var _copySource = null;
    var _DAY_KEYS = ['MON','TUE','WED','THU','FRI','SAT','SUN'];
    var _DAY_LBLS = {MON:'Mon',TUE:'Tue',WED:'Wed',THU:'Thu',FRI:'Fri',SAT:'Sat',SUN:'Sun'};
    function showCopyPop(btn, sourceDay) {
      _copySource = sourceDay;
      var srcRow = document.querySelector('.schedule-row[data-day="' + sourceDay + '"]');
      var times = Array.from(srcRow.querySelectorAll('.time-chip')).map(function(c){ return c.dataset.time; });
      var pills = _DAY_KEYS.filter(function(d){ return d !== sourceDay; }).map(function(d){
        return `<button class="copy-day-pill" onclick="doCopy('${d}',this)">${_DAY_LBLS[d]}</button>`;
      }).join('');
      var note = times.length ? '' : '<p style="font-size:12px;color:#B56152;margin:10px 0 0">No times set on this day yet.</p>';
      document.getElementById('copy-pop').innerHTML =
        '<p style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#948466;margin:0 0 10px">Copy to</p>'
        + '<div style="display:flex;gap:6px;flex-wrap:wrap">' + pills + '</div>' + note;
      positionPop('copy-pop', btn);
    }
    function doCopy(targetDay, btn) {
      var srcRow = document.querySelector('.schedule-row[data-day="' + _copySource + '"]');
      var times = Array.from(srcRow.querySelectorAll('.time-chip')).map(function(c){ return c.dataset.time; });
      var targetChips = document.querySelector('.schedule-row[data-day="' + targetDay + '"] .time-chips');
      targetChips.innerHTML = '';
      times.forEach(function(t) {
        var chip = document.createElement('div');
        chip.className = 'time-chip';
        chip.dataset.time = t;
        chip.innerHTML = '<span class="chip-time" onclick="editTimeSlot(this)">' + t + '</span>'
          + '<button class="chip-remove" onclick="removeChip(this)">&times;</button>';
        targetChips.appendChild(chip);
      });
      var orig = btn.textContent;
      btn.classList.add('copied');
      btn.textContent = '✓ ' + orig;
      setTimeout(function(){ btn.classList.remove('copied'); btn.textContent = orig; }, 1800);
    }

    // ── Save ─────────────────────────────────────────────────────────
    function saveSchedule() {
      var name = document.getElementById('schedule-name').value.trim() || 'Custom Schedule';
      var days = {};
      document.querySelectorAll('.schedule-row').forEach(function(row) {
        var dk = row.dataset.day;
        var times = [];
        row.querySelectorAll('.time-chip').forEach(function(c){ times.push(c.dataset.time); });
        if (times.length) days[dk] = times;
      });
      if (!Object.keys(days).length) {
        showSchedAlert('Add at least one time slot before saving.', 'error');
        return;
      }
      fetch('/schedule', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: name, days: days})
      }).then(function(r){ return r.json(); }).then(function(d) {
        if (d.ok) showSchedAlert('Schedule saved and activated!', 'success');
        else showSchedAlert(d.error || 'Error saving.', 'error');
      }).catch(function(){ showSchedAlert('Network error — try again.', 'error'); });
    }
    function showSchedAlert(msg, type) {
      var el = document.getElementById('sched-alert');
      el.innerHTML = '<div class="alert alert-' + (type === 'success' ? 'success' : 'error') + '" style="margin-bottom:16px">' + msg + '</div>';
      setTimeout(function(){ el.innerHTML = ''; }, 4000);
    }
    </script>'''
    return layout('schedule', content)



@app.route('/activate-schedule/<int:pattern_id>', methods=['POST'])
def activate_schedule(pattern_id):
    conn.execute("UPDATE schedule_patterns SET is_active=0")
    conn.execute("UPDATE schedule_patterns SET is_active=1 WHERE id=?", (pattern_id,))
    conn.commit()
    return redirect('/schedule')


@app.route('/delete-schedule/<int:pattern_id>', methods=['POST'])
def delete_schedule(pattern_id):
    conn.execute("DELETE FROM schedule_patterns WHERE id=?", (pattern_id,))
    conn.commit()
    return redirect('/schedule')


def _fetch_fb_thumbnail(conn, client, pid, fb_id):
    import requests as req
    try:
        thumb_url = client.get_post_thumbnail(fb_id)
        if thumb_url:
            r = req.get(thumb_url, timeout=15)
            if r.status_code == 200:
                thumb_dir = get_thumbnail_dir()
                thumb_dir.mkdir(parents=True, exist_ok=True)
                thumb_path = thumb_dir / f'{pid}.jpg'
                img = Image.open(BytesIO(r.content))
                img.thumbnail((400, 400), Image.LANCZOS)
                img.save(str(thumb_path), format='JPEG', quality=85)
                conn.execute("UPDATE posts SET fb_thumbnail_url=? WHERE id=?", (str(thumb_path), pid))
                conn.commit()
                return True
    except Exception as e:
        print(f'⚠ Could not fetch FB thumbnail for #{pid}: {e}')
    return False

def _purge_local_photo(conn, pid, photo_path):
    if photo_path and os.path.exists(photo_path):
        try:
            os.remove(photo_path)
        except Exception:
            pass
    conn.execute("UPDATE posts SET photo_path='' WHERE id=?", (pid,))
    conn.commit()


# ── Background scheduler ──────────────────────────────────────────

def run_scheduler():
    print('🕐 Scheduler started')
    while True:
        try:
            newly_posted = conn.execute("""
                SELECT id, facebook_post_id, photo_path, fb_thumbnail_url FROM posts
                WHERE status='fb_scheduled'
                  AND scheduled_time < datetime('now', 'localtime', '-5 minutes')
            """).fetchall()
            if newly_posted:
                conn.execute("""
                    UPDATE posts SET status='posted'
                    WHERE status='fb_scheduled'
                      AND scheduled_time < datetime('now', 'localtime', '-5 minutes')
                """)
                conn.commit()
                page_id_tmp = get_setting(conn, 'page_id')
                token_tmp = get_setting(conn, 'page_access_token')
                if page_id_tmp and token_tmp:
                    client_tmp = FacebookClient(page_id_tmp, token_tmp)
                    for np in newly_posted:
                        if np['facebook_post_id']:
                            if not np['fb_thumbnail_url']:
                                _fetch_fb_thumbnail(conn, client_tmp, np['id'], np['facebook_post_id'])
                            if np['photo_path'] and os.path.exists(np['photo_path'] or ''):
                                _purge_local_photo(conn, np['id'], np['photo_path'])

            page_id = get_setting(conn, 'page_id')
            token   = get_setting(conn, 'page_access_token')
            if page_id and token:
                client = FacebookClient(page_id, token)
                due = conn.execute("""
                    SELECT * FROM posts
                    WHERE status IN ('scheduled','retrying')
                      AND (scheduled_time <= datetime('now', 'localtime', '+29 days'))
                    ORDER BY scheduled_time ASC
                """).fetchall()
                for row in due:
                    post = dict(row)
                    pid  = post['id']
                    update_post_status(conn, pid, status='posting',
                                       last_attempt_at=datetime.now().isoformat())
                    log_event(conn, pid, 'attempt', description='Scheduled upload started')
                    success, fb_id, error = attempt_post(client, post)
                    if error and error.startswith('SKIP:'):
                        update_post_status(conn, pid, status='scheduled',
                                           last_error=None)
                        continue
                    if success:
                        try:
                            sched_dt = datetime.strptime(post['scheduled_time'][:16], '%Y-%m-%d %H:%M')
                            is_future = sched_dt > datetime.now()
                        except Exception:
                            is_future = False
                        new_status = 'fb_scheduled' if is_future else 'posted'
                        update_post_status(conn, pid, status=new_status,
                                           facebook_post_id=fb_id,
                                           posted_at=datetime.now().isoformat(),
                                           last_error=None)
                        _fetch_fb_thumbnail(conn, client, pid, fb_id)
                        if new_status == 'posted':
                            _purge_local_photo(conn, pid, post.get('photo_path', ''))
                        log_event(conn, pid, 'success', response_body=f'post_id={fb_id}',
                                  description='Scheduled on Facebook — will post at scheduled time' if is_future else 'Post published to Facebook successfully')
                        print(f'✅ Scheduled #{pid} on Facebook (status: {new_status})')
                        if get_setting(conn, 'notify_on_success') == '1':
                            notify('Ibis Publisher ✓', (post.get('caption') or '')[:60])
                    else:
                        attempt_count = post.get('attempt_count', 0) + 1
                        is_fatal = error.startswith('AUTH_ERROR') or \
                                   error.startswith('FILE_NOT_FOUND') or \
                                   'HTTP 400' in error or 'HTTP 403' in error or \
                                   attempt_count >= 3
                        if is_fatal:
                            update_post_status(conn, pid, status='failed',
                                               attempt_count=attempt_count, last_error=error)
                            log_event(conn, pid, 'failure', error_message=error,
                                      description='Upload failed — post marked as failed')
                            if get_setting(conn, 'notify_on_failure') == '1':
                                notify('Ibis Publisher — Failed', error[:80])
                        else:
                            delay = [5, 10, 20][min(attempt_count - 1, 2)]
                            next_try = (datetime.now() + timedelta(minutes=delay)).isoformat()
                            update_post_status(conn, pid, status='retrying',
                                               attempt_count=attempt_count,
                                               last_error=error, next_attempt_at=next_try)
                            log_event(conn, pid, 'retry', error_message=error,
                                      description=f'Upload failed, retrying (attempt {attempt_count})')
            purge_old_photos(conn)
        except Exception as e:
            print(f'Scheduler error: {e}')
        time.sleep(60)


def purge_old_photos(conn):
    ready = conn.execute("""
        SELECT id, photo_path FROM posts
        WHERE status = 'posted'
          AND photo_path IS NOT NULL AND photo_path != ''
          AND fb_thumbnail_url IS NOT NULL AND fb_thumbnail_url != ''
    """).fetchall()
    for row in ready:
        _purge_local_photo(conn, row['id'], row['photo_path'])
    retry = conn.execute("""
        SELECT id, facebook_post_id, photo_path, status FROM posts
        WHERE status IN ('posted', 'fb_scheduled')
          AND facebook_post_id IS NOT NULL AND facebook_post_id != ''
          AND (fb_thumbnail_url IS NULL OR fb_thumbnail_url = '')
    """).fetchall()
    if retry:
        page_id = get_setting(conn, 'page_id')
        token = get_setting(conn, 'page_access_token')
        if page_id and token:
            client = FacebookClient(page_id, token)
            for r in retry:
                if _fetch_fb_thumbnail(conn, client, r['id'], r['facebook_post_id']):
                    if r['status'] == 'posted':
                        _purge_local_photo(conn, r['id'], r['photo_path'] or '')
    old_thumbs = conn.execute("""
        SELECT id, fb_thumbnail_url FROM posts
        WHERE status = 'posted'
          AND fb_thumbnail_url IS NOT NULL AND fb_thumbnail_url != ''
          AND posted_at < datetime('now', '-90 days')
    """).fetchall()
    for row in old_thumbs:
        path = row['fb_thumbnail_url']
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
        conn.execute("UPDATE posts SET fb_thumbnail_url='' WHERE id=?", (row['id'],))
    if old_thumbs:
        conn.commit()


def _recover_interrupted_posts(conn):
    """Reset any posts stuck in 'posting' status — app was killed mid-upload."""
    stuck = conn.execute("SELECT id FROM posts WHERE status='posting'").fetchall()
    for (pid,) in stuck:
        conn.execute(
            "UPDATE posts SET status='failed', last_error=? WHERE id=?",
            ('Upload interrupted — app closed during upload', pid)
        )
        log_event(conn, pid, 'failure', description='Upload interrupted — app was closed mid-upload')
    if stuck:
        conn.commit()


def main():
    global conn
    conn = init_db()
    _recover_interrupted_posts(conn)
    handle_missed_posts(conn)
    threading.Thread(target=run_scheduler, daemon=True, name='scheduler').start()
    port = 8765
    print(f'\n🦢 Ibis Publisher is running at http://localhost:{port}')
    print('Press Ctrl+C to quit.\n')
    threading.Timer(1.5, lambda: webbrowser.open(f'http://localhost:{port}')).start()
    app.run(port=port, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
