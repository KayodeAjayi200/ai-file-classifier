# AI File Classifier

A fully local, AI-powered media management system. Browse, tag, search, and organise photos and videos — all on your own machine. No cloud. No subscriptions.

Uses a vision LLM (via [Ollama](https://ollama.com)) to auto-tag files, generate descriptions, transcribe text in images (OCR), and flag low-quality or duplicate shots.

---

## Features

- 📸 **Photo & video library** — browse by date, type, or tag
- 🤖 **Local AI tagging** — auto-tags, descriptions, OCR text extraction via Ollama vision model
- 🔍 **Natural language search** — "dog at beach", "birthday 2024", "#receipt"
- 📱 **PWA mobile app** — install to home screen, works on local WiFi or Tailscale
- 📁 **Multi-folder support** — switch between media libraries
- 🎬 **Video support** — frame extraction + analysis via FFmpeg
- 🗑️ **AI deletion queue** — flag low-quality, blurry, duplicate shots for human review
- 📊 **Analytics** — AI processing stats, category breakdown, error log
- ⏱️ **Scheduled AI runs** — set a daily/hourly scan time
- 🔄 **Pull to refresh** on mobile
- 🌓 **Dark mode** UI

---

## Requirements

| Tool | Purpose | Install |
|---|---|---|
| Python 3.10+ | Runtime | [python.org](https://python.org) |
| Ollama | Local LLM server | [ollama.com](https://ollama.com) |
| FFmpeg | Video frame extraction | [ffmpeg.org](https://ffmpeg.org/download.html) — add to PATH |

---

## Quick start

```bat
git clone https://github.com/YOUR_USERNAME/ai-file-classifier.git
cd ai-file-classifier
setup.bat
python search.py
```

Open in your browser: **http://localhost:5050**

Mobile: open **http://YOUR_PC_IP:5050/mobile** on your phone → tap Share → Add to Home Screen.

---

## Setup details

`setup.bat` installs Python dependencies and pulls the default model (`qwen2.5-vl:7b`).

Or manually:

```bat
pip install -r requirements.txt
ollama pull qwen2.5-vl:7b
```

### Optional extras

```bat
pip install faster-whisper    # audio transcription inside videos
pip install pillow-heif       # HEIC/HEIF support (iPhone photos)
pip install send2trash        # safe trash instead of permanent delete
```

---

## How to add your folders

1. Open the Admin panel at **http://localhost:5050/admin**
2. Go to **Folders** → add the path to your media library
3. Go to **AI Queue** → click **Run AI on folder** to start tagging

---

## Command-line classifier (batch mode)

For bulk analysis and moving files into review folders:

```bat
python classifier.py --input "C:\Photos" --dry-run
python classifier.py --input "C:\Photos" --move
```

Output folders (files are **never deleted**, only moved):

```
AI Review/
  Keep/               ← good photos/videos
  Review/             ← uncertain, needs human check
  Probably Delete/    ← low value, accidental, junk
  Duplicates/         ← near-identical files
  Screenshots/        ← screen recordings, screenshots
  Low Quality/        ← blurry, dark, overexposed
```

---

## Model options

| Model | VRAM | Speed | Quality |
|---|---|---|---|
| `qwen2.5-vl:7b` | ~6 GB | Fast | ⭐⭐⭐⭐ |
| `llava:7b` | ~6 GB | Fast | ⭐⭐⭐ |
| `gemma3:12b` | ~10 GB | Medium | ⭐⭐⭐⭐ |
| `qwen2.5-vl:32b` | ~24 GB | Slow | ⭐⭐⭐⭐⭐ |

CPU-only works but expect ~5–20 seconds per file. An NVIDIA GPU (even an RTX 3060) speeds this up dramatically.

---

## Architecture

```
search.py          ← Flask web app (desktop + mobile PWA + admin panel)
classifier.py      ← Batch CLI classifier
src/               ← Scanner, analyser, organiser, reporter modules
classifier.db      ← SQLite database (auto-created)
```

Everything is local. No data leaves your machine.

---

## Remote access (optional)

To access from your phone over the internet, install [Tailscale](https://tailscale.com) on both devices. The app auto-detects local WiFi and prefers it when both devices are on the same network.

---

## Releasing a new version

Before pushing, bump the version:

```bat
python bump_version.py
```

This updates `APP_VERSION` in `search.py` to today's date (`Major.Minor.YYMM.Day`). The version is shown subtly in the UI and automatically busts the mobile PWA cache so users get the update immediately.

---

## Contributing

Issues and PRs welcome. The main app lives in a single file (`search.py`) — Flask routes, mobile PWA HTML/CSS/JS, desktop UI, AI worker, and service worker all in one place.

---

## License

MIT
