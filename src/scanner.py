from pathlib import Path
from typing import List
import config


class Scanner:
    def scan(self, root: Path, skip_video: bool = False) -> List[Path]:
        extensions = set(config.SUPPORTED_IMAGES)
        if not skip_video:
            extensions |= config.SUPPORTED_VIDEOS

        files = []
        for f in root.rglob('*'):
            if not f.is_file():
                continue
            if f.suffix.lower() not in extensions:
                continue
            # Skip hidden files / system folders
            if any(part.startswith('.') for part in f.parts):
                continue
            files.append(f)

        return sorted(files)

    def is_image(self, path: Path) -> bool:
        return path.suffix.lower() in config.SUPPORTED_IMAGES

    def is_video(self, path: Path) -> bool:
        return path.suffix.lower() in config.SUPPORTED_VIDEOS
