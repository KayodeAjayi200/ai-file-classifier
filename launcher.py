"""AI File Classifier — Desktop Launcher
Double-click (or run via shortcut) to start the server in the background
and open the browser. A system-tray icon lets you open, admin, or quit.
"""
import json, subprocess, sys, os, time, threading, webbrowser
import urllib.request
from pathlib import Path

BASE_DIR   = Path(__file__).parent
SERVER_URL = "http://localhost:5050"
ICON_PATH  = BASE_DIR / "app_icon.ico"

# pythonw.exe runs without a console window on Windows
_PYTHONW = Path(sys.executable).with_name("pythonw.exe")
PYTHON   = str(_PYTHONW) if _PYTHONW.exists() else sys.executable

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False


# ── Icon generation ───────────────────────────────────────────────────────────

def _build_icon(size: int = 64) -> "Image.Image":
    """Draw a purple rounded-square icon with a white document."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    pad = max(1, size // 14)
    d.rounded_rectangle([pad, pad, size - pad - 1, size // 2],
                        radius=size // 4, fill="#7c3aed")
    d.rounded_rectangle([pad, size // 2, size - pad - 1, size - pad - 1],
                        radius=size // 4, fill="#6d28d9")
    dw, dh = int(size * .44), int(size * .52)
    dx, dy = (size - dw) // 2, int(size * .20)
    fold   = int(dw * .28)
    d.polygon([
        (dx, dy + fold), (dx + fold, dy),
        (dx + dw, dy), (dx + dw, dy + dh), (dx, dy + dh)
    ], fill="white")
    d.polygon([(dx, dy + fold), (dx + fold, dy), (dx + fold, dy + fold)],
              fill="#c4b5fd")
    lpad = int(dw * .16)
    lh   = max(1, int(dh * .065))
    for i, ratio in enumerate([.38, .53, .67]):
        ly = dy + int(dh * ratio)
        w  = dw - 2 * lpad if i < 2 else int((dw - 2 * lpad) * .6)
        d.rounded_rectangle([dx + lpad, ly, dx + lpad + w, ly + lh],
                            radius=lh // 2, fill="#7c3aed")
    return img


def _build_status_icon(size: int, dot_color: str) -> "Image.Image":
    """Base icon + a coloured status dot in the bottom-right corner."""
    img  = _build_icon(size)
    d    = ImageDraw.Draw(img)
    r    = max(4, size // 8)           # dot radius
    cx   = size - r - max(1, size // 16)
    cy   = size - r - max(1, size // 16)
    # white ring for contrast
    d.ellipse([cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1], fill="white")
    d.ellipse([cx - r,     cy - r,     cx + r,     cy + r    ], fill=dot_color)
    return img


def _make_icons(size: int = 64) -> dict:
    return {
        "starting":   _build_icon(size),                   # plain purple
        "online":     _build_status_icon(size, "#22c55e"),  # green
        "processing": _build_status_icon(size, "#f59e0b"),  # amber
        "offline":    _build_status_icon(size, "#ef4444"),  # red
    }


def ensure_icon():
    """Generate a multi-size app_icon.ico if it doesn't exist yet."""
    if ICON_PATH.exists():
        return
    try:
        sizes = [256, 64, 48, 32, 16]
        imgs  = [_build_icon(s).convert("RGBA") for s in sizes]
        imgs[0].save(str(ICON_PATH), format="ICO",
                     append_images=imgs[1:],
                     sizes=[(s, s) for s in sizes])
    except Exception:
        pass


# ── Server management ─────────────────────────────────────────────────────────

_server_proc: "subprocess.Popen | None" = None


def _is_running() -> bool:
    return _server_proc is not None and _server_proc.poll() is None


def start_server():
    global _server_proc
    if _is_running():
        return
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    _server_proc = subprocess.Popen(
        [PYTHON, str(BASE_DIR / "search.py")],
        cwd=str(BASE_DIR),
        creationflags=flags,
    )


def stop_server():
    global _server_proc
    if _is_running():
        _server_proc.terminate()
        try:
            _server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _server_proc.kill()
    _server_proc = None


def wait_for_server(timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(SERVER_URL + "/favicon.ico", timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


# ── Status polling ────────────────────────────────────────────────────────────

# Shared state written by poller, read by menu
_status_label = ["Starting…"]   # single-item list so menu closure sees updates


def _status_poller(icon, icons: dict):
    """Poll /api/tray-status every 5 s and update icon + tooltip."""
    prev_state   = "starting"
    prev_queue   = 0

    while True:
        time.sleep(5)
        try:
            with urllib.request.urlopen(SERVER_URL + "/api/tray-status", timeout=3) as r:
                data      = json.loads(r.read())
            pending    = data.get("pending",    0)
            processing = data.get("processing", 0)
            analyzed   = data.get("analyzed",   0)
            queue      = pending + processing

            if queue > 0:
                state   = "processing"
                label   = f"🟡 AI analysing {queue} file{'s' if queue != 1 else ''}…"
                tooltip = f"AI File Classifier — Analysing {queue} file{'s' if queue != 1 else ''}…"
            else:
                state   = "online"
                label   = f"🟢 Online · {analyzed:,} files indexed"
                tooltip = f"AI File Classifier — Online · {analyzed:,} files indexed"

            # Transition notifications
            if prev_state == "processing" and state == "online":
                _notify(icon, "Analysis complete",
                        f"{analyzed:,} files indexed")
            elif prev_state == "offline":
                _notify(icon, "AI File Classifier", "Server is back online")

        except Exception:
            state   = "offline"
            label   = "🔴 Server offline"
            tooltip = "AI File Classifier — Server offline"
            if prev_state not in ("starting", "offline"):
                _notify(icon, "AI File Classifier",
                        "Server has stopped unexpectedly")

        _status_label[0] = label
        if state != prev_state:
            try:
                icon.icon  = icons[state]
                icon.title = tooltip
            except Exception:
                pass
        elif state != "offline":
            try:
                icon.title = tooltip
            except Exception:
                pass

        prev_state = state
        prev_queue = queue if state != "offline" else 0


def _notify(icon, title: str, msg: str):
    try:
        icon.notify(title, msg)
    except Exception:
        pass


# ── Tray application ──────────────────────────────────────────────────────────

def run_tray():
    ensure_icon()
    icons = _make_icons(64)

    def _open(_=None, __=None):
        webbrowser.open(SERVER_URL)

    def _open_admin(_=None, __=None):
        webbrowser.open(SERVER_URL + "/admin")

    def _restart(_=None, __=None):
        stop_server()
        start_server()
        threading.Thread(target=lambda: (
            wait_for_server() and _notify(icon, "AI File Classifier", "Server restarted")
        ), daemon=True).start()

    def _quit(_=None, __=None):
        stop_server()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(lambda _: _status_label[0], _open, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open AI File Classifier", _open, default=True),
        pystray.MenuItem("Open Admin Panel", _open_admin),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Restart Server", _restart),
        pystray.MenuItem("Exit", _quit),
    )
    icon = pystray.Icon("AIFileClassifier", icons["starting"],
                        "AI File Classifier — Starting…", menu)

    def _startup():
        start_server()
        ready = wait_for_server()
        if ready:
            webbrowser.open(SERVER_URL)
            _notify(icon, "AI File Classifier", "Ready — opening in your browser")
            # Hand off to status poller
            _status_poller(icon, icons)
        else:
            icon.icon  = icons["offline"]
            icon.title = "AI File Classifier — Failed to start"
            _notify(icon, "AI File Classifier",
                    "Could not start. Make sure Python packages are installed.\n"
                    "Run setup.bat and try again.")

    threading.Thread(target=_startup, daemon=True).start()
    icon.run()


# ── Fallback: no pystray ──────────────────────────────────────────────────────

def run_simple():
    print("Starting AI File Classifier…")
    start_server()
    ready = wait_for_server()
    if ready:
        print(f"Ready → {SERVER_URL}")
        webbrowser.open(SERVER_URL)
        print("Press Ctrl+C to stop.\n")
        try:
            _server_proc.wait()
        except KeyboardInterrupt:
            pass
    else:
        print("ERROR: server did not start. Check search.py for errors.")
    stop_server()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if HAS_TRAY:
        run_tray()
    else:
        print("Tip: pip install pystray  — for a system-tray icon")
        run_simple()
