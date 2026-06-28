# ADY Monitor

Monitors [ticket.ady.az](https://ticket.ady.az) for Baku ↔ Tbilisi train
ticket availability and sends Telegram notifications the moment anything changes.

## Architecture

```
Browser (Chrome)           API layer                 Notifications
─────────────────     ────────────────────────     ─────────────────
Establishes CF        Polls 3 endpoints:           Telegram Bot
session via real  →   • get_trip_dates         →   on every change
user interaction      • get_traintrip
                      • get_trip (optional)
Keepalive actions
every 3–8 minutes
```

The browser is only used to maintain a trusted Cloudflare session.
All ticket data comes from the JSON API directly.

## Project Structure

```
ady_monitor/
├── main.py                   Entry point
├── requirements.txt
├── config/
│   └── settings.py           ← Edit this first
├── models/
│   └── trip.py               TripDate, Trip, WagonClass, RouteSnapshot
├── network/
│   └── api_client.py         ADYApiClient (3 endpoints)
├── browser/
│   ├── keepalive.py          Human-like mouse/scroll actions
│   └── session.py            CF cookie extraction + keepalive scheduler
├── telegram/
│   └── bot.py                Notification formatters
├── monitor/
│   ├── state.py              Change detection + JSON persistence
│   └── poller.py             Main polling loop
└── utils/
    └── logging_setup.py      Rotating log file + console
```

## Setup

### 1. Configure settings

Edit `config/settings.py`:

```python
TELEGRAM_BOT_TOKEN = "7123456789:AAF..."   # From @BotFather
TELEGRAM_CHAT_ID   = "123456789"           # Your chat/group ID
```

To get your chat ID, message your bot and call:
`https://api.telegram.org/bot<TOKEN>/getUpdates`

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Launch Chrome with remote debugging

```bat
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

Or create a shortcut with that argument.

### 4. Open the ticket site

Navigate to `https://ticket.ady.az` in that Chrome window.
Wait for Cloudflare to clear (the page loads normally — no spinner).

### 5. Run the monitor

```bash
python main.py
```

The monitor will:
- Extract Cloudflare cookies via Chrome DevTools Protocol
- Start the keepalive scheduler
- Begin polling on a randomised 10–60 minute interval
- Send a startup notification to your Telegram

## Telegram Notifications

**New trip available:**
```
🚆 ADY Ticket Update

Direction: Baku → Tbilisi
Date: 26-07-2026
Train: #38
Departure: 23:10
Arrival: 08:41
Total Free Seats: 16

Classes:
  • Luxe (L): 4 seats @ 192.88 AZN
  • Coupe (K): 12 seats @ 139.97 AZN

Detected: 26-06-2026 00:36:12
🔗 Book now
```

**Seat count changed:**
```
📉 ADY Seat Count Changed

Direction: Tbilisi → Baku
Date: 27-07-2026
Train: #38 @ 09:30
Seats: 16 → 4

Detected: 26-06-2026 02:14:08
```

**Cloudflare challenge:**
```
⚠️ Cloudflare challenge detected

Please open the browser and solve the challenge manually.
Polling has been paused.
```

## Cloudflare Recovery

If Cloudflare blocks the session:
1. You'll receive a Telegram notification.
2. Open the Chrome window and solve the challenge.
3. The monitor detects the new `cf_clearance` cookie and resumes automatically.

## State File

`monitor_state.json` stores the last known ticket snapshot.
Delete it to reset change detection (will re-notify about all current tickets).

## Logs

`ady_monitor.log` — rotating, 5 MB × 3 backups.
Console output mirrors the log file.
