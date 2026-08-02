"""Order queue and history, including picking and completion proof."""
import threading
import tkinter as tk
import html
import re
import tempfile
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import ttk, messagebox, filedialog

from . import theme
from .widgets import SpinnerLabel, fade_in
from ..services.api_client import ApiClient, ApiError, SessionExpired

STATUS_OPTIONS = ["all", "pending", "approved", "picking", "fulfilled", "rejected"]
STATE_CODES = [
    "AL","AK","AZ","AR","CA","CO","CT","DE","DC","FL","GA","HI","ID","IL",
    "IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE",
    "NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","PR","RI","SC",
    "SD","TN","TX","UT","VT","VA","VI","WA","WV","WI","WY",
]


class OrdersView(ttk.Frame):
    def __init__(self, parent, api: ApiClient):
        super().__init__(parent)
        self.api = api
        self.orders: list[dict] = []
        self.selected_order: dict | None = None
        self._qty_vars: dict[int, tk.IntVar] = {}
        self._poll_stop = threading.Event()
        self._since: str | None = None
        self._build()
        self.refresh()
        self._start_polling()
        self.bind("<Destroy>", self._on_destroy)

    def _build(self):
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12,
                 pady=(12, 6))
        ttk.Label(top, text="ORDERS", style="Head.TLabel").pack(side="left")
        self.count_lbl = ttk.Label(top, text="", style="Muted.TLabel")
        self.count_lbl.pack(side="left", padx=10)
        self.spinner = SpinnerLabel(top, text="Loading", style="Muted.TLabel")
        self.spinner.pack(side="left")
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="right")
        self.status_var = tk.StringVar(value="all")
        status = ttk.Combobox(top, textvariable=self.status_var,
                              values=STATUS_OPTIONS, width=12, state="readonly")
        status.pack(side="right", padx=8)
        status.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Label(top, text="Status", style="Muted.TLabel").pack(side="right")

        left = ttk.Frame(self)
        left.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(0, 12))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        cols = ("id", "status", "ready", "requester", "project", "created")
        self.tree = ttk.Treeview(left, columns=cols, show="headings",
                                 selectmode="browse")
        headings = {
            "id": ("#", 50), "status": ("Status", 90),
            "ready": ("Info", 90), "requester": ("Requester", 110),
            "project": ("Project / Event", 190), "created": ("Created", 125),
        }
        for col, (label, width) in headings.items():
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.tag_configure("incomplete", foreground=theme.RUST)
        self.tree.tag_configure("fulfilled", foreground=theme.OK)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        right = ttk.Frame(self, style="Surface.TFrame")
        right.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(0, 12))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        self.detail_head = ttk.Label(right, text="Select an order",
                                     style="Surface.TLabel", font=theme.FONT_HEAD)
        self.detail_head.grid(row=0, column=0, sticky="w", padx=14,
                              pady=(14, 2))
        self.status_lbl = ttk.Label(right, text="", style="SurfaceMuted.TLabel")
        self.status_lbl.grid(row=1, column=0, sticky="w", padx=14)
        self.detail_sub = ttk.Label(right, text="", style="SurfaceMuted.TLabel",
                                    wraplength=460, justify="left")
        self.detail_sub.grid(row=2, column=0, sticky="w", padx=14, pady=(4, 2))

        wrap = ttk.Frame(right, style="Surface.TFrame")
        wrap.grid(row=3, column=0, sticky="nsew", padx=14, pady=8)
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(wrap, bg=theme.SURFACE, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        sb2 = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        sb2.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=sb2.set)
        self.lines_inner = ttk.Frame(self.canvas, style="Surface.TFrame")
        self._window = self.canvas.create_window((0, 0), window=self.lines_inner,
                                                  anchor="nw")
        self.lines_inner.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            self._window, width=e.width))

        controls = ttk.Frame(right, style="Surface.TFrame")
        controls.grid(row=4, column=0, sticky="ew", padx=14, pady=(4, 14))
        self.reason_var = tk.StringVar()
        self.reason_entry = ttk.Entry(controls, textvariable=self.reason_var)
        self.reason_entry.pack(fill="x", pady=(0, 8))
        self.reason_entry.insert(0, "")
        btns = ttk.Frame(controls, style="Surface.TFrame")
        btns.pack(fill="x")
        self.edit_btn = ttk.Button(btns, text="Edit…", command=self._edit_dialog,
                                   state="disabled")
        self.edit_btn.pack(side="left")
        self.delete_btn = ttk.Button(btns, text="Delete Order",
                                     style="Danger.TButton",
                                     command=self._delete_order, state="disabled")
        self.delete_btn.pack(side="left", padx=(8, 0))
        self.approve_btn = ttk.Button(btns, text="Approve",
                                      style="Primary.TButton",
                                      command=self._approve, state="disabled")
        self.approve_btn.pack(side="left", padx=(8, 0))
        self.reject_btn = ttk.Button(btns, text="Reject",
                                     style="Danger.TButton",
                                     command=self._reject, state="disabled")
        self.reject_btn.pack(side="left", padx=(8, 0))
        self.pick_btn = ttk.Button(btns, text="Pick Order",
                                   style="Primary.TButton",
                                   command=self._pick, state="disabled")
        self.pick_btn.pack(side="left", padx=(8, 0))
        self.print_btn = ttk.Button(btns, text="Print Pick Slip",
                                    command=self._print_pick_slip, state="disabled")
        self.print_btn.pack(side="left", padx=(8, 0))
        self.done_btn = ttk.Button(btns, text="Mark Done…",
                                   style="Primary.TButton",
                                   command=self._done_dialog, state="disabled")
        self.done_btn.pack(side="left", padx=(8, 0))

    def refresh(self):
        self.spinner.start("Loading orders")
        status_filter = self.status_var.get()

        def work():
            try:
                orders = self.api.all_orders(status_filter)
            except SessionExpired:
                return
            except ApiError as exc:
                self.after(0, lambda: self._load_failed(exc))
                return
            self.after(0, lambda: self._set_orders(orders))
        threading.Thread(target=work, daemon=True).start()

    def _load_failed(self, exc):
        self.spinner.stop()
        theme.show_error(self, "Load failed", str(exc))

    def _set_orders(self, orders):
        if not self.winfo_exists():
            return
        self.spinner.stop()
        keep = self.selected_order["id"] if self.selected_order else None
        self.orders = orders
        self.tree.delete(*self.tree.get_children())
        for order in orders:
            tags = []
            if order.get("incomplete"):
                tags.append("incomplete")
            if order["status"] == "fulfilled":
                tags.append("fulfilled")
            self.tree.insert("", "end", iid=str(order["id"]), values=(
                order["id"], order["status"].upper(),
                "INCOMPLETE" if order.get("incomplete") else "Ready",
                order["requester"], order.get("project") or "—",
                order["created_at"][:16].replace("T", " "),
            ), tags=tags)
        self.count_lbl.configure(text=f"{len(orders)} order{'s' if len(orders) != 1 else ''}")
        if keep is not None and self.tree.exists(str(keep)):
            self.tree.selection_set(str(keep))
            self._on_select()
        else:
            self.selected_order = None
            self._show_detail(None)

    def _on_select(self, _evt=None):
        selection = self.tree.selection()
        if not selection:
            self._show_detail(None)
            return
        oid = int(selection[0])
        self.selected_order = next((o for o in self.orders if o["id"] == oid), None)
        self._show_detail(self.selected_order)

    def _show_detail(self, order):
        for child in self.lines_inner.winfo_children():
            child.destroy()
        self._qty_vars.clear()
        for button in (self.edit_btn, self.delete_btn, self.approve_btn, self.reject_btn,
                       self.pick_btn, self.print_btn, self.done_btn):
            button.configure(state="disabled")
        if not order:
            self.detail_head.configure(text="Select an order")
            self.status_lbl.configure(text="")
            self.detail_sub.configure(text="")
            return

        self.detail_head.configure(text=f"Order #{order['id']} — {order['requester']}")
        status_text = order["status"].upper()
        if order.get("incomplete"):
            status_text += "  ·  INCOMPLETE: " + "; ".join(order["incomplete_reasons"])
        self.status_lbl.configure(text=status_text,
                                  foreground=theme.RUST if order.get("incomplete") else theme.status_color(order["status"]))
        project = order.get("project_details") or {}
        bits = []
        for label, key in (("Event", "event_date"), ("Deliver", "delivery_date"),
                           ("Ship by", "ship_by_date")):
            if project.get(key):
                bits.append(f"{label} {project[key]}")
        if project.get("ups_ground_days"):
            bits.append(f"UPS Ground {project['ups_ground_days']} business day(s)")
        address = ", ".join(filter(None, [
            project.get("shipping_address1"), project.get("shipping_address2"),
            " ".join(filter(None, [project.get("shipping_city"),
                                     project.get("shipping_state"),
                                     project.get("shipping_postal_code")]))
        ]))
        bits.append("Ship to " + address if address else "ADDRESS PENDING")
        if order.get("tracking_numbers"):
            bits.append("Tracking: " + ", ".join(order["tracking_numbers"]))
        if order.get("notes"):
            bits.append('Notes: "' + order["notes"] + '"')
        self.detail_sub.configure(text="  ·  ".join(bits))

        hdr = ttk.Frame(self.lines_inner, style="Surface.TFrame")
        hdr.pack(fill="x", pady=(4, 3))
        ttk.Label(hdr, text="Item", style="SurfaceMuted.TLabel").pack(side="left")
        ttk.Label(hdr, text="Qty", style="SurfaceMuted.TLabel").pack(side="right")
        for line in order["lines"]:
            row = ttk.Frame(self.lines_inner, style="Surface.TFrame")
            row.pack(fill="x", pady=3)
            label = line["item_name"] + ("  ~ESTIMATED" if line.get("qty_estimated") else "")
            ttk.Label(row, text=label, style="Surface.TLabel").pack(side="left")
            ttk.Label(row, text=" " + line["item_code"],
                      style="SurfaceMuted.TLabel", font=theme.FONT_MONO).pack(side="left")
            if order["status"] == "pending":
                var = tk.IntVar(value=line["qty_requested"])
                self._qty_vars[line["item_id"]] = var
                spin = ttk.Spinbox(row, from_=0, to=line["qty_requested"],
                                   textvariable=var, width=6)
                spin.pack(side="right")
                ttk.Label(row, text=f"of {line['qty_requested']}  ",
                          style="SurfaceMuted.TLabel").pack(side="right")
            else:
                shown = line["qty_approved"] if line["qty_approved"] is not None else line["qty_requested"]
                ttk.Label(row, text=str(shown), style="Surface.TLabel",
                          font=theme.FONT_MONO).pack(side="right")
        if order.get("proof_photo_ids"):
            ttk.Label(self.lines_inner,
                      text=f"Completion proof: {len(order['proof_photo_ids'])} photo(s)",
                      style="SurfaceMuted.TLabel").pack(anchor="w", pady=(10, 0))

        self.edit_btn.configure(state="normal")
        if self.api.has_role("admin"):
            self.delete_btn.configure(state="normal")
        if order["status"] == "pending":
            self.reject_btn.configure(state="normal")
            if not order.get("incomplete"):
                self.approve_btn.configure(state="normal")
        elif order["status"] == "approved":
            self.pick_btn.configure(state="normal")
        elif order["status"] == "picking":
            self.print_btn.configure(state="normal")
            self.done_btn.configure(state="normal")
        elif order["status"] == "fulfilled":
            self.print_btn.configure(state="normal")

    def _run_action(self, call, success_message):
        self.spinner.start("Saving")
        self._set_action_buttons(False)

        def work():
            try:
                call()
            except SessionExpired:
                return
            except (ApiError, OSError) as exc:
                self.after(0, lambda: self._action_failed(exc))
                return
            self.after(0, lambda: self._action_done(success_message))
        threading.Thread(target=work, daemon=True).start()

    def _set_action_buttons(self, enabled):
        if not enabled:
            for button in (self.edit_btn, self.delete_btn, self.approve_btn, self.reject_btn,
                           self.pick_btn, self.print_btn, self.done_btn):
                button.configure(state="disabled")
        elif self.selected_order:
            self._show_detail(self.selected_order)

    def _action_failed(self, exc):
        self.spinner.stop()
        self._set_action_buttons(True)
        theme.show_error(self, "Action failed", str(exc))

    def _action_done(self, message):
        self.spinner.stop()
        self.selected_order = None
        self.refresh()
        self.event_generate("<<InventoryChanged>>")
        theme.show_info(self, "Done", message)

    def _collect_overrides(self):
        order = self.selected_order
        if not order:
            return None
        overrides = {}
        for line in order["lines"]:
            var = self._qty_vars.get(line["item_id"])
            if var is None:
                continue
            try:
                value = max(0, min(int(var.get()), line["qty_requested"]))
            except (ValueError, tk.TclError):
                value = line["qty_requested"]
            if value != line["qty_requested"]:
                overrides[line["item_id"]] = value
        return overrides or None

    def _approve(self):
        order = self.selected_order
        if not order:
            return
        self._run_action(lambda: self.api.approve_order(
            order["id"], reason=self.reason_var.get().strip(),
            line_overrides=self._collect_overrides()),
            f"Order #{order['id']} approved.")

    def _reject(self):
        order = self.selected_order
        if not order:
            return
        reason = self.reason_var.get().strip()
        if not reason:
            theme.show_error(self, "Reason required", "Enter a reason before rejecting.")
            return
        if not messagebox.askyesno("Reject order", f"Reject order #{order['id']}?", parent=self):
            return
        self._run_action(lambda: self.api.reject_order(order["id"], reason),
                         f"Order #{order['id']} rejected.")

    def _pick(self):
        order = self.selected_order
        if not order:
            return
        self.spinner.start("Starting pick")
        self._set_action_buttons(False)

        def work():
            try:
                picked = self.api.pick_order(order["id"])
            except SessionExpired:
                return
            except ApiError as exc:
                self.after(0, lambda: self._action_failed(exc))
                return
            self.after(0, lambda: self._pick_done(picked))
        threading.Thread(target=work, daemon=True).start()

    def _pick_done(self, order):
        self.spinner.stop()
        try:
            self._open_pick_slip(order)
        except OSError as exc:
            theme.show_error(self, "Pick slip", f"Order started, but the pick slip could not open: {exc}")
        self.selected_order = None
        self.refresh()
        theme.show_info(self, "Pick started",
                        f"Order #{order['id']} is being picked. The location-sorted pick slip opened for printing.")

    @staticmethod
    def _location_key(line):
        location = (line.get("item_location") or "").strip()
        if not location:
            return (1, [])
        parts = [int(part) if part.isdigit() else part.lower()
                 for part in re.split(r"(\d+)", location)]
        return (0, parts)

    def _open_pick_slip(self, order):
        project = order.get("project_details") or {}
        lines = sorted(order.get("lines") or [], key=self._location_key)
        picker = (self.api.user or {}).get("full_name") or (self.api.user or {}).get("username") or ""
        street = ", ".join(filter(None, [project.get("shipping_address1"),
                                          project.get("shipping_address2")]))
        city = " ".join(filter(None, [project.get("shipping_city"),
                                       project.get("shipping_state"),
                                       project.get("shipping_postal_code")]))
        address = ", ".join(filter(None, [street, city])) or "ADDRESS PENDING"
        rows = []
        missing_started = False
        for line in lines:
            has_location = bool((line.get("item_location") or "").strip())
            if not has_location and not missing_started:
                rows.append("<tr class='section'><td colspan='5'>LOCATION MISSING</td></tr>")
                missing_started = True
            qty = line.get("qty_approved")
            if qty is None:
                qty = line.get("qty_requested")
            location = (line.get("item_location") or "").strip() or "LOCATION MISSING"
            estimated = ' <span class="est">EST.</span>' if line.get("qty_estimated") else ""
            rows.append(
                "<tr>"
                f"<td class='check'>□</td><td class='loc'>{html.escape(location)}</td>"
                f"<td class='code'>{html.escape(str(line.get('item_code') or ''))}</td>"
                f"<td>{html.escape(str(line.get('item_name') or ''))}{estimated}</td>"
                f"<td class='qty'>{html.escape(str(qty))}</td>"
                "</tr>")
        document = f'''<!doctype html><html><head><meta charset="utf-8">
<title>Pick Slip Order {order['id']}</title>
<style>
@page{{size:letter;margin:.42in}}*{{box-sizing:border-box}}body{{font-family:Arial,sans-serif;color:#111;margin:0}}
header{{border:3px solid #111;padding:12px 14px;display:flex;justify-content:space-between;gap:20px}}
h1{{font-size:24px;margin:0;text-transform:uppercase}}.order{{font-size:28px;font-weight:800}}
.meta{{display:grid;grid-template-columns:140px 1fr 140px 1fr;border:2px solid #111;border-top:0}}
.meta div{{padding:7px 9px;border-right:1px solid #999;border-bottom:1px solid #999;min-height:32px}}
.meta .label{{font-size:10px;font-weight:bold;text-transform:uppercase;background:#eee}}
table{{width:100%;border-collapse:collapse;margin-top:14px}}th,td{{border:1px solid #222;padding:8px 7px;text-align:left}}
th{{background:#111;color:white;text-transform:uppercase;font-size:11px;letter-spacing:.04em}}.section td{{background:#eee;font-size:11px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}}
.check{{font-size:23px;width:38px;text-align:center}}.loc{{font-weight:800;width:130px}}.code{{font-family:monospace;width:125px}}.qty{{font-size:19px;font-weight:800;text-align:center;width:70px}}
.est{{font-size:9px;border:1px solid #111;padding:1px 3px}}.missing{{margin-top:8px;font-size:10px}}
.footer{{margin-top:18px;display:grid;grid-template-columns:1fr 1fr;gap:14px}}.box{{border:2px solid #111;min-height:92px;padding:8px}}
.box strong{{font-size:11px;text-transform:uppercase}}.line{{display:inline-block;border-bottom:1px solid #111;min-width:170px;height:22px}}
.printbar{{position:fixed;right:18px;top:18px}}@media print{{.printbar{{display:none}}}}
</style></head><body onload="setTimeout(()=>window.print(),250)">
<button class="printbar" onclick="window.print()">Print Again</button>
<header><div><h1>Warehouse Pick Slip</h1><div>{html.escape(str(order.get('project') or 'General order'))}</div></div><div class="order">ORDER #{order['id']}</div></header>
<div class="meta">
<div class="label">Requester</div><div>{html.escape(str(order.get('requester') or ''))}</div>
<div class="label">Picker</div><div>{html.escape(picker)}</div>
<div class="label">Event date</div><div>{html.escape(str(project.get('event_date') or '—'))}</div>
<div class="label">Deliver by</div><div>{html.escape(str(project.get('delivery_date') or '—'))}</div>
<div class="label">Ship by</div><div>{html.escape(str(project.get('ship_by_date') or '—'))}</div>
<div class="label">Printed</div><div>{datetime.now().strftime('%Y-%m-%d %I:%M %p')}</div>
<div class="label">Ship to</div><div style="grid-column:span 3">{html.escape(address)}</div>
</div>
<table><thead><tr><th>Pick</th><th>Location</th><th>Part #</th><th>Item</th><th>Qty</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<div class="missing">Items without a saved location are sorted to the bottom under <b>LOCATION MISSING</b>.</div>
<div class="footer"><div class="box"><strong>Boxes / tracking</strong><p>Box count: <span class="line"></span></p><p>Tracking 1: <span class="line"></span></p><p>Tracking 2: <span class="line"></span></p><p>Tracking 3: <span class="line"></span></p></div>
<div class="box"><strong>Picker completion</strong><p>Initials: <span class="line"></span></p><p>Notes:</p></div></div>
</body></html>'''
        out = Path(tempfile.gettempdir()) / f"oti_pick_slip_order_{order['id']}.html"
        out.write_text(document, encoding="utf-8")
        if not webbrowser.open(out.resolve().as_uri()):
            raise OSError("No web browser was available to open the printable pick slip.")

    def _print_pick_slip(self):
        order = self.selected_order
        if not order:
            return
        try:
            self._open_pick_slip(order)
        except OSError as exc:
            theme.show_error(self, "Pick slip", str(exc))

    def _delete_order(self):
        order = self.selected_order
        if not order:
            return
        tracking = ", ".join(order.get("tracking_numbers") or []) or "none"
        items = "\n".join(
            f"• {line['item_code']} · {line['item_name']} × "
            f"{line.get('qty_approved') if line.get('qty_approved') is not None else line['qty_requested']}"
            for line in order.get("lines", []))
        if not messagebox.askyesno(
                "Delete order",
                f"Delete order #{order['id']} permanently from all order views?\n\n"
                f"Requester: {order.get('requester')}\nStatus: {order.get('status')}\n"
                f"Tracking: {tracking}\n\n{items}\n\n"
                "Inventory deductions will remain unchanged. Completion photos and tracking records will be removed.",
                parent=self):
            return
        self._run_action(lambda: self.api.delete_order(order["id"]),
                         f"Order #{order['id']} deleted. Inventory was left unchanged.")

    def _done_dialog(self):
        order = self.selected_order
        if not order:
            return
        win = tk.Toplevel(self)
        win.title(f"Complete order #{order['id']}")
        win.configure(bg=theme.PAPER)
        win.transient(self.winfo_toplevel())
        win.grab_set()
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="TRACKING & COMPLETION PROOF",
                  style="Head.TLabel").pack(anchor="w")
        ttk.Label(frm, text="At least one tracking number and one photo are required.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 10))
        tracks_frame = ttk.Frame(frm)
        tracks_frame.pack(fill="x")
        track_vars: list[tk.StringVar] = []

        def add_tracking(value=""):
            row = ttk.Frame(tracks_frame)
            row.pack(fill="x", pady=2)
            var = tk.StringVar(value=value)
            track_vars.append(var)
            ttk.Entry(row, textvariable=var, width=38).pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="Remove", command=lambda: (row.destroy(), track_vars.remove(var))).pack(side="left", padx=(6, 0))
        add_tracking()
        ttk.Button(frm, text="＋ Add another tracking number", command=add_tracking).pack(anchor="w", pady=6)
        photos: list[str] = []
        photo_lbl = ttk.Label(frm, text="No photos selected", style="Muted.TLabel")
        photo_lbl.pack(anchor="w", pady=(8, 2))

        def choose_photos():
            selected = filedialog.askopenfilenames(
                parent=win, title="Choose completion photos",
                filetypes=[("Images", "*.jpg *.jpeg *.png *.gif *.webp")])
            if selected:
                photos[:] = list(selected)
                photo_lbl.configure(text=f"{len(photos)} photo(s) selected")
        ttk.Button(frm, text="Choose Photo(s)…", command=choose_photos).pack(anchor="w")
        err = ttk.Label(frm, text="", style="Muted.TLabel", foreground=theme.RUST)
        err.pack(anchor="w", pady=(8, 0))
        buttons = ttk.Frame(frm)
        buttons.pack(fill="x", pady=(12, 0))

        def submit():
            tracking = [v.get().strip() for v in track_vars if v.get().strip()]
            if not tracking:
                err.configure(text="Add at least one tracking number.")
                return
            if not photos:
                err.configure(text="Choose at least one completion photo.")
                return
            win.destroy()
            self._run_action(lambda: self.api.fulfill_order(order["id"], tracking, photos),
                             f"Order #{order['id']} completed with tracking proof.")
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(buttons, text="Mark Done", style="Primary.TButton",
                   command=submit).pack(side="right", padx=(0, 8))
        fade_in(win)

    def _edit_dialog(self):
        order = self.selected_order
        if not order:
            return
        win = tk.Toplevel(self)
        win.title(f"Edit order #{order['id']}")
        win.geometry("560x720")
        win.configure(bg=theme.PAPER)
        win.transient(self.winfo_toplevel())
        win.grab_set()
        canvas = tk.Canvas(win, bg=theme.PAPER, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
        sb.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=sb.set)
        frm = ttk.Frame(canvas, padding=16)
        window_id = canvas.create_window((0, 0), window=frm, anchor="nw")
        frm.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window_id, width=e.width))
        project = order.get("project_details") or {}
        fields = {}

        def field(label, key, value=""):
            ttk.Label(frm, text=label).pack(anchor="w", pady=(7, 2))
            var = tk.StringVar(value=value or "")
            ttk.Entry(frm, textvariable=var).pack(fill="x")
            fields[key] = var
        ttk.Label(frm, text=f"EDIT ORDER #{order['id']}", style="Head.TLabel").pack(anchor="w")
        field("Project / event name", "name", project.get("name") or order.get("project") or "")
        field("Owner", "owner", project.get("owner"))
        field("Event date (YYYY-MM-DD)", "event_date", project.get("event_date"))
        field("Venue / location", "location", project.get("location"))
        field("Street address", "shipping_address1", project.get("shipping_address1"))
        field("Suite / floor / attention", "shipping_address2", project.get("shipping_address2"))
        field("City", "shipping_city", project.get("shipping_city"))
        ttk.Label(frm, text="State").pack(anchor="w", pady=(7, 2))
        state_var = tk.StringVar(value=project.get("shipping_state") or "")
        ttk.Combobox(frm, textvariable=state_var, values=STATE_CODES,
                     state="readonly").pack(fill="x")
        fields["shipping_state"] = state_var
        field("ZIP code", "shipping_postal_code", project.get("shipping_postal_code"))
        field("Estimated attendees", "attendees",
              "" if project.get("attendees") is None else str(project.get("attendees")))
        ttk.Label(frm, text="Notes").pack(anchor="w", pady=(7, 2))
        notes = tk.Text(frm, height=3, bg=theme.SURFACE2, fg=theme.INK,
                        insertbackground=theme.INK, relief="flat")
        notes.pack(fill="x")
        notes.insert("1.0", order.get("notes") or "")
        ttk.Label(frm, text="LINE QUANTITIES", style="Head.TLabel").pack(anchor="w", pady=(14, 4))
        line_vars = []
        for line in order["lines"]:
            row = ttk.Frame(frm)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=line["item_name"]).pack(side="left", fill="x", expand=True)
            estimated = tk.BooleanVar(value=line.get("qty_estimated", False))
            ttk.Checkbutton(row, text="Estimated", variable=estimated).pack(side="right")
            qty = tk.StringVar(value=str(line["qty_requested"]))
            ttk.Entry(row, textvariable=qty, width=7).pack(side="right", padx=6)
            line_vars.append((line["item_id"], qty, estimated))
        err = ttk.Label(frm, text="", style="Muted.TLabel", foreground=theme.RUST,
                        wraplength=500)
        err.pack(anchor="w", pady=(8, 0))
        buttons = ttk.Frame(frm)
        buttons.pack(fill="x", pady=(12, 8))

        def save():
            try:
                edit_lines = []
                for item_id, qty_var, estimated_var in line_vars:
                    qty = int(qty_var.get().strip())
                    if qty <= 0:
                        raise ValueError
                    edit_lines.append({"item_id": item_id, "qty": qty,
                                       "estimated": bool(estimated_var.get())})
                attendees_raw = fields["attendees"].get().strip()
                attendees = None if attendees_raw == "" else int(attendees_raw)
                if attendees is not None and attendees < 0:
                    raise ValueError
            except ValueError:
                err.configure(text="Quantities and attendees must be valid whole numbers.")
                return
            project_body = {key: var.get().strip() for key, var in fields.items()
                            if key != "attendees"}
            project_body["attendees"] = attendees
            body = {"notes": notes.get("1.0", "end").strip(),
                    "project": project_body, "lines": edit_lines}
            win.destroy()
            self._run_action(lambda: self.api.edit_order(order["id"], body),
                             f"Order #{order['id']} updated.")
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(buttons, text="Save Changes", style="Primary.TButton",
                   command=save).pack(side="right", padx=(0, 8))
        fade_in(win)

    def _start_polling(self):
        def loop():
            while not self._poll_stop.is_set():
                try:
                    result = self.api.pending_orders_updates(self._since)
                    self._since = result["server_time"]
                    if result.get("orders") and not self._poll_stop.is_set():
                        self.after(0, self.refresh)
                except SessionExpired:
                    return
                except ApiError:
                    if self._poll_stop.wait(4.0):
                        return
        threading.Thread(target=loop, daemon=True).start()

    def _on_destroy(self, event):
        if event.widget is self:
            self._poll_stop.set()
