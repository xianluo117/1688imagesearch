"""Chrome identities shared by SKU queries and proxy diagnostics only."""
import hashlib

CHROME_PROFILES = ("chrome120", "chrome123", "chrome124", "chrome131")


def chrome_identity(account_id: str | None = None) -> str:
    """Stable per executing account; standalone clients use a fixed default."""
    if account_id is None:
        return CHROME_PROFILES[0]
    index = int.from_bytes(hashlib.sha256(account_id.encode()).digest()[:8], "big")
    return CHROME_PROFILES[index % len(CHROME_PROFILES)]
