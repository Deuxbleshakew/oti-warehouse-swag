"""Browsable and editable inventory adjustment history."""
import threading
import tkinter as tk
from tkinter import ttk

from . import theme
from .widgets import SpinnerLabel, fade_in
from ..services.api_client import ApiClient, ApiError, SessionExpired


class InventoryHistoryView(ttk.Frame):
    def __init__(self, parent, api: ApiClient):
        super().__init__(parent)
        self.api = api
        self.rows: list[dict] = []
        self._build()
        self.refresh()

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        ttk.Label(top, text="INVENTORY HISTORY", style="Head.TLabel").pack(side="left")
        self.count_lbl = ttk.Label(top, text="", style="Muted.TLabel")
        self.count_lbl.pack(side="left", padx=10)
        self.spinner = SpinnerLabel(top, text="Loading", style="Muted.TLabel")
        self.spinner.pack(side="left")
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="right")
        self.edit_btn = ttk.Button(top, text="Edit Entry…", command=self._edit,
                                   state="disabled")
        self.edit_btn.pack(side="right", padx=6)

        wrap = ttk.Frame(self)
        wrap.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        cols = ("date", "code", "item", "delta", "reason", "user", "source")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings",
                                 selectmode="browse")
        headings = {
            "date": ("Date", 135), "code": ("Code", 110),
            "item": ("Item", 210), "delta": ("Change", 70),
            "reason": ("Reason", 280), "user": ("User", 110),
            "source": ("Source", 90),
        }
        for col, (label, width) in headings.items():
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width,
                             anchor="e" if col == "delta" else "w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.tag_configure("negative", foreground=theme.RUST)
        self.tree.tag_configure("positive", foreground=theme.OK)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.edit_btn.configure(
            state="normal" if self.tree.selection() else "disabled"))
        self.tree.bind("<Double-1>", lambda e: self._edit())

    def refresh(self):
        self.spinner.start("Loading history")

        def work():
            try:
                rows = self.api.inventory_transactions()
            except SessionExpired:
                return
            except ApiError as exc:
                self.after(0, lambda: self._failed(exc))
                return
            self.after(0, lambda: self._set_rows(rows))
        threading.Thread(target=work, daemon=True).start()

    def _failed(self, exc):
        self.spinner.stop()
        theme.show_error(self, "Load failed", str(exc))

    def _set_rows(self, rows):
        self.spinner.stop()
        self.rows = rows
        keep = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            tag = "negative" if row["delta"] < 0 else "positive"
            self.tree.insert("", "end", iid=str(row["id"]), values=(
                row["created_at"][:16].replace("T", " "), row["item_code"],
                row["item_name"], f"{row['delta']:+d}", row["reason"],
                row.get("user") or "—", row["source"]), tags=(tag,))
        self.count_lbl.configure(text=f"{len(rows)} entries")
        if keep and self.tree.exists(keep[0]):
            self.tree.selection_set(keep[0])

    def _selected(self):
        selected = self.tree.selection()
        if not selected:
            return None
        txid = int(selected[0])
        return next((row for row in self.rows if row["id"] == txid), None)

    def _edit(self):
        row = self._selected()
        if not row:
            return
        win = tk.Toplevel(self)
        win.title(f"Edit inventory entry #{row['id']}")
        win.configure(bg=theme.PAPER)
        win.transient(self.winfo_toplevel())
        win.grab_set()
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=f"{row['item_code']} · {row['item_name']}",
                  font=theme.FONT_BOLD).grid(row=0, column=0, columnspan=2,
                                             sticky="w")
        ttk.Label(frm, text="Change").grid(row=1, column=0, sticky="w",
                                            pady=(10, 3), padx=(0, 10))
        delta = tk.StringVar(value=str(row["delta"]))
        ttk.Entry(frm, textvariable=delta, width=12).grid(row=1, column=1,
                                                          sticky="w", pady=(10, 3))
        ttk.Label(frm, text="Reason").grid(row=2, column=0, sticky="w",
                                            pady=3, padx=(0, 10))
        reason = tk.StringVar(value=row["reason"])
        ttk.Entry(frm, textvariable=reason, width=42).grid(row=2, column=1,
                                                           sticky="ew", pady=3)
        override = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="Allow negative stock if this correction requires it",
                        variable=override).grid(row=3, column=0, columnspan=2,
                                                sticky="w", pady=(6, 0))
        err = ttk.Label(frm, text="", style="Muted.TLabel", foreground=theme.RUST,
                        wraplength=360)
        err.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        buttons = ttk.Frame(frm)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))

        def save():
            try:
                value = int(delta.get().strip())
            except ValueError:
                err.configure(text="Change must be a whole number.")
                return
            if not reason.get().strip():
                err.configure(text="A reason is required.")
                return
            for child in buttons.winfo_children():
                child.configure(state="disabled")

            def work():
                try:
                    self.api.update_inventory_transaction(
                        row["id"], value, reason.get().strip(), override.get())
                except SessionExpired:
                    return
                except ApiError as exc:
                    self.after(0, lambda: err.configure(text=str(exc)))
                    return
                self.after(0, lambda: (win.destroy(), self.refresh(),
                                       self.event_generate("<<InventoryChanged>>")))
            threading.Thread(target=work, daemon=True).start()
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", style="Primary.TButton",
                   command=save).pack(side="right", padx=(0, 8))
        fade_in(win)
