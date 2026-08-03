"""
admin_app/app/views/inventory_view.py — the Inventory tab.

Item list (including inactive items, dimmed), add/edit item dialogs, and
stock adjustment. Stock is deliberately NOT a field on the edit dialog —
it can only move through the Adjust Stock dialog, which requires a
reason, because the backend logs every adjustment as an inventory
transaction. Same threading rules as orders_view.
"""
import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from . import theme
from .widgets import (EyedropperOverlay, PhotoStrip, suggest_code,
                      SpinnerLabel, fade_in)
from ..services.api_client import ApiClient, ApiError, SessionExpired

BRANDS = [("Oticon", "OT"), ("Government Services", "GS"),
          ("Bernafon-Demant Group", "BDG"), ("Philips", "PHI"),
          ("Other…", "")]

ITEM_FIELDS = [
    # (key, label, width) — brand, color and code get dedicated widgets
    ("name", "Name *", 34),
    ("category", "Category", 22),
    ("measures", "Measures", 18),
    ("location", "Bin / shelf", 16),
    ("color_name", "Color name", 16),
    ("reorder_threshold", "Reorder at", 8),
    ("cost", "Unit cost", 10),
    ("description", "Description", 44),
]


class InventoryView(ttk.Frame):
    def __init__(self, parent, api: ApiClient):
        super().__init__(parent)
        self.api = api
        self.items: list[dict] = []
        self._build()
        self.refresh()

    # ---- UI ------------------------------------------------------------------
    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        ttk.Label(top, text="INVENTORY", style="Head.TLabel").pack(side="left")
        self.count_lbl = ttk.Label(top, text="", style="Muted.TLabel")
        self.count_lbl.pack(side="left", padx=10)
        self.spinner = SpinnerLabel(top, text="Loading", style="Muted.TLabel")
        self.spinner.pack(side="left")

        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="right")
        self.delete_btn = ttk.Button(top, text="Delete", style="Danger.TButton",
                                     command=self._delete_item, state="disabled")
        self.delete_btn.pack(side="right", padx=6)
        self.count_btn = ttk.Button(top, text="Resolve Count…",
                                    command=self._resolve_count_dialog,
                                    state="disabled")
        self.count_btn.pack(side="right", padx=6)
        self.adjust_btn = ttk.Button(top, text="Adjust Stock…",
                                     command=self._adjust_dialog,
                                     state="disabled")
        self.adjust_btn.pack(side="right", padx=6)
        self.edit_btn = ttk.Button(top, text="Edit…", command=self._edit_dialog,
                                   state="disabled")
        self.edit_btn.pack(side="right", padx=6)
        ttk.Button(top, text="New Item…", style="Primary.TButton",
                   command=self._new_dialog).pack(side="right", padx=6)
        self.low_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Needs attention only", variable=self.low_only,
                        command=lambda: self._set_items(self.items)).pack(
            side="right", padx=10)

        wrap = ttk.Frame(self)
        wrap.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        cols = ("code", "name", "category", "qty", "reorder", "count", "location", "active")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings",
                                 selectmode="browse")
        headings = {"code": ("Code", 120), "name": ("Name", 220),
                    "category": ("Category", 140), "qty": ("On hand", 120),
                    "reorder": ("Reorder at", 80),
                    "count": ("Count", 70),
                    "location": ("Bin", 90), "active": ("Active", 60)}
        for c, (label, width) in headings.items():
            self.tree.heading(c, text=label)
            anchor = "e" if c in ("qty", "reorder", "count") else "w"
            self.tree.column(c, width=width, anchor=anchor)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")

        self.tree.tag_configure("low", foreground=theme.RUST)
        self.tree.tag_configure("not_counted", foreground=theme.SAFETY)
        self.tree.tag_configure("inactive", foreground=theme.MUTED)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda e: self._edit_dialog())

    # ---- data ---------------------------------------------------------------
    def refresh(self):
        self.spinner.start("Loading inventory")
        def work():
            try:
                items = self.api.all_items()
            except SessionExpired:
                return
            except ApiError as e:
                self.after(0, lambda: (self.spinner.stop(),
                                       theme.show_error(self, "Load failed", str(e))))
                return
            self.after(0, lambda: self._set_items(items))
        threading.Thread(target=work, daemon=True).start()

    def _set_items(self, items: list[dict]):
        if not self.winfo_exists():
            return
        self.spinner.stop()
        self.items = items
        keep = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        low_count = sum(1 for it in items if it["active"]
                        and it.get("inventory_counted", True)
                        and it["qty_on_hand"] <= it["reorder_threshold"])
        not_counted_count = sum(1 for it in items if it["active"]
                                and not it.get("inventory_counted", True))
        show = items
        if self.low_only.get():
            show = [it for it in items if it["active"]
                    and (not it.get("inventory_counted", True) or
                         it["qty_on_hand"] <= it["reorder_threshold"])]
        for it in show:
            tags = []
            if not it["active"]:
                tags.append("inactive")
            elif not it.get("inventory_counted", True):
                tags.append("not_counted")
            elif it["qty_on_hand"] <= it["reorder_threshold"]:
                tags.append("low")
            qty_display = it["qty_on_hand"] if it.get("inventory_counted", True) else "Not Counted Yet"
            self.tree.insert("", "end", iid=str(it["id"]), values=(
                it["code"], it["name"], it["category"], qty_display,
                it["reorder_threshold"],
                it.get("open_count_requests", 0) or "",
                it["location"], "yes" if it["active"] else "no"), tags=tags)
        txt = f"{len(show)} of {len(items)} items" \
            if self.low_only.get() else f"{len(items)} items"
        if low_count:
            txt += f" · {low_count} at/below reorder point"
        if not_counted_count:
            txt += f" · {not_counted_count} not counted yet"
        count_requests = sum(it.get("open_count_requests", 0) for it in items)
        if count_requests:
            txt += f" · {count_requests} recount request{'s' if count_requests != 1 else ''}"
        self.count_lbl.configure(text=txt)
        if keep and self.tree.exists(keep[0]):
            self.tree.selection_set(keep[0])
        else:
            self._on_select()

    def _selected_item(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            return None
        iid = int(sel[0])
        return next((i for i in self.items if i["id"] == iid), None)

    def _on_select(self, _evt=None):
        has = bool(self.tree.selection())
        state = "normal" if has else "disabled"
        self.edit_btn.configure(state=state)
        self.adjust_btn.configure(state=state)
        self.delete_btn.configure(state=state)
        item = self._selected_item()
        self.count_btn.configure(state="normal" if item and item.get("open_count_requests", 0) else "disabled")

    # ---- dialogs -------------------------------------------------------------
    def _new_dialog(self):
        self._item_dialog(None)

    def _edit_dialog(self):
        item = self._selected_item()
        if item:
            self._item_dialog(item)

    def _item_dialog(self, item: dict | None):
        win = tk.Toplevel(self)
        win.title("New Item" if item is None else f"Edit {item['code']}")
        win.configure(bg=theme.PAPER)
        win.transient(self.winfo_toplevel())
        win.grab_set()
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)

        r = 0

        # ---- brand + code (auto-suggested, manual always wins) --------------
        ttk.Label(frm, text="Brand").grid(row=r, column=0, sticky="w",
                                           pady=3, padx=(0, 10))
        brand_row = ttk.Frame(frm)
        brand_row.grid(row=r, column=1, sticky="w", pady=3)
        brand_names = [b[0] for b in BRANDS]
        init_brand = ""
        if item is not None:
            init_brand = next((n for n, c in BRANDS
                               if c == item.get("brand")), "Other…")
        brand_var = tk.StringVar(value=init_brand)
        brand_dd = ttk.Combobox(brand_row, textvariable=brand_var,
                                values=brand_names, width=22,
                                state="readonly")
        brand_dd.pack(side="left")
        custom_brand_var = tk.StringVar(
            value=item.get("brand", "") if item is not None
            and init_brand == "Other…" else "")
        custom_brand_ent = ttk.Entry(brand_row,
                                     textvariable=custom_brand_var, width=8)
        r += 1

        ttk.Label(frm, text="Code *").grid(row=r, column=0, sticky="w",
                                            pady=3, padx=(0, 10))
        code_row = ttk.Frame(frm)
        code_row.grid(row=r, column=1, sticky="w", pady=3)
        code_var = tk.StringVar(value="" if item is None else item["code"])
        code_ent = ttk.Entry(code_row, textvariable=code_var, width=18)
        code_ent.pack(side="left")
        code_hint = ttk.Label(code_row, text="", style="Muted.TLabel")
        code_hint.pack(side="left", padx=8)
        if item is not None:
            code_ent.configure(state="disabled")   # codes are permanent IDs
        r += 1

        # Manual-override detection by VALUE, not by keypress events —
        # a <Key> binding misses right-click paste and programmatic edits.
        # Any content that isn't the suggestion we just wrote counts as
        # manual; clearing the field entirely re-enables auto-suggest.
        code_touched = {"manual": item is not None}
        last_auto = {"value": None}

        def on_code_write(*_):
            val = code_var.get()
            if val == last_auto["value"]:
                return                      # our own programmatic set
            code_touched["manual"] = bool(val.strip())
            if not val.strip():
                code_hint.configure(text="")
                # refill on idle, not now: a keystroke that replaces a
                # selection arrives as delete-then-insert, and refilling
                # between the two would splice the suggestion into what
                # the user is typing. By idle time the insert has landed
                # and re-marked manual, so the refill correctly skips.
                win.after_idle(refresh_code_suggestion)
        code_var.trace_add("write", on_code_write)

        def brand_code() -> str:
            name = brand_var.get()
            code = dict(BRANDS).get(name, "")
            if name == "Other…":
                code = custom_brand_var.get().strip().upper()
            return code

        def refresh_code_suggestion(*_):
            if brand_var.get() == "Other…":
                custom_brand_ent.pack(side="left", padx=(6, 0))
            else:
                custom_brand_ent.pack_forget()
            if item is not None or code_touched["manual"]:
                return
            suggestion = suggest_code(
                brand_code(), vars_["category"].get(),
                [i["code"] for i in self.items])
            last_auto["value"] = suggestion
            code_var.set(suggestion)
            code_hint.configure(text="auto — type to override"
                                if suggestion else "")

        brand_dd.bind("<<ComboboxSelected>>", refresh_code_suggestion)
        custom_brand_var.trace_add("write", refresh_code_suggestion)

        # ---- plain text fields ------------------------------------------------
        vars_: dict[str, tk.StringVar] = {}
        for key, label, width in ITEM_FIELDS:
            ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w",
                                             pady=3, padx=(0, 10))
            init = "" if item is None else str(item.get(key, ""))
            var = tk.StringVar(value=init)
            vars_[key] = var
            ttk.Entry(frm, textvariable=var, width=width).grid(
                row=r, column=1, sticky="w", pady=3)
            r += 1
        vars_["category"].trace_add("write", refresh_code_suggestion)

        # ---- color: swatch + hex + eyedropper --------------------------------
        ttk.Label(frm, text="Color").grid(row=r, column=0, sticky="w",
                                           pady=3, padx=(0, 10))
        color_row = ttk.Frame(frm)
        color_row.grid(row=r, column=1, sticky="w", pady=3)
        color_var = tk.StringVar(value="" if item is None
                                 else item.get("color", ""))
        swatch = tk.Label(color_row, text="  ", width=3, relief="solid",
                          bd=1, bg=theme.SURFACE2,
                          highlightbackground=theme.INK)
        swatch.pack(side="left", padx=(0, 6))
        color_ent = ttk.Entry(color_row, textvariable=color_var, width=10)
        color_ent.pack(side="left")

        def update_swatch(*_):
            val = color_var.get().strip()
            try:
                swatch.configure(bg=val if val else theme.SURFACE2)
            except tk.TclError:
                swatch.configure(bg=theme.SURFACE2)
        color_var.trace_add("write", update_swatch)
        update_swatch()

        def eyedrop():
            def picked(hexcol):
                win.deiconify()
                win.lift()
                if hexcol:
                    color_var.set(hexcol)
            win.withdraw()   # get the dialog out of its own screenshot
            win.after(150, lambda: EyedropperOverlay(
                self.winfo_toplevel(), picked))
        ttk.Button(color_row, text="🎨 Pick from screen",
                   command=eyedrop).pack(side="left", padx=(8, 0))
        r += 1

        # ---- photos (existing item only — new items add photos after save) --
        photo_strip = None
        if item is not None:
            ttk.Label(frm, text="Photos").grid(row=r, column=0, sticky="nw",
                                                pady=(8, 3), padx=(0, 10))
            photo_holder = ttk.Frame(frm, style="Surface.TFrame", padding=6)
            photo_holder.grid(row=r, column=1, sticky="ew", pady=(8, 3))
            current = {"images": list(item["images"]),
                       "image_ids": list(item["image_ids"])}

            def load_image(filename, image_id):
                import io
                import urllib.request
                # Image bytes live in the database now, so thumbnails survive
                # cloud restarts. filename remains only for labels/compatibility.
                url = self.api.base_url + f"/item-images/{image_id}"
                from PIL import Image
                with urllib.request.urlopen(url, timeout=10) as resp:
                    return Image.open(io.BytesIO(resp.read())).convert("RGBA")

            def add_photos(paths):
                def work():
                    err = None
                    updated = None
                    for p in paths:
                        try:
                            with open(p, "rb") as f:
                                content = f.read()
                            updated = self.api.upload_item_image(
                                item["id"], os.path.basename(p), content)
                        except SessionExpired:
                            return
                        except (ApiError, OSError) as e:
                            err = str(e)
                            break
                    def done():
                        if updated:
                            current["images"] = updated["images"]
                            current["image_ids"] = updated["image_ids"]
                            photo_strip.set_photos(current["images"],
                                                   current["image_ids"])
                            self.refresh()
                        if err:
                            theme.show_error(win, "Photo upload", err)
                    self.after(0, done)
                threading.Thread(target=work, daemon=True).start()

            def remove_photo(image_id):
                def work():
                    try:
                        updated = self.api.delete_item_image(item["id"],
                                                             image_id)
                    except SessionExpired:
                        return
                    except ApiError as e:
                        self.after(0, lambda: theme.show_error(
                            win, "Remove photo", str(e)))
                        return
                    def done():
                        current["images"] = updated["images"]
                        current["image_ids"] = updated["image_ids"]
                        photo_strip.set_photos(current["images"],
                                               current["image_ids"])
                        self.refresh()
                    self.after(0, done)
                threading.Thread(target=work, daemon=True).start()

            photo_strip = PhotoStrip(photo_holder, on_add=add_photos,
                                     on_remove=remove_photo,
                                     load_image=load_image)
            photo_strip.pack(fill="x")
            photo_strip.set_photos(current["images"], current["image_ids"])
            r += 1

        # ---- stock / active ---------------------------------------------------
        qty_var = None
        active_var = tk.BooleanVar(value=True if item is None
                                   else item["active"])
        if item is None:
            ttk.Label(frm, text="Opening stock").grid(row=r, column=0,
                                                       sticky="w", pady=3,
                                                       padx=(0, 10))
            qty_var = tk.StringVar(value="")
            ttk.Entry(frm, textvariable=qty_var, width=8).grid(
                row=r, column=1, sticky="w", pady=3)
            r += 1
            ttk.Label(frm, text="Leave opening stock blank to mark the item Not Counted Yet and create an initial count request. Enter 0 only when it was physically counted as zero. Photos can be added after saving.",
                      style="Muted.TLabel").grid(row=r, column=0,
                                                 columnspan=2, sticky="w")
            r += 1
        else:
            ttk.Checkbutton(frm, text="Active (visible in the catalog)",
                            variable=active_var).grid(
                row=r, column=0, columnspan=2, sticky="w", pady=6)
            r += 1
            ttk.Label(frm, text=f"On hand: {item['qty_on_hand']} — use "
                                "Adjust Stock to change it.",
                      style="Muted.TLabel").grid(row=r, column=0,
                                                 columnspan=2, sticky="w")
            r += 1

        err_lbl = ttk.Label(frm, text="", style="Muted.TLabel",
                            foreground=theme.RUST, wraplength=340)
        err_lbl.grid(row=r, column=0, columnspan=2, sticky="w", pady=(6, 0))
        r += 1

        btns = ttk.Frame(frm)
        btns.grid(row=r, column=0, columnspan=2, sticky="e", pady=(12, 0))

        def save():
            data: dict = {}
            for key, _label, _w in ITEM_FIELDS:
                val = vars_[key].get().strip()
                if key == "reorder_threshold":
                    try:
                        data[key] = int(val or 0)
                    except ValueError:
                        err_lbl.configure(
                            text="Reorder at must be a whole number.")
                        return
                elif key == "cost":
                    try:
                        data[key] = float(val or 0)
                    except ValueError:
                        err_lbl.configure(text="Unit cost must be a number.")
                        return
                else:
                    data[key] = val
            data["code"] = code_var.get().strip().upper()
            data["brand"] = brand_code()
            color = color_var.get().strip()
            if color and not (color.startswith("#") and len(color) in (4, 7)):
                err_lbl.configure(text="Color must be hex like #1a2b3c "
                                        "(or use the picker).")
                return
            data["color"] = color
            if not data["code"] or not data["name"]:
                err_lbl.configure(text="Code and Name are required.")
                return

            if item is None:
                try:
                    raw_qty = qty_var.get().strip()
                    data["qty_on_hand"] = int(raw_qty) if raw_qty else None
                except ValueError:
                    err_lbl.configure(
                        text="Opening stock must be a whole number.")
                    return
            else:
                data.pop("code", None)          # not editable
                data["active"] = bool(active_var.get())

            def work():
                try:
                    if item is None:
                        self.api.create_item(data)
                    else:
                        self.api.update_item(item["id"], data)
                except SessionExpired:
                    return
                except ApiError as e:
                    self.after(0, lambda: err_lbl.configure(text=str(e)))
                    return
                self.after(0, lambda: (win.destroy(), self.refresh()))
            threading.Thread(target=work, daemon=True).start()

        ttk.Button(btns, text="Cancel", command=win.destroy).pack(
            side="right", padx=(8, 0))
        ttk.Button(btns, text="Save", style="Primary.TButton",
                   command=save).pack(side="right")
        win.bind("<Return>", lambda e: save())
        win.bind("<Escape>", lambda e: win.destroy())
        fade_in(win)

    def _delete_item(self):
        item = self._selected_item()
        if not item:
            return
        if not messagebox.askyesno(
                "Delete item",
                f"Delete {item['code']} · {item['name']}?\n\n"
                "It will disappear from the catalog and inventory list. Past "
                "orders and inventory history will keep a Deleted Item label.", parent=self):
            return
        self.spinner.start("Deleting item")

        def work():
            try:
                self.api.delete_item(item["id"])
            except SessionExpired:
                return
            except ApiError as exc:
                self.after(0, lambda: (self.spinner.stop(),
                                       theme.show_error(self, "Delete failed", str(exc))))
                return
            self.after(0, self.refresh)
        threading.Thread(target=work, daemon=True).start()

    def _resolve_count_dialog(self):
        item = self._selected_item()
        if not item or not item.get("open_count_requests"):
            return
        win = tk.Toplevel(self)
        win.title(f"Recount requests — {item['code']}")
        win.configure(bg=theme.PAPER)
        win.transient(self.winfo_toplevel())
        win.grab_set()
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=f"{item['name']} ({item['code']})",
                  font=theme.FONT_BOLD).pack(anchor="w")
        body = ttk.Label(frm, text="Loading request notes…", style="Muted.TLabel",
                         wraplength=420, justify="left")
        body.pack(anchor="w", pady=(5, 10))
        ttk.Label(frm, text=f"System quantity: {item['qty_on_hand']}",
                  style="SurfaceMuted.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(frm, text="Physical quantity counted *").pack(anchor="w")
        physical = tk.StringVar(value=str(item["qty_on_hand"]))
        physical_entry = ttk.Entry(frm, textvariable=physical, width=16)
        physical_entry.pack(anchor="w", pady=(3, 8))
        delta_preview = ttk.Label(frm, text="Adjustment: 0", style="Muted.TLabel")
        delta_preview.pack(anchor="w", pady=(0, 8))
        ttk.Label(frm, text="Resolution note (optional)").pack(anchor="w")
        note = tk.StringVar(value="Physical recount completed")
        ttk.Entry(frm, textvariable=note, width=48).pack(fill="x", pady=(3, 10))

        def update_preview(*_):
            try:
                counted = int(physical.get())
                if counted < 0:
                    raise ValueError
                delta = counted - int(item["qty_on_hand"])
                delta_preview.configure(text=f"Adjustment: {delta:+d} · new on hand {counted}")
            except ValueError:
                delta_preview.configure(text="Enter a whole number of zero or more")
        physical.trace_add("write", update_preview)
        buttons = ttk.Frame(frm)
        buttons.pack(fill="x")
        resolve_btn = ttk.Button(buttons, text="Mark Recounted",
                                 style="Primary.TButton", state="disabled")
        resolve_btn.pack(side="right")
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 8))
        requests = []

        def loaded(rows):
            requests[:] = [row for row in rows if row["item_id"] == item["id"]]
            if requests:
                lines = [f"• {row['requester']}: {row['note'] or 'No note'}"
                         for row in requests]
                body.configure(text="\n".join(lines))
                resolve_btn.configure(state="normal")
            else:
                body.configure(text="No open requests remain.")

        def load():
            try:
                rows = self.api.count_requests("open")
            except (SessionExpired, ApiError) as exc:
                self.after(0, lambda: body.configure(text=str(exc)))
                return
            self.after(0, lambda: loaded(rows))
        threading.Thread(target=load, daemon=True).start()

        def resolve():
            try:
                counted = int(physical.get())
                if counted < 0:
                    raise ValueError
            except ValueError:
                theme.show_error(win, "Physical count required",
                                 "Enter a whole physical quantity of zero or more.")
                return
            delta = counted - int(item["qty_on_hand"])
            if not messagebox.askyesno(
                    "Apply physical recount",
                    f"System: {item['qty_on_hand']}\nCounted: {counted}\nAdjustment: {delta:+d}\n\nApply this inventory correction?",
                    parent=win):
                return
            resolve_btn.configure(state="disabled")
            def work():
                try:
                    for row in requests:
                        self.api.resolve_count_request(row["id"], counted,
                                                       note.get().strip())
                except SessionExpired:
                    return
                except ApiError as exc:
                    self.after(0, lambda: (body.configure(text=str(exc)),
                                           resolve_btn.configure(state="normal")))
                    return
                self.after(0, lambda: (win.destroy(), self.refresh()))
            threading.Thread(target=work, daemon=True).start()
        resolve_btn.configure(command=resolve)
        update_preview()
        physical_entry.focus_set()
        fade_in(win)

    def _adjust_dialog(self):
        item = self._selected_item()
        if not item:
            return
        win = tk.Toplevel(self)
        win.title(f"Adjust stock — {item['code']}")
        win.configure(bg=theme.PAPER)
        win.transient(self.winfo_toplevel())
        win.grab_set()
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text=item["name"], font=theme.FONT_BOLD).grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frm, text=f"Currently on hand: {item['qty_on_hand']}",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=2,
                                             sticky="w", pady=(0, 10))

        ttk.Label(frm, text="Change by").grid(row=2, column=0, sticky="w",
                                               padx=(0, 10))
        delta_var = tk.StringVar(value="")
        delta_ent = ttk.Entry(frm, textvariable=delta_var, width=10)
        delta_ent.grid(row=2, column=1, sticky="w")
        ttk.Label(frm, text="Positive to add stock, negative to remove "
                            "(e.g. -12 for damage/shrinkage).",
                  style="Muted.TLabel", wraplength=280).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(2, 8))

        ttk.Label(frm, text="Reason *").grid(row=4, column=0, sticky="w",
                                              padx=(0, 10))
        reason_var = tk.StringVar()
        ttk.Entry(frm, textvariable=reason_var, width=34).grid(
            row=4, column=1, sticky="w")

        err_lbl = ttk.Label(frm, text="", style="Muted.TLabel",
                            foreground=theme.RUST, wraplength=300)
        err_lbl.grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=2, sticky="e", pady=(12, 0))

        def apply():
            try:
                delta = int(delta_var.get().strip())
            except ValueError:
                err_lbl.configure(text="Change must be a whole number, e.g. 25 or -12.")
                return
            if delta == 0:
                err_lbl.configure(text="Change can't be zero.")
                return
            reason = reason_var.get().strip()
            if not reason:
                err_lbl.configure(text="A reason is required — it goes in the "
                                        "inventory log.")
                return

            def work():
                try:
                    self.api.adjust_inventory(item["id"], delta, reason)
                except SessionExpired:
                    return
                except ApiError as e:
                    self.after(0, lambda: err_lbl.configure(text=str(e)))
                    return
                self.after(0, lambda: (win.destroy(), self.refresh()))
            threading.Thread(target=work, daemon=True).start()

        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right",
                                                                  padx=(8, 0))
        ttk.Button(btns, text="Apply", style="Primary.TButton",
                   command=apply).pack(side="right")
        delta_ent.focus_set()
        win.bind("<Return>", lambda e: apply())
        win.bind("<Escape>", lambda e: win.destroy())
        fade_in(win)
