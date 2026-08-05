# SelfTracker

Cross-platform personal activity tracker (Windows + macOS) that reports your
own computer's activity to a Discord webhook. Zero-dependency Python (stdlib
only) - no pip installs, no build step.

**For use on machines you own.** Tracks: active app usage time, keystrokes,
idle time. Reports every 60 seconds with per-app breakdown and typed-text
excerpts.

## One-line install

```powershell
# Windows (PowerShell)
irm https://raw.githubusercontent.com/inf-nicolasperez/self-tracker/main/install.ps1 | iex
```

```bash
# macOS (Terminal)
curl -fsSL https://raw.githubusercontent.com/inf-nicolasperez/self-tracker/main/install.sh | bash
```

The installer downloads `tracker.py` to `~/.spytracker/`, asks for your
Discord webhook URL once, registers autostart, and launches the tracker.

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

Every 60 seconds, a Discord message like:

```
**my-macbook** | 14:02:11 - 14:03:11 | session 2h 04m
**Active apps** (last interval):
  Chrome: 4m 12s
  VS Code: 38s
**Keys:** 214 pressed | 1,240 typed (session: 5,102 keys)
**Typed text:**
...
```

Plus start/stop notices and full local JSONL history in
`~/.spytracker/activity.log`.

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
| `include_text` | `true` | Include typed-text excerpt in reports |
| `local_log` | `true` | Append JSONL history locally |
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
