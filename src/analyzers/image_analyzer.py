import base64
import json
import re
import requests
from pathlib import Path
from io import BytesIO

from PIL import Image, ImageFilter
import imagehash
import config

# Optional: OpenCV for better blur detection
try:
    import cv2
    import numpy as np
    _CV2 = True
except ImportError:
    _CV2 = False

# Optional: HEIC/HEIF support
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

ANALYSIS_PROMPT = """Analyze this image and respond ONLY with valid JSON — no other text.

{
  "category": "selfie|group_photo|family|friends|celebration|travel|food|pets|outdoors|home|screenshot|screen_recording|document|receipt|meme|whatsapp_junk|social_save|wallpaper|junk|other",
  "subject": "brief description of main subject (max 10 words)",
  "quality": "excellent|good|poor|very_poor",
  "issues": ["list", "of", "issues"],
  "action": "keep|review|probably_delete",
  "confidence": 0-100,
  "reason": "one sentence explaining the suggested action"
}

Category guide:
- selfie: person taking photo of themselves
- group_photo: multiple people together
- family: family moments/gatherings
- friends: social moments with friends
- celebration: birthday, party, wedding, graduation, event
- travel: holiday, trip, sightseeing, landmarks
- food: food, drinks, restaurants
- pets: animals, dogs, cats
- outdoors: nature, parks, scenery, streets
- home: inside the house, furniture, rooms
- screenshot: screenshot of a phone or computer screen
- screen_recording: recorded screen content
- document: ID, passport, form, letter, certificate, handwritten note
- receipt: receipt, invoice, bill
- meme: meme or joke image
- whatsapp_junk: forwarded image, chain message, low-effort share
- social_save: saved from Instagram, TikTok, Twitter etc
- wallpaper: wallpaper or stock image
- junk: accidental, empty, blurry, or completely useless image
- other: anything that doesn't fit above

Possible issues: blurry, overexposed, underexposed, eyes_closed, bad_angle, partial_subject, accidental_shot, no_clear_subject, very_dark, empty, duplicate_likely.

Action rules:
- probably_delete: screenshots, memes, whatsapp_junk, social_save, junk, receipts older than context, blurry photos with no value
- review: borderline quality, might be useful
- keep: genuine personal memories, important documents, good quality photos

Be practical. Flag clutter aggressively."""


class ImageAnalyzer:
    def __init__(self, ollama_host: str, model: str, blur_threshold: float = config.BLUR_THRESHOLD):
        self.ollama_host = ollama_host
        self.model = model
        self.blur_threshold = blur_threshold

    def analyze(self, path: Path) -> dict:
        result: dict = {'file_type': 'image', 'action': 'review', 'confidence': 50}

        try:
            img = Image.open(path).convert('RGB')
        except Exception as e:
            result['reason'] = f"Could not open image: {e}"
            return result

        # Blur score
        blur_score = self._blur_score(img)
        result['blur_score'] = blur_score
        result['is_blurry'] = blur_score < self.blur_threshold

        # Perceptual hash for duplicate detection
        try:
            result['phash'] = str(imagehash.phash(img))
        except Exception:
            pass

        # Resize before sending to Ollama (saves tokens / time)
        img_small = self._resize(img)

        ai = self._ask_ollama(img_small)
        if ai:
            result.update(ai)

        # Enforce blur override
        if result['is_blurry']:
            result.setdefault('issues', [])
            if 'blurry' not in result['issues']:
                result['issues'].append('blurry')
            if result.get('quality') not in ('poor', 'very_poor'):
                result['quality'] = 'poor'

        return result

    # ------------------------------------------------------------------
    def _blur_score(self, img: Image.Image) -> float:
        if _CV2:
            gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
            return float(cv2.Laplacian(gray, cv2.CV_64F).var())
        # Fallback: PIL edge detection
        gray = img.convert('L')
        edges = gray.filter(ImageFilter.FIND_EDGES)
        pixels = list(edges.getdata())
        return float(sum(pixels) / max(len(pixels), 1))

    def _resize(self, img: Image.Image, max_size: int = 1024) -> Image.Image:
        w, h = img.size
        if max(w, h) > max_size:
            ratio = max_size / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        return img

    def _to_base64(self, img: Image.Image) -> str:
        buf = BytesIO()
        img.save(buf, format='JPEG', quality=85)
        return base64.b64encode(buf.getvalue()).decode()

    def _ask_ollama(self, img: Image.Image) -> dict | None:
        try:
            resp = requests.post(
                f"{self.ollama_host}/api/chat",
                json={
                    "model": self.model,
                    "messages": [{
                        "role": "user",
                        "content": ANALYSIS_PROMPT,
                        "images": [self._to_base64(img)],
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
