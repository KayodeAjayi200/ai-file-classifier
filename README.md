# AI File Classifier

> **Your personal AI photo & video library. Runs entirely on your PC. No cloud. No subscription. No data leaving your machine.**

AI File Classifier automatically tags, describes, and organises your photos and videos using a local AI vision model. Browse everything from your phone or desktop — search in plain English, filter by date or type, and let AI do the heavy lifting.

---

## What it does

| | |
|---|---|
| 🤖 **AI auto-tagging** | Every photo and video is described, tagged, and categorised automatically |
| 🔍 **Smart search** | Search in plain English — *"dog at the beach"*, *"birthday cake"*, *"receipt"* |
| 📱 **Mobile app** | Full photo library on your phone over WiFi — install as a home screen app |
| 📸 **Date & type views** | Browse by month, filter to photos, videos, or documents in one tap |
| 🗂️ **Multi-folder library** | Add as many folders as you like — each gets its own AI categories |
| 🎬 **Video support** | Videos are analysed frame-by-frame and fully searchable |
| 🧹 **Duplicate & junk detection** | AI flags blurry shots, near-duplicates, accidental captures, and screenshots |
| 🗑️ **Safe review & delete** | Review AI suggestions before anything is removed — you stay in control |
| 🌓 **Dark mode** | Clean, modern UI on desktop and mobile |
| 🔒 **100% private** | Everything runs on your own hardware. Nothing is sent anywhere. |

---

## Install

### Step 1 — Prerequisites

Install these once. They take a few minutes.

| | Download | Notes |
|---|---|---|
| **Python 3.10+** | [python.org](https://www.python.org/downloads/) | ✅ Tick **"Add Python to PATH"** during install |
| **Ollama** | [ollama.com](https://ollama.com) | Runs the AI model locally |
| **FFmpeg** *(optional)* | [ffmpeg.org](https://ffmpeg.org/download.html) | Only needed for video analysis |

### Step 2 — Download & install the app

1. Download **AIFileClassifier-Setup.exe** from the [Releases](../../releases/latest) page
2. Run the installer — it installs to **Program Files** and creates Start Menu + Desktop shortcuts
3. Accept the UAC prompt (needed to write to Program Files)

> Python packages are installed automatically during setup.

### Step 3 — Pull an AI model

Open a terminal and run:

```
ollama pull qwen2.5-vl:7b
```

This downloads the default vision model (~5 GB). Only needed once.

### Step 4 — Launch

Click **AI File Classifier** from your Start Menu or Desktop. A tray icon appears — click it to open the app in your browser.

---

## Add your photos & videos

1. Open the app → click **Admin** in the top bar
2. Go to **Folders** → paste in the path to your media folder (e.g. `C:\Users\You\Pictures`)
3. Go to **AI Queue** → click **Run AI** to start tagging

The AI works quietly in the background. Results appear in your library as each file is processed.

---

## Mobile setup

1. Make sure your phone is on the same WiFi as your PC
2. Open your mobile browser and go to the address shown in the app (e.g. `http://192.168.1.x:5050/mobile`)
3. Tap **Share → Add to Home Screen** (iOS) or **Install App** (Android)

You now have a full photo library on your phone — search, browse, delete, and swipe through everything, all locally.

> For access outside your home network, install [Tailscale](https://tailscale.com) on both devices.

---

## Choosing a model

The default model (`qwen2.5-vl:7b`) works well on most machines. If you have more VRAM or want faster/better results:

| Model | VRAM needed | Speed | Best for |
|---|---|---|---|
| `qwen2.5-vl:7b` *(default)* | ~6 GB | Fast | Most laptops & desktops |
| `llava:7b` | ~6 GB | Fast | Lightweight alternative |
| `gemma3:12b` | ~10 GB | Medium | Better descriptions |
| `qwen2.5-vl:32b` | ~24 GB | Slow | Maximum accuracy |

CPU-only mode works — expect ~5–20 seconds per file. A dedicated GPU (even an RTX 3060) makes a big difference on large libraries.

To switch models, open **Admin → Settings** and enter the model name.

---

## Set up with an AI agent

Want your AI assistant (Claude, Copilot, ChatGPT, Cursor, etc.) to set this up for you? Copy and paste the prompt below:

<details>
<summary>📋 Click to copy — AI agent setup prompt</summary>

```
Set up AI File Classifier on my Windows PC.

Steps:
1. Check if Python 3.10+ is installed. If not, tell me to install it from https://python.org (tick "Add Python to PATH").
2. Check if Ollama is installed. If not, tell me to install it from https://ollama.com and run: ollama pull qwen2.5-vl:7b
3. Check if FFmpeg is installed and on PATH. If not, tell me to download it from https://ffmpeg.org/download.html and add the bin folder to PATH.
4. Download the latest AIFileClassifier-Setup.exe from https://github.com/KayodeAjayi200/ai-file-classifier/releases/latest
5. Run the installer silently if possible, or guide me through it.
6. Once installed, verify the app starts by checking if http://localhost:5050 responds.
7. Tell me the local IP address to use on my phone (e.g. http://192.168.x.x:5050/mobile).
8. Confirm everything is working and summarise what was installed.
```

</details>

---

## Privacy

AI File Classifier runs **entirely on your machine**:

- No account required
- No internet connection needed (after setup)
- No telemetry, analytics, or tracking
- All AI inference runs locally via Ollama
- Your files and metadata never leave your PC

---

## License

MIT — free to use, modify, and distribute.
