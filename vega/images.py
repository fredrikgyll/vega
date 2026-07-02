"""Turn Sanity asset refs into Vega Scene CDN image URLs.

Posters arrive as refs like ``image-<asset>-<width>x<height>-<ext>``. Vega
Scene's Sanity project serves them from::

    https://cdn.sanity.io/images/f6l4um99/production/<asset>-<width>x<height>.<ext>

The trailing ``?w=&h=&fit=crop&auto=format`` params let the CDN resize and pick
an efficient format for us.
"""

from __future__ import annotations

SANITY_PROJECT = "f6l4um99"
SANITY_DATASET = "production"
_CDN_BASE = f"https://cdn.sanity.io/images/{SANITY_PROJECT}/{SANITY_DATASET}"


def sanity_image_url(ref: str | None, *, width: int | None = None, height: int | None = None) -> str | None:
    """Build a CDN URL from a poster ref, or return ``None`` if unparseable."""
    if not ref or not ref.startswith("image-"):
        return None
    try:
        asset, dims, ext = ref.removeprefix("image-").rsplit("-", 2)
    except ValueError:
        return None
    url = f"{_CDN_BASE}/{asset}-{dims}.{ext}"
    params = []
    if width:
        params.append(f"w={width}")
    if height:
        params.append(f"h={height}")
    if params:
        params.append("fit=crop")
    params.append("auto=format")
    return f"{url}?{'&'.join(params)}"
