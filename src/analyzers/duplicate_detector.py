import imagehash
from src.database import Database
import config


class DuplicateDetector:
    def __init__(self, db: Database):
        self.db = db

    def find_duplicates(self) -> list[list[str]]:
        entries = self.db.get_all_phashes()
        if len(entries) < 2:
            return []

        hashes: list[tuple[str, imagehash.ImageHash]] = []
        for path, phash_str in entries:
            try:
                hashes.append((path, imagehash.hex_to_hash(phash_str)))
            except Exception:
                continue

        groups: list[list[str]] = []
        visited: set[str] = set()

        for i, (path_a, hash_a) in enumerate(hashes):
            if path_a in visited:
                continue
            group = [path_a]
            for path_b, hash_b in hashes[i + 1:]:
                if path_b not in visited and (hash_a - hash_b) <= config.DUPLICATE_THRESHOLD:
                    group.append(path_b)
                    visited.add(path_b)
            if len(group) > 1:
                visited.add(path_a)
                groups.append(group)

        # Mark duplicates in DB — keep the first file, flag the rest
        for i, group in enumerate(groups):
            group_id = f"dup_{i:04d}"
            for path in group[1:]:
                self.db.update_duplicate_group(path, group_id)

        return groups
