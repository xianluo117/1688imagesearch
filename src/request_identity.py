"""Chrome identities shared by SKU queries and proxy diagnostics only."""
import hashlib
import random

CHROME_PROFILES = ("chrome120", "chrome123", "chrome124", "chrome131")


def chrome_identity(task_id: str | None = None) -> str:
    """Random per standalone session; stable across retries of a random task ID."""
    if task_id is None:
        return random.choice(CHROME_PROFILES)
    index = int.from_bytes(hashlib.sha256(task_id.encode()).digest()[:8], "big")
    return CHROME_PROFILES[index % len(CHROME_PROFILES)]
