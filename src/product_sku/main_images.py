"""Ordered main-gallery links from verified product roots; no image downloads."""
from .specification_images import safe_image_url


def main_gallery_images(verified_roots: list[dict], product_id: str) -> list[str]:
    images: list[str] = []
    seen: set[str] = set()
    for root in verified_roots:
        fields = root
        for key in ("result", "data", "gallery", "fields"):
            fields = fields.get(key) if isinstance(fields, dict) else None
        if not isinstance(fields, dict) or str(fields.get("offerId")) != product_id:
            continue
        if any(str(fields[key]) != product_id for key in ("productId", "itemId") if key in fields):
            continue
        candidates = fields.get("mainImage")
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.startswith("//"):
                candidate = "https:" + candidate
            image = safe_image_url(candidate)
            if image is not None and image not in seen:
                seen.add(image)
                images.append(image)
    return images
