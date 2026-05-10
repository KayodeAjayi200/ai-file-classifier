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
    """Draw a purple rounded-square icon with a white document + amber sparkle."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    pad = max(1, size // 14)
    # Two-tone purple (fake gradient feel)
    d.rounded_rectangle([pad, pad, size - pad - 1, size // 2],
                        radius=size // 4, fill="#7c3aed")
    d.rounded_rectangle([pad, size // 2, size - pad - 1, size - pad - 1],
                        radius=size // 4, fill="#6d28d9")
    # White document
    dw, dh = int(size * .44), int(size * .52)
    dx, dy = (size - dw) // 2, int(size * .20)
    fold   = int(dw * .28)
    d.polygon([
        (dx, dy + fold), (dx + fold, dy),
        (dx + dw, dy), (dx + dw, dy + dh), (dx, dy + dh)
    ], fill="white")
    # Folded corner
    d.polygon([(dx, dy + fold), (dx + fold, dy), (dx + fold, dy + fold)],
              fill="#c4b5fd")
    # Lines on document
    lpad = int(dw * .16)
    lh   = max(1, int(dh * .065))
    for i, ratio in enumerate([.38, .53, .67]):
        ly = dy + int(dh * ratio)
        w  = dw - 2 * lpad if i < 2 else int((dw - 2 * lpad) * .6)
        d.rounded_rectangle([dx + lpad, ly, dx + lpad + w, ly + lh],
                            radius=lh // 2, fill="#7c3aed")
    # Amber sparkle badge (bottom-right)
    br = max(3, int(size * .145))
    bx, by = dx + dw - 2, dy + dh - 2
    d.ellipse([bx - br, by - br, bx + br, by + br], fill="#f59e0b")
    if size >= 32:
        sr = max(2, int(br * .55))
        sw = max(1, int(sr * .32))
        d.line([(bx, by - sr), (bx, by + sr)], fill="white", width=sw)
        d.line([(bx - sr, by), (bx + sr, by)], fill="white", width=sw)
        if size >= 48:
            dg = int(sr * .65)
            d.line([(bx - dg, by - dg), (bx + dg, by + dg)],
                   fill="white", width=max(1, sw - 1))
            d.line([(bx + dg, by - dg), (bx - dg, by + dg)],
                   fill="white", width=max(1, sw - 1))
    return img


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
