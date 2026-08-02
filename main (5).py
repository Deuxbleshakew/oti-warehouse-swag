"""
admin_app/app/views/theme.py — one place for colors, fonts, and small
shared widgets so every view looks the same and OpsDeck can restyle the
whole app by swapping this module.

Dark "night shift" palette — same safety-yellow accent as before, but on
charcoal instead of paper, matching the original Swag Store look.
"""
import tkinter as tk
from tkinter import ttk

INK = "#E8E5DC"        # main text (light on dark now)
PAPER = "#1E2124"      # app background
SURFACE = "#282C30"    # cards / panes
SURFACE2 = "#31363B"   # inputs, hover
SAFETY = "#F2B705"
SAFETY_INK = "#3A2E00"
RUST = "#E06248"       # brightened for dark-bg contrast
OK = "#6FBF8A"
LINE = "#3D4349"
BAR = "#141618"     # top header bar, darker than the app bg
MUTED = "#9A958A"

FONT_BASE = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_HEAD = ("Segoe UI", 13, "bold")
FONT_MONO = ("Consolas", 9)
FONT_SMALL = ("Segoe UI", 9)


def apply_theme(root: tk.Misc) -> ttk.Style:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=PAPER, foreground=INK, font=FONT_BASE,
                    fieldbackground=SURFACE2, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, troughcolor=PAPER,
                    selectbackground=SAFETY, selectforeground=SAFETY_INK,
                    insertcolor=INK)
    style.configure("TFrame", background=PAPER)
    style.configure("Surface.TFrame", background=SURFACE)
    style.configure("TLabel", background=PAPER, foreground=INK)
    style.configure("Surface.TLabel", background=SURFACE, foreground=INK)
    style.configure("Muted.TLabel", background=PAPER, foreground=MUTED,
                    font=FONT_SMALL)
    style.configure("SurfaceMuted.TLabel", background=SURFACE,
                    foreground=MUTED, font=FONT_SMALL)
    style.configure("Head.TLabel", background=PAPER, font=FONT_HEAD)

    style.configure("TNotebook", background=PAPER, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(16, 8), font=FONT_BOLD,
                    background=PAPER, foreground=MUTED)
    style.map("TNotebook.Tab",
              background=[("selected", SURFACE)],
              foreground=[("selected", SAFETY)])

    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE,
                    foreground=INK, rowheight=26, font=FONT_BASE,
                    bordercolor=LINE)
    style.configure("Treeview.Heading", font=FONT_BOLD, background=SURFACE2,
                    foreground=INK, bordercolor=LINE)
    style.map("Treeview.Heading", background=[("active", SURFACE2)])
    style.map("Treeview", background=[("selected", SAFETY)],
              foreground=[("selected", SAFETY_INK)])

    style.configure("TButton", padding=(12, 6), background=SURFACE2,
                    foreground=INK, bordercolor=LINE)
    style.map("TButton", background=[("active", "#3D4349"),
                                     ("pressed", BAR)],
              relief=[("pressed", "sunken"), ("!pressed", "flat")])
    style.configure("Primary.TButton", background=SAFETY,
                    foreground=SAFETY_INK, font=FONT_BOLD)
    style.map("Primary.TButton",
              background=[("pressed", "#C79604"), ("active", "#E0A904"),
                          ("disabled", "#5C5747")],
              foreground=[("disabled", "#8C8878")],
              relief=[("pressed", "sunken"), ("!pressed", "flat")])
    style.configure("Danger.TButton", background=RUST, foreground="#2B0F08",
                    font=FONT_BOLD)
    style.map("Danger.TButton",
              background=[("pressed", "#A94432"), ("active", "#C9553E")],
              relief=[("pressed", "sunken"), ("!pressed", "flat")])

    style.configure("TEntry", padding=4, foreground=INK)
    style.configure("TSpinbox", arrowcolor=INK, foreground=INK)
    style.configure("TCheckbutton", background=PAPER, foreground=INK)
    style.map("TCheckbutton", background=[("active", PAPER)])
    style.configure("Vertical.TScrollbar", background=SURFACE2,
                    troughcolor=PAPER, arrowcolor=MUTED)

    # tk (non-ttk) widget defaults so plain Entries/Toplevels match
    root.option_add("*Toplevel.background", PAPER)
    root.option_add("*Canvas.background", SURFACE)
    return style


def status_color(status: str) -> str:
    return {"pending": MUTED, "approved": OK, "picking": SAFETY,
            "rejected": RUST, "fulfilled": INK}.get(status, INK)


def show_error(parent, title: str, message: str):
    from tkinter import messagebox
    messagebox.showerror(title, message, parent=parent)


def show_info(parent, title: str, message: str):
    from tkinter import messagebox
    messagebox.showinfo(title, message, parent=parent)
