# 🦢 Ibis Publisher

**Schedule and auto-publish photos from Adobe Lightroom Classic to your Facebook Page.**

No manual uploading. Select photos in Lightroom, add captions, pick a schedule — Ibis Publisher does the rest.

---

## How It Works

Ibis Publisher has two parts that work together:

| Component | What it does |
|-----------|--------------|
| **Lightroom Plugin** (`.lrplugin`) | Scheduling dialog inside Lightroom — select photos, write captions, assign time slots, add to queue |
| **Companion App** (Python / `.app` / `.exe`) | Runs in background — fires Facebook API calls at scheduled times, handles retries, sends notifications |

They share a SQLite database on your local disk. No cloud account or subscription needed.

---

## One-Time Setup

### Step 1 — Create a Facebook Developer App (10 min)

> **Important:** Facebook's API only supports posting to **Facebook Pages**, not personal profiles. You need a Page for your photography (free, takes 5 min to create at facebook.com/pages/create).

1. Go to [developers.facebook.com](https://developers.facebook.com) → **My Apps** → **Create App**
2. Choose type: **Business**
3. Name it anything (e.g. "My Photo Scheduler") and click **Create**
4. In the left sidebar → **Add Products** → **Pages API** → **Set Up**
5. Go to **App Settings → Basic** → note your **App ID**
6. Under **Roles**, add yourself as a **Tester** (for Development Mode — no App Review needed for your own page)

### Step 2 — Get a Page Access Token (5 min)

1. Go to [Graph API Explorer](https://developers.facebook.com/tools/explorer)
2. Top-right dropdown: select your app
3. "User or Page" dropdown: select **your Facebook Page**
4. Click **Add a Permission** and add:
   - `pages_manage_posts`
   - `pages_read_engagement`
   - `pages_show_list`
5. Click **Generate Access Token** → authorize
6. Copy the token — you'll paste it into the companion app

> Your **Page ID** is found under your Page → **About** → **Page Transparency** → Page ID.

### Step 3 — Install the Companion App

**macOS:**
```bash
cd companion-app
pip install -r requirements.txt
python app.py          # development mode, or build .app with:
cd ../build-scripts && bash build-macos.sh
```

**Windows:**
```bash
cd companion-app
pip install -r requirements.txt
python app.py          # development mode, or build .exe with:
cd ..\build-scripts && build-windows.bat
```

On first launch, the **Connect Facebook** wizard opens automatically. Paste your token and Page ID.

### Step 4 — Install the Lightroom Plugin

1. In Lightroom Classic: **File → Plug-in Manager → Add**
2. Navigate to `ibis-publisher/lightroom-plugin/IbisPublisher.lrplugin`
3. Click **Add Plug-in**

That's it. You'll find **Schedule for Facebook...** under `File → Plug-in Extras`.

---

## Daily Workflow

1. **Edit your photos** in Lightroom as usual
2. **Select 1 to 50+ photos** in the filmstrip
3. Go to **File → Plug-in Extras → Schedule for Facebook...**
4. In the dialog:
   - Write individual captions (or use a template with tokens like `{camera}`, `{capture_date}`)
   - Apply to all photos with **Apply to All** for bulk captions
   - Choose a **schedule pattern** (or use the active one)
   - Review the time slots in the right panel
5. Click **Add to Queue**
6. Leave the **Ibis Publisher companion app running** — it posts automatically at the scheduled times

---

## Caption Tokens

Use these in your captions to auto-fill from Lightroom metadata:

| Token | Example output |
|-------|---------------|
| `{capture_date}` | June 12, 2026 |
| `{camera}` | Sony A7R V |
| `{lens}` | 85mm f/1.4 |
| `{aperture}` | f/2.8 |
| `{shutter}` | 1/500s |
| `{iso}` | ISO 400 |
| `{focal_length}` | 85mm |
| `{keywords}` | landscape, mountains, sunset |
| `{location}` | Patagonia, Argentina |
| `{filename}` | DSC09812 |
| `{rating}` | ★★★★★ |
| `{capture_year}` | 2026 |

**Example template:** `Shot in {location} · {camera} · {aperture} at {shutter} · {capture_date}`

---

## Schedule Patterns

Configure posting schedules in the companion app → Settings. Built-in patterns:

| Pattern | When it posts |
|---------|--------------|
| Daily at 8am | Every day, 8:00 AM |
| Mon/Wed/Fri at 7pm | Mon, Wed, Fri at 7:00 PM |
| Weekdays at 9am | Monday–Friday, 9:00 AM |
| Twice daily | Every day, 8:00 AM and 6:00 PM |

---

## Batch Scheduling

Select 50 photos → open dialog → each photo becomes its own post, automatically filling the next available slots in your schedule. No manual time-picking.

> **Rate limit:** Facebook allows 25 posts per 24-hour window. Ibis Publisher warns you if a batch would exceed this and reschedules the overflow automatically.

---

## Reliability

- **Automatic retries:** Failed posts are retried up to 3 times (5 min → 10 min → 20 min backoff)
- **Desktop notifications:** Success and failure notifications via your OS notification center
- **Missed posts:** If the companion app was off when a post was due, it catches up on next launch (within 24 hours)
- **Activity log:** Full audit trail in the companion app → Activity Log
- **Daily backup:** `queue.db.bak` is kept alongside `queue.db`

---

## File Locations

| What | Where |
|------|-------|
| Database | `~/Library/Application Support/IbisPublisher/queue.db` (macOS) |
| Database | `%APPDATA%\IbisPublisher\queue.db` (Windows) |
| Exported JPEGs | `~/Library/Application Support/IbisPublisher/exports/` |
| Lightroom Plugin | `ibis-publisher/lightroom-plugin/IbisPublisher.lrplugin/` |

---

## Known Limitations (v1)

- **Facebook Pages only** — personal profiles are not supported by Facebook's API (this is Meta's policy, not a technical limitation we can work around)
- **Facebook Groups** — also not supported by the API since 2018
- **Computer must be on** — the companion app needs to be running for posts to go out. If your Mac/PC is off at posting time, it catches up within 24 hours on next launch
- **Token expires every ~60 days** — the app reminds you 10 days before and guides you through re-authentication

---

## Roadmap

- [ ] **v1.1** — Plugin↔app communication via local HTTP (more robust than SQLite CLI)
- [ ] **v1.2** — Calendar view with drag-and-drop reordering
- [ ] **v2.0** — Instagram publishing (same Meta API infrastructure)
- [ ] **v2.1** — AI caption generation from photo content (optional Claude API integration)
- [ ] **v2.2** — LinkedIn publishing

---

## Project Structure

```
ibis-publisher/
├── lightroom-plugin/
│   └── IbisPublisher.lrplugin/
│       ├── Info.lua               Plugin manifest
│       ├── PluginInit.lua         Startup, path setup
│       ├── ScheduleDialog.lua     Main scheduling UI
│       ├── TokenResolver.lua      EXIF/metadata → caption tokens
│       ├── ScheduleCalculator.lua Slot calculation from patterns
│       ├── Database.lua           SQLite via CLI
│       └── SettingsProvider.lua   Plugin Manager panel
├── companion-app/
│   ├── app.py                     Main GUI (tkinter)
│   ├── requirements.txt
│   └── core/
│       ├── db.py                  Database layer
│       ├── facebook_api.py        Graph API client
│       ├── scheduler.py           Background posting engine
│       └── notifications.py       Cross-platform notifications
├── shared/
│   └── schema.sql                 SQLite schema (shared source of truth)
├── build-scripts/
│   ├── build-macos.sh
│   └── build-windows.bat
├── run.py                         Dev launcher
└── README.md
```

---

*Ibis Publisher is a personal-use tool. It is not affiliated with Meta or Adobe.*
