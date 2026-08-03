"""
admin_app/app/views/widgets.py — reusable pieces for the item dialog:

- EyedropperOverlay: the "pick from screen" color tool from the original
  Swag Store. Freezes a screenshot of the whole screen, shows it
  full-screen with a magnified loupe following the cursor, and returns
  the hex of whatever pixel gets clicked (Esc cancels).
- PhotoStrip: horizontal row of photo thumbnails with add/remove and
  click-to-enlarge.

Both need Pillow. Everything here is pure UI — no API calls; the dialog
that hosts these decides what to do with the picked color / chosen files.
"""
import os
import tkinter as tk
from tkinter import ttk, filedialog

from PIL import Image, ImageTk, ImageGrab

from . import theme

LOUPE_SIZE = 120         # on-screen loupe box, px
LOUPE_CAPTURE = 15       # screen pixels sampled into the loupe (odd number)


class SpinnerLabel(ttk.Label):
    """Tiny non-blocking spinner for network activity."""
    FRAMES = ("◐", "◓", "◑", "◒")

    def __init__(self, parent, text="Loading", **kwargs):
        super().__init__(parent, text="", **kwargs)
        self.base_text = text
        self._index = 0
        self._job = None

    def start(self, text=None):
        if text is not None:
            self.base_text = text
        if self._job is None:
            self._tick()

    def _tick(self):
        if not self.winfo_exists():
            return
        self.configure(text=f"{self.FRAMES[self._index]} {self.base_text}")
        self._index = (self._index + 1) % len(self.FRAMES)
        self._job = self.after(110, self._tick)

    def stop(self, text=""):
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
        self._job = None
        self.configure(text=text)


def fade_in(window: tk.Toplevel, duration_ms: int = 140):
    """Best-effort dialog fade. Unsupported window managers simply show it."""
    try:
        window.attributes("-alpha", 0.0)
    except tk.TclError:
        return
    steps = max(1, duration_ms // 16)

    def step(index=0):
        if not window.winfo_exists():
            return
        alpha = min(1.0, (index + 1) / steps)
        try:
            window.attributes("-alpha", alpha)
        except tk.TclError:
            return
        if alpha < 1.0:
            window.after(16, step, index + 1)
    window.after_idle(step)


class EyedropperOverlay(tk.Toplevel):
    def __init__(self, parent, on_pick):
        super().__init__(parent)
        self.on_pick = on_pick
        # grab BEFORE going fullscreen so the overlay isn't in its own shot
        self.withdraw()
        self.update_idletasks()
        self.screenshot = ImageGrab.grab()
        self.deiconify()

        self.overrideredirect(True)
        try:
            self.attributes("-fullscreen", True)
        except tk.TclError:
            pass
        self.geometry(f"{self.screenshot.width}x{self.screenshot.height}+0+0")
        self.attributes("-topmost", True)

        self._tk_shot = ImageTk.PhotoImage(self.screenshot)
        self.canvas = tk.Canvas(self, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_image(0, 0, image=self._tk_shot, anchor="nw")

        self._loupe_img_id = None
        self._loupe_tk = None
        self._loupe_box = self.canvas.create_rectangle(
            0, 0, 0, 0, outline=theme.SAFETY, width=2, state="hidden")
        self._hex_bg = self.canvas.create_rectangle(
            0, 0, 0, 0, fill="#141618", outline=theme.SAFETY, state="hidden")
        self._hex_text = self.canvas.create_text(
            0, 0, text="", fill="#E8E5DC", font=("Consolas", 11, "bold"),
            state="hidden")

        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Button-1>", self._on_click)
        self.bind("<Escape>", lambda e: self._finish(None))
        self.focus_force()
        self.grab_set()

    def _pixel_at(self, x, y) -> str:
        x = max(0, min(x, self.screenshot.width - 1))
        y = max(0, min(y, self.screenshot.height - 1))
        px = self.screenshot.getpixel((x, y))
        if isinstance(px, int):
            px = (px, px, px)
        return "#{:02x}{:02x}{:02x}".format(*px[:3])

    def _on_motion(self, event):
        x, y = event.x, event.y
        half = LOUPE_CAPTURE // 2
        box = (max(0, x - half), max(0, y - half),
               min(self.screenshot.width, x + half + 1),
               min(self.screenshot.height, y + half + 1))
        region = self.screenshot.crop(box).resize(
            (LOUPE_SIZE, LOUPE_SIZE), Image.NEAREST)
        self._loupe_tk = ImageTk.PhotoImage(region)

        # keep the loupe on-screen: flip to the other side near edges
        lx = x + 24 if x + 24 + LOUPE_SIZE < self.screenshot.width \
            else x - 24 - LOUPE_SIZE
        ly = y + 24 if y + 24 + LOUPE_SIZE < self.screenshot.height \
            else y - 24 - LOUPE_SIZE
        if self._loupe_img_id is None:
            self._loupe_img_id = self.canvas.create_image(
                lx, ly, image=self._loupe_tk, anchor="nw")
        else:
            self.canvas.itemconfigure(self._loupe_img_id,
                                      image=self._loupe_tk, state="normal")
            self.canvas.coords(self._loupe_img_id, lx, ly)
        self.canvas.coords(self._loupe_box, lx, ly,
                           lx + LOUPE_SIZE, ly + LOUPE_SIZE)
        self.canvas.itemconfigure(self._loupe_box, state="normal")

        hexcol = self._pixel_at(x, y)
        self.canvas.coords(self._hex_text, lx + LOUPE_SIZE // 2,
                           ly + LOUPE_SIZE + 14)
        self.canvas.itemconfigure(self._hex_text, text=hexcol, state="normal")
        bbox = self.canvas.bbox(self._hex_text)
        if bbox:
            self.canvas.coords(self._hex_bg, bbox[0] - 6, bbox[1] - 3,
                               bbox[2] + 6, bbox[3] + 3)
            self.canvas.itemconfigure(self._hex_bg, state="normal")
        self.canvas.tag_raise(self._hex_text)

    def _on_click(self, event):
        self._finish(self._pixel_at(event.x, event.y))

    def _finish(self, hexcol):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        self.on_pick(hexcol)


class PhotoStrip(ttk.Frame):
    """Thumbnails of an item's photos + [+ Add] button. The host dialog
    supplies callbacks; this widget never talks to the API itself.

    on_add(paths: list[str])       — user chose local files to upload
    on_remove(image_id: int)       — user removed an existing photo
    load_image(filename, image_id) -> Image — host downloads the persistent
                                             DB-backed image endpoint
    """
    THUMB = 84

    def __init__(self, parent, *, on_add, on_remove, load_image):
        super().__init__(parent, style="Surface.TFrame")
        self.on_add = on_add
        self.on_remove = on_remove
        self.load_image = load_image
        self._thumb_refs = []          # keep PhotoImage refs alive
        self.row = ttk.Frame(self, style="Surface.TFrame")
        self.row.pack(fill="x")

    def set_photos(self, images: list[str], image_ids: list[int]):
        for w in self.row.winfo_children():
            w.destroy()
        self._thumb_refs.clear()

        for filename, image_id in zip(images, image_ids):
            cell = tk.Frame(self.row, bg=theme.SURFACE2, bd=0)
            cell.pack(side="left", padx=(0, 8))
            try:
                pil = self.load_image(filename, image_id)
                pil.thumbnail((self.THUMB, self.THUMB))
                tkimg = ImageTk.PhotoImage(pil)
                self._thumb_refs.append(tkimg)
                lbl = tk.Label(cell, image=tkimg, bg=theme.SURFACE2,
                               cursor="hand2")
                lbl.pack()
                lbl.bind("<Button-1>",
                         lambda e, f=filename, i=image_id: self._enlarge(f, i))
            except Exception:
                tk.Label(cell, text="⚠ can't\nload", bg=theme.SURFACE2,
                         fg=theme.MUTED, width=10, height=4).pack()
            rm = tk.Label(cell, text="✕ remove", bg=theme.SURFACE2,
                          fg=theme.RUST, cursor="hand2",
                          font=("Segoe UI", 8))
            rm.pack()
            rm.bind("<Button-1>", lambda e, i=image_id: self.on_remove(i))

        add = tk.Label(self.row, text="＋\nAdd photo", bg=theme.SURFACE2,
                       fg=theme.INK, width=10, height=4, cursor="hand2")
        add.pack(side="left")
        add.bind("<Button-1>", lambda e: self._choose())

    def _choose(self):
        paths = filedialog.askopenfilenames(
            parent=self, title="Choose photos",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.gif *.webp")])
        if paths:
            self.on_add(list(paths))

    def _enlarge(self, filename: str, image_id: int):
        try:
            pil = self.load_image(filename, image_id)
        except Exception:
            return
        win = tk.Toplevel(self)
        win.title(filename)
        win.configure(bg=theme.BAR)
        sw = win.winfo_screenwidth() - 120
        sh = win.winfo_screenheight() - 120
        pil.thumbnail((sw, sh))
        tkimg = ImageTk.PhotoImage(pil)
        lbl = tk.Label(win, image=tkimg, bg=theme.BAR)
        lbl.image = tkimg
        lbl.pack(padx=10, pady=10)
        win.bind("<Escape>", lambda e: win.destroy())
        lbl.bind("<Button-1>", lambda e: win.destroy())


def suggest_code(brand_code: str, category: str,
                 existing_codes: list[str]) -> str:
    """GS + 'Bags & Totes' -> GS-BAG-001 (next free number). Mirrors the
    original Swag Store's brand+category generator."""
    if not brand_code:
        return ""
    cat_part = "".join(ch for ch in category.upper() if ch.isalpha())[:3] \
        or "GEN"
    prefix = f"{brand_code}-{cat_part}-"
    top = 0
    for code in existing_codes:
        if code.upper().startswith(prefix):
            tail = code[len(prefix):]
            if tail.isdigit():
                top = max(top, int(tail))
    return f"{prefix}{top + 1:03d}"
