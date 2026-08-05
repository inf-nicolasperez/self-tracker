# SelfTracker

Cross-platform personal activity tracker (Windows + macOS) that reports your
own computer's activity to a Discord webhook. Zero-dependency Python (stdlib
only) - no pip installs, no build step.

**For use on machines you own.** Tracks: active app usage time, keystrokes,
idle time. Reports every 60 seconds with per-app breakdown and typed-text
excerpts.

## One-line install

Pass your webhook URL in the command - silent, no prompts, no confirmation.

```bash
# macOS (Terminal) - replace YOUR-WEBHOOK-URL
mkdir -p ~/.spytracker && curl -fsSL https://raw.githubusercontent.com/inf-nicolasperez/self-tracker/v1/tracker.py -o ~/.spytracker/tracker.py && python3 ~/.spytracker/tracker.py --install 'YOUR-WEBHOOK-URL'
```

```powershell
# Windows (PowerShell)
New-Item -ItemType Directory -Force -Path "$HOME\.spytracker" | Out-Null
curl.exe -fsSL https://raw.githubusercontent.com/inf-nicolasperez/self-tracker/v1/tracker.py -o "$HOME\.spytracker\tracker.py"
python "$HOME\.spytracker\tracker.py" --install 'YOUR-WEBHOOK-URL'
```

This downloads `tracker.py` to `~/.spytracker/`, saves the webhook URL,
registers autostart, and starts tracking. Without a URL it runs local-log
only (set the URL later in `~/.spytracker/config.json`).

## 1. Create a Discord webhook

Discord server → channel settings → **Integrations** → **Webhooks** → New
Webhook → copy the URL. Paste it when the installer asks.

## 2. Grant macOS permissions (one-time, required for keystrokes)

Windows works out of the box. On macOS:

1. **Automation** - the first run prompts: "Terminal wants to control System
   Events". Click OK (needed for app-name tracking).
2. **Accessibility** - System Settings → Privacy & Security →
   Accessibility → enable Terminal (needed for keystroke capture). Without it
   the tracker runs but logs no keystrokes.

## What it sends

Every 60 seconds, a Discord message with per-event detail:

```
**my-macbook** | 14:02:11 - 14:03:11 | session 2h 04m
**Apps** (last 60s, time range):
  Chrome: 4m 12s (14:02:11-14:05:23)
  VS Code: 38s (14:05:24-14:06:02)
**Idle:**
  2m 14s (14:06:03-14:08:17)
**Keys:** 214 pressed | 1,240 typed (session: 5,102 keys)
**App switches:**
  14:05:24 VS Code.exe (project - main.ts)
**Typed text** (24 events, time + window):
  14:05:25 VS Code.exe [project - main.ts]: def main
  14:05:27 VS Code.exe [project - main.ts]: ():
```

Plus start/stop notices. Full local detail:

- `~/.spytracker/events.jsonl` - every keystroke, app switch and idle period,
  timestamped with the exact window it happened in (machine-readable)
- `~/.spytracker/activity.log` - one JSON summary line per report
- `~/.spytracker/tracker.log` - runtime log

Every keystroke event looks like:

```json
{"type": "key", "ts": 1785894147.71, "app": "Chrome.exe", "title": "gmail.com", "text": "h"}
```

## Configuration

`~/.spytracker/config.json`:

| Key | Default | Meaning |
|---|---|---|
| `webhook_url` | `""` | Discord webhook URL (empty = local log only) |
| `report_interval` | `60` | Seconds between reports |
| `poll_interval` | `2.0` | Foreground-app check frequency |
| `key_poll` | `0.02` | Keystroke poll frequency (50 Hz) |
| `idle_threshold` | `60` | Seconds of inactivity before counting idle |
| `keylog_enabled` | `true` | Master switch for keystroke capture |
| `include_text` | `true` | Include typed text in reports |
| `discord_detail` | `true` | Per-event typed text, app switches, idle ranges in reports |
| `max_text_events` | `25` | Max typed-text events shown per report |
| `local_log` | `true` | Append report summaries to `activity.log` |
| `log_events` | `true` | Append every event to `events.jsonl` |
| `device_name` | hostname | Shown in reports |

## Managing

```bash
python ~/.spytracker/tracker.py --test      # send a test message
python ~/.spytracker/tracker.py --once      # run until first report, exit
python ~/.spytracker/tracker.py --dry       # sample locally, print, no send
python ~/.spytracker/tracker.py --install   # re-register autostart / relaunch
python ~/.spytracker/tracker.py --uninstall # remove autostart entry
```

Stop the running tracker: Task Manager (Windows) / Activity Monitor (macOS) -
kill the `python`/`pythonw` process named SelfTracker.

## Privacy notes

- Keystroke capture includes typed text - sensitive input (passwords) is
  visible in reports. Set `keylog_enabled: false` for stats-only mode.
- Reports and logs live in `~/.spytracker/`.
- Autostart is a standard, visible entry (HKCU Run key on Windows,
  `~/Library/LaunchAgents/com.self-tracker.plist` on macOS) - no stealth.
