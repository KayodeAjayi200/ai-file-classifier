import shutil
from pathlib import Path
from rich.console import Console
import config

console = Console()


class Organizer:
    def __init__(self, output_dir: str, dry_run: bool = True):
        self.output_dir = Path(output_dir)
        self.dry_run = dry_run

    def organize_one(self, file_info: dict) -> str | None:
        """Move a single file immediately after analysis. Returns new path or None."""
        if self.dry_run or not file_info:
            return None
        self._create_folders()
        folder = self._folder_for(file_info)
        return self._move(file_info['path'], folder)

    def organize(self, results: list[dict]):
        if not self.dry_run:
            self._create_folders()

        for file_info in results:
            folder = self._folder_for(file_info)
            if self.dry_run:
                console.print(f"  [dim]{folder:<20}[/] [dim]{Path(file_info['path']).name}[/dim]", highlight=False)
            else:
                self._move(file_info['path'], folder)

        if self.dry_run:
            console.print("\n[yellow]Dry run — no files moved.[/yellow] Add [cyan]--move[/cyan] to apply.")

    def _folder_for(self, info: dict) -> str:
        # Priority 1: duplicates
        if info.get('duplicate_group'):
            return config.REVIEW_FOLDERS['duplicates']

        action = info.get('action', 'review')
        category = info.get('category', '')
        is_blurry = info.get('is_blurry') or info.get('quality') in ('poor', 'very_poor')

        # Priority 2: junk categories always go to Probably Delete
        if category in ('meme', 'whatsapp_junk', 'social_save', 'junk', 'wallpaper') and action != 'keep':
            return config.REVIEW_FOLDERS['probably_delete']

        # Priority 3: probably_delete action
        if action == 'probably_delete':
            return config.REVIEW_FOLDERS['probably_delete']

        # Priority 4: low quality
        if is_blurry and action != 'keep':
            return config.REVIEW_FOLDERS['low_quality']

        # Priority 5: review action
        if action == 'review':
            return config.REVIEW_FOLDERS['review']

        # Priority 6: keep — sort into category folder
        return config.CATEGORY_FOLDERS.get(category, 'Personal')

    def _create_folders(self):
        for name in set(config.CATEGORY_FOLDERS.values()) | set(config.REVIEW_FOLDERS.values()):
            (self.output_dir / name).mkdir(parents=True, exist_ok=True)

    def _move(self, src_path: str, folder_name: str):
        src = Path(src_path)
        if not src.exists():
            return
        dest_dir = self.output_dir / folder_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name

        counter = 1
        while dest.exists():
            dest = dest_dir / f"{src.stem}_{counter}{src.suffix}"
            counter += 1

        try:
            shutil.move(str(src), str(dest))
            return str(dest)
        except Exception as e:
            console.print(f"[red]  Could not move {src.name}: {e}[/red]")
            return None

