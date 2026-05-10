import base64
import json
import re
import subprocess
import tempfile
import requests
from pathlib import Path
from io import BytesIO

from PIL import Image
import imagehash
import config

VIDEO_PROMPT = """These are {n} frames sampled from a video (duration: {duration:.1f}s, size: {size:.1f} MB).
Analyze the video and respond ONLY with valid JSON — no other text.

{{
  "category": "home_video|screen_recording|downloaded_content|accidental|tutorial|event|nature|travel|unknown",
  "content": "brief description of video content",
  "quality": "excellent|good|poor|very_poor",
  "issues": ["list", "of", "issues"],
  "action": "keep|review|probably_delete",
  "confidence": 0-100,
  "reason": "one sentence explaining the suggested action"
}}

Possible issues: blurry, shaky, accidental_recording, no_clear_subject, screen_recording, very_short, downloaded_content, duplicate_risk.

Be honest. Flag accidental recordings, screen recordings, and low-value clips for deletion."""


class VideoAnalyzer:
    def __init__(self, ollama_host: str, model: str, transcribe_audio: bool = True):
        self.ollama_host = ollama_host
        self.model = model
        self._whisper = None

        if transcribe_audio:
            try:
                from faster_whisper import WhisperModel
                self._whisper = WhisperModel("tiny", device="cpu", compute_type="int8")
            except ImportError:
                pass

    def analyze(self, path: Path) -> dict:
        result: dict = {'file_type': 'video', 'action': 'review', 'confidence': 50}

        metadata = self._get_metadata(path)
        duration = metadata.get('duration', 0) if metadata else 0
        size_mb = path.stat().st_size / (1024 * 1024)

        frames = self._extract_frames(path, duration)
        if not frames:
            result['reason'] = "Could not extract video frames"
            return result

        # Use first frame for duplicate detection
        try:
            result['phash'] = str(imagehash.phash(frames[0]))
        except Exception:
            pass

        ai = self._ask_ollama(frames, duration, size_mb)
        if ai:
            result.update(ai)

        # Heuristic: flag very short clips for review
        if duration > 0 and duration < 3 and result.get('action') == 'keep':
            result['action'] = 'review'
            result.setdefault('issues', []).append('very_short')

        return result

    # ------------------------------------------------------------------
    def _get_metadata(self, path: Path) -> dict | None:
        try:
            out = subprocess.run(
                ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', str(path)],
                capture_output=True, text=True, timeout=30,
            )
            fmt = json.loads(out.stdout).get('format', {})
            return {'duration': float(fmt.get('duration', 0))}
        except Exception:
            return None

    def _extract_frames(self, path: Path, duration: float) -> list[Image.Image]:
        n = min(config.MAX_FRAMES_PER_VIDEO, max(1, int(duration / config.VIDEO_FRAME_INTERVAL))) if duration else 1
        frames: list[Image.Image] = []

        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                if duration > 0:
                    interval = duration / (n + 1)
                    for i in range(n):
                        ts = interval * (i + 1)
                        out = tmp / f"f{i:03d}.jpg"
                        subprocess.run(
                            ['ffmpeg', '-ss', str(ts), '-i', str(path),
                             '-vframes', '1', '-q:v', '2', '-vf', 'scale=512:-1',
                             str(out), '-y'],
                            capture_output=True, timeout=30,
                        )
                        if out.exists():
                            frames.append(Image.open(out).copy())
                else:
                    out = tmp / "f000.jpg"
                    subprocess.run(
                        ['ffmpeg', '-i', str(path),
                         '-vframes', '1', '-q:v', '2', '-vf', 'scale=512:-1',
                         str(out), '-y'],
                        capture_output=True, timeout=30,
                    )
                    if out.exists():
                        frames.append(Image.open(out).copy())
        except Exception:
            pass

        return frames

    def _frames_to_grid(self, frames: list[Image.Image]) -> Image.Image:
        if len(frames) == 1:
            return frames[0]
        cols = min(4, len(frames))
        rows = (len(frames) + cols - 1) // cols
        cw, ch = 256, 192
        grid = Image.new('RGB', (cols * cw, rows * ch))
        for i, f in enumerate(frames):
            grid.paste(f.resize((cw, ch), Image.LANCZOS), ((i % cols) * cw, (i // cols) * ch))
        return grid

    def _to_base64(self, img: Image.Image) -> str:
        buf = BytesIO()
        img.save(buf, format='JPEG', quality=80)
        return base64.b64encode(buf.getvalue()).decode()

    def _ask_ollama(self, frames: list[Image.Image], duration: float, size_mb: float) -> dict | None:
        try:
            grid = self._frames_to_grid(frames)
            prompt = VIDEO_PROMPT.format(n=len(frames), duration=duration, size=size_mb)
            resp = requests.post(
                f"{self.ollama_host}/api/chat",
                json={
                    "model": self.model,
                    "messages": [{
                        "role": "user",
                        "content": prompt,
                        "images": [self._to_base64(grid)],
                    }],
                    "stream": False,
                    "options": {"temperature": 0.1},
                },
                timeout=config.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            content = resp.json()['message']['content']
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception:
            pass
        return None
