"""AI File Classifier — Desktop Launcher
Double-click (or run via shortcut) to start the server in the background
and open the browser. A system-tray icon lets you open, admin, or quit.
"""
import subprocess, sys, os, time, threading, webbrowser
from pathlib import Path

BASE_DIR   = Path(__file__).parent
SERVER_URL = "http://localhost:5050"
ICON_PATH  = BASE_DIR / "app_icon.ico"

# pythonw.exe runs without a console window on Windows
_PYTHONW = Path(sys.executable).with_name("pythonw.exe")
PYTHON   = str(_PYTHONW) if _PYTHONW.exists() else sys.executable

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False


# ── Icon generation ───────────────────────────────────────────────────────────

def _build_icon(size: int = 256) -> "Image.Image":
    """Draw a purple rounded-square with a white document + sparkle."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    pad = size // 12
    rad = size // 5
    # purple background
    d.rounded_rectangle([pad, pad, size - pad, size - pad],
                        radius=rad, fill="#7c3aed")
    # white document body
    dw, dh = int(size * .38), int(size * .46)
    dx, dy = (size - dw) // 2, int(size * .24)
    fold = int(dw * .28)
    d.polygon([
        (dx, dy + fold), (dx + fold, dy),
        (dx + dw, dy), (dx + dw, dy + dh),
        (dx, dy + dh)
    ], fill="white")
    # folded corner
    d.polygon([
        (dx, dy + fold), (dx + fold, dy), (dx + fold, dy + fold)
    ], fill="#c4b5fd")
    # three text lines on the doc
    lx, lw = dx + int(dw * .22), int(dw * .56)
    for i, ly_off in enumerate([.38, .52, .66]):
        ly = dy + int(dh * ly_off)
        w  = lw if i < 2 else int(lw * .65)
        d.rounded_rectangle([lx, ly, lx + w, ly + int(dh * .07)],
                             radius=2, fill="#e9d5ff")
    # sparkle ✦ (top-right corner of background)
    sx, sy = int(size * .65), int(size * .16)
    sr = int(size * .09)
    for angle in (0, 45, 90, 135):
        import math
        a = math.radians(angle)
        x1 = sx + int(sr * .3 * math.cos(a))
        y1 = sy + int(sr * .3 * math.sin(a))
        x2 = sx + int(sr * math.cos(a + math.pi / 2 * 0))
        y2 = sy + int(sr * math.sin(a + math.pi / 2 * 0))
        d.line([(sx - int(sr * math.cos(a)), sy - int(sr * math.sin(a))),
                (sx + int(sr * math.cos(a)), sy + int(sr * math.sin(a)))],
               fill="white", width=max(2, size // 64))
    return img


def ensure_icon():
    """Generate app_icon.ico if it doesn't exist yet."""
    if not ICON_PATH.exists():
        try:
            img = _build_icon(256)
            # Save as multi-size ICO
            img.save(str(ICON_PATH), format="ICO",
                     sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
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
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(SERVER_URL + "/favicon.ico", timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


# ── Tray application ──────────────────────────────────────────────────────────

def run_tray():
    ensure_icon()
    try:
        icon_img = _build_icon(64)
    except Exception:
        from PIL import Image
        icon_img = Image.new("RGB", (64, 64), "#7c3aed")

    _icon_ref: list = []

    def _open(_=None, __=None):
        webbrowser.open(SERVER_URL)

    def _open_admin(_=None, __=None):
        webbrowser.open(SERVER_URL + "/admin")

    def _restart(_=None, __=None):
        stop_server()
        start_server()
        threading.Thread(target=lambda: (
            wait_for_server() and _icon_ref[0].notify("Server restarted", "AI File Classifier")
        ), daemon=True).start()

    def _quit(_=None, __=None):
        stop_server()
        _icon_ref[0].stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open AI File Classifier", _open, default=True),
        pystray.MenuItem("Open Admin Panel", _open_admin),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Restart Server", _restart),
        pystray.MenuItem("Exit", _quit),
    )
    icon = pystray.Icon("AIFileClassifier", icon_img, "AI File Classifier", menu)
    _icon_ref.append(icon)

    def _startup():
        start_server()
        ready = wait_for_server()
        if ready:
            webbrowser.open(SERVER_URL)
            try:
                icon.notify("Ready", "AI File Classifier is running")
            except Exception:
                pass
        else:
            try:
                icon.notify("Error", "Server failed to start — check search.py")
            except Exception:
                pass

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
