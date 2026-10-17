"""Keep photos small and limit how many are handled at once.

Render's starter plan has 512 MB of memory. Full-size phone photos (3-8 MB
each) served in parallel were enough to crash the server, so:
  * every photo is shrunk to at most MAX_SIDE pixels before it is stored,
  * older full-size photos are shrunk the first time they are requested,
  * only a few photos are read/sent at the same time (IMAGE_SLOTS).
"""
import ctypes
import io
import threading

from PIL import Image, ImageOps

MAX_SIDE = 1600              # longest edge in pixels
TOO_BIG_BYTES = 2 * 1024 * 1024   # re-encode anything heavier than this even if small in pixels
JPEG_QUALITY = 82

Image.MAX_IMAGE_PIXELS = 60_000_000   # refuse absurd images instead of exhausting memory

# Sync FastAPI routes run in a thread pool, so a thread semaphore caps how many
# photos are in memory at once. Others wait their turn instead of piling up.
IMAGE_SLOTS = threading.BoundedSemaphore(4)
_RESIZE_LOCK = threading.Lock()       # only one decode/re-encode at a time

CACHE_FOREVER = {"Cache-Control": "private, max-age=31536000, immutable"}
CACHE_SHORT = {"Cache-Control": "private, max-age=300"}


try:
    _LIBC = ctypes.CDLL("libc.so.6")
except Exception:          # Windows / macOS: not needed
    _LIBC = None


def limit_malloc_arenas():
    """glibc gives every thread its own memory arena; with 40 worker threads
    each touching multi-MB photos the process balloons and never shrinks.
    Two arenas is plenty for this app. Call once at start-up."""
    if _LIBC is not None:
        try:
            _LIBC.mallopt(-8, 2)   # M_ARENA_MAX = -8
        except Exception:
            pass


def release_memory():
    """Hand freed memory back to the OS after a big photo was handled."""
    if _LIBC is not None:
        try:
            _LIBC.malloc_trim(0)
        except Exception:
            pass


def _is_animated(im) -> bool:
    return bool(getattr(im, "is_animated", False)) and getattr(im, "n_frames", 1) > 1


def needs_shrink(content: bytes) -> bool:
    if not content:
        return False
    try:
        with Image.open(io.BytesIO(content)) as im:   # header only, cheap
            if _is_animated(im):
                return False
            return max(im.size) > MAX_SIDE or len(content) > TOO_BIG_BYTES
    except Exception:
        return False


def shrink(content: bytes, content_type: str = "") -> tuple[bytes, str]:
    """Return (bytes, content_type) no larger than MAX_SIDE. Never raises:
    anything Pillow can't handle is returned unchanged."""
    if not needs_shrink(content):
        return content, content_type
    with _RESIZE_LOCK:
        try:
            im = Image.open(io.BytesIO(content))
            if _is_animated(im):
                return content, content_type
            if im.format == "JPEG":
                # Decode at reduced scale straight away - big memory saving.
                im.draft("RGB", (MAX_SIDE, MAX_SIDE))
            im = ImageOps.exif_transpose(im)
            im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
            out = io.BytesIO()
            has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
            if has_alpha:
                im.save(out, "WEBP", quality=85, method=4)
                new_type = "image/webp"
            else:
                if im.mode != "RGB":
                    im = im.convert("RGB")
                im.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
                new_type = "image/jpeg"
            im.close()
            data = out.getvalue()
            if len(data) >= len(content):
                return content, content_type
            return data, new_type
        except Exception:
            return content, content_type
        finally:
            release_memory()


def ext_for(content_type: str, fallback: str) -> str:
    return {"image/jpeg": ".jpg", "image/webp": ".webp",
            "image/png": ".png", "image/gif": ".gif"}.get(content_type, fallback)
