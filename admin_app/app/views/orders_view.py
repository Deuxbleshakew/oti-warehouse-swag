"""
admin_app/app/views/orders_view.py — the Pending Orders tab.

Self-contained ttk.Frame: give it a parent and an ApiClient and it runs.
Designed to be lifted into another app's Notebook unchanged (the OpsDeck
plan) — it owns its background poll thread and cleans it up on destroy.

Threading rule used throughout: network calls happen in worker threads;
every UI mutation is marshalled back with self.after(0, ...). Tkinter is
not thread-safe and quietly corrupts state if you touch widgets from a
worker.
"""
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from . import theme
from ..services.api_client import ApiClient, ApiError, SessionExpired


class OrdersView(ttk.Frame):
    def __init__(self, parent, api: ApiClient):
        super().__init__(parent)
        self.api = api
        self.orders: list[dict] = []
        self.selected_order: dict | None = None
        self._poll_thread: threading.Thread | None = None
        self._poll_stop = threading.Event()
        self._since: str | None = None

        self._build()
        self.refresh()
        self._start_polling()
        self.bind("<Destroy>", self._on_destroy)

    # ---- UI ------------------------------------------------------------------
    def _build(self):
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 6))
        ttk.Label(top, text="PENDING ORDERS", style="Head.TLabel").pack(side="left")
        self.count_lbl = ttk.Label(top, text="", style="Muted.TLabel")
        self.count_lbl.pack(side="left", padx=10)
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="right")
        self.live_lbl = ttk.Label(top, text="● live", style="Muted.TLabel")
        self.live_lbl.pack(side="right", padx=8)

        # left: order list
        left = ttk.Frame(self)
        left.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(0, 12))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        cols = ("id", "requester", "project", "lines", "created")
        self.tree = ttk.Treeview(left, columns=cols, show="headings",
                                 selectmode="browse")
        headings = {"id": ("#", 50), "requester": ("Requester", 110),
                    "project": ("Project / Event", 170),
                    "lines": ("Lines", 60), "created": ("Created", 130)}
        for c, (label, width) in headings.items():
            self.tree.heading(c, text=label)
            self.tree.column(c, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # right: detail / decision pane
        right = ttk.Frame(self, style="Surface.TFrame")
        right.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(0, 12))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        self.detail = right

        self.detail_head = ttk.Label(right, text="Select an order",
                                     style="Surface.TLabel", font=theme.FONT_HEAD)
        self.detail_head.grid(row=0, column=0, sticky="w", padx=14, pady=(14, 2))
        self.detail_sub = ttk.Label(right, text="", style="SurfaceMuted.TLabel",
                                    wraplength=460, justify="left")
        self.detail_sub.grid(row=1, column=0, sticky="w", padx=14)

        # lines area (scrollable canvas holding per-line qty spinboxes)
        lines_wrap = ttk.Frame(right, style="Surface.TFrame")
        lines_wrap.grid(row=2, column=0, sticky="nsew", padx=14, pady=8)
        lines_wrap.columnconfigure(0, weight=1)
        lines_wrap.rowconfigure(0, weight=1)
        self.lines_canvas = tk.Canvas(lines_wrap, bg=theme.SURFACE,
                                      highlightthickness=0)
        self.lines_canvas.grid(row=0, column=0, sticky="nsew")
        lines_sb = ttk.Scrollbar(lines_wrap, orient="vertical",
                                 command=self.lines_canvas.yview)
        lines_sb.grid(row=0, column=1, sticky="ns")
        self.lines_canvas.configure(yscrollcommand=lines_sb.set)
        self.lines_inner = ttk.Frame(self.lines_canvas, style="Surface.TFrame")
        self._lines_window = self.lines_canvas.create_window(
            (0, 0), window=self.lines_inner, anchor="nw")
        self.lines_inner.bind("<Configure>", lambda e: self.lines_canvas.configure(
            scrollregion=self.lines_canvas.bbox("all")))
        self.lines_canvas.bind("<Configure>", lambda e: self.lines_canvas.itemconfigure(
            self._lines_window, width=e.width))

        # decision area
        dec = ttk.Frame(right, style="Surface.TFrame")
        dec.grid(row=3, column=0, sticky="ew", padx=14, pady=(4, 14))
        dec.columnconfigure(1, weight=1)
        ttk.Label(dec, text="Reason", style="SurfaceMuted.TLabel").grid(
            row=0, column=0, sticky="w")
        self.reason_var = tk.StringVar()
        self.reason_entry = ttk.Entry(dec, textvariable=self.reason_var)
        self.reason_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(dec, text="Optional for approve — required for reject.",
                  style="SurfaceMuted.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, 8))
        btns = ttk.Frame(dec, style="Surface.TFrame")
        btns.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.approve_btn = ttk.Button(btns, text="Approve",
                                      style="Primary.TButton",
                                      command=self._approve, state="disabled")
        self.approve_btn.pack(side="left")
        self.reject_btn = ttk.Button(btns, text="Reject",
                                     style="Danger.TButton",
                                     command=self._reject, state="disabled")
        self.reject_btn.pack(side="left", padx=8)

        self._qty_vars: dict[int, tk.IntVar] = {}   # item_id -> approved qty

    # ---- data ---------------------------------------------------------------
    def refresh(self):
        def work():
            try:
                orders = self.api.pending_orders()
            except SessionExpired:
                return
            except ApiError as e:
                self.after(0, lambda: theme.show_error(self, "Load failed", str(e)))
                return
            self.after(0, lambda: self._set_orders(orders))
        threading.Thread(target=work, daemon=True).start()

    def _set_orders(self, orders: list[dict]):
        if not self.winfo_exists():
            return
        self.orders = orders
        keep_id = self.selected_order["id"] if self.selected_order else None
        self.tree.delete(*self.tree.get_children())
        for o in orders:
            created = o["created_at"][:16].replace("T", " ")
            self.tree.insert("", "end", iid=str(o["id"]), values=(
                o["id"], o["requester"], o.get("project") or "—",
                len(o["lines"]), created))
        self.count_lbl.configure(
            text=f"{len(orders)} waiting" if orders else "queue is clear")
        if keep_id is not None and self.tree.exists(str(keep_id)):
            self.tree.selection_set(str(keep_id))
        else:
            self.selected_order = None
            self._show_detail(None)

    def _on_select(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            return
        oid = int(sel[0])
        order = next((o for o in self.orders if o["id"] == oid), None)
        self.selected_order = order
        self._show_detail(order)

    def _show_detail(self, order: dict | None):
        for w in self.lines_inner.winfo_children():
            w.destroy()
        self._qty_vars.clear()
        self.reason_var.set("")

        if not order:
            self.detail_head.configure(text="Select an order")
            self.detail_sub.configure(text="")
            self.approve_btn.configure(state="disabled")
            self.reject_btn.configure(state="disabled")
            return

        self.detail_head.configure(text=f"Order #{order['id']} — {order['requester']}")
        sub = order.get("project") or "No project"
        project = order.get("project_details") or {}
        project_bits = []
        if project.get("event_date"):
            project_bits.append(f"Event {project['event_date']}")
        if project.get("delivery_date"):
            project_bits.append(f"Deliver {project['delivery_date']}")
        if project.get("ship_by_date"):
            project_bits.append(f"SHIP BY {project['ship_by_date']}")
        if project.get("ups_ground_days"):
            days = project["ups_ground_days"]
            project_bits.append(f"UPS Ground {days} business day{'s' if days != 1 else ''}")
        address = ", ".join(part for part in (
            project.get("shipping_address1"),
            project.get("shipping_address2"),
            " ".join(part for part in (
                project.get("shipping_city"), project.get("shipping_state"),
                project.get("shipping_postal_code")) if part),
        ) if part)
        if address:
            project_bits.append(f"Ship to {address}")
        if project.get("location"):
            project_bits.append(f"Venue {project['location']}")
        if project.get("attendees") is not None:
            project_bits.append(f"{project['attendees']} attendees")
        if project_bits:
            sub += "  ·  " + "  ·  ".join(project_bits)
        if order.get("notes"):
            sub += f'  ·  "{order["notes"]}"'
        self.detail_sub.configure(text=sub)

        hdr = ttk.Frame(self.lines_inner, style="Surface.TFrame")
        hdr.pack(fill="x", pady=(6, 2))
        ttk.Label(hdr, text="Item", style="SurfaceMuted.TLabel").pack(side="left")
        ttk.Label(hdr, text="Approve qty", style="SurfaceMuted.TLabel").pack(side="right")

        for line in order["lines"]:
            row = ttk.Frame(self.lines_inner, style="Surface.TFrame")
            row.pack(fill="x", pady=3)
            name = ttk.Label(row, text=line["item_name"], style="Surface.TLabel")
            name.pack(side="left")
            code = ttk.Label(row, text=f' {line["item_code"]}',
                             style="SurfaceMuted.TLabel", font=theme.FONT_MONO)
            code.pack(side="left")
            var = tk.IntVar(value=line["qty_requested"])
            self._qty_vars[line["item_id"]] = var
            spin = ttk.Spinbox(row, from_=0, to=line["qty_requested"],
                               textvariable=var, width=6)
            spin.pack(side="right")
            ttk.Label(row, text=f'of {line["qty_requested"]}  ',
                      style="SurfaceMuted.TLabel").pack(side="right")

        self.approve_btn.configure(state="normal")
        self.reject_btn.configure(state="normal")

    # ---- decisions -----------------------------------------------------------
    def _collect_overrides(self, order: dict) -> dict[int, int] | None:
        """None means 'approve everything as requested'; a dict means at
        least one line was cut down."""
        overrides = {}
        for line in order["lines"]:
            var = self._qty_vars.get(line["item_id"])
            if var is None:
                continue
            try:
                val = int(var.get())
            except (tk.TclError, ValueError):
                val = line["qty_requested"]
            val = max(0, min(val, line["qty_requested"]))
            if val != line["qty_requested"]:
                overrides[line["item_id"]] = val
        return overrides or None

    def _approve(self):
        order = self.selected_order
        if not order:
            return
        overrides = self._collect_overrides(order)
        reason = self.reason_var.get().strip()
        self._set_buttons(False)

        def work():
            try:
                self.api.approve_order(order["id"], reason=reason,
                                       line_overrides=overrides)
            except SessionExpired:
                return
            except ApiError as e:
                self.after(0, lambda: self._decision_failed("Approve failed", e))
                return
            self.after(0, lambda: self._decision_done(
                f"Order #{order['id']} approved."))
        threading.Thread(target=work, daemon=True).start()

    def _reject(self):
        order = self.selected_order
        if not order:
            return
        reason = self.reason_var.get().strip()
        if not reason:
            theme.show_error(self, "Reason required",
                             "A rejection needs a reason — the requester "
                             "sees it on their order.")
            self.reason_entry.focus_set()
            return
        if not messagebox.askyesno(
                "Reject order",
                f"Reject order #{order['id']} from {order['requester']}?",
                parent=self):
            return
        self._set_buttons(False)

        def work():
            try:
                self.api.reject_order(order["id"], reason=reason)
            except SessionExpired:
                return
            except ApiError as e:
                self.after(0, lambda: self._decision_failed("Reject failed", e))
                return
            self.after(0, lambda: self._decision_done(
                f"Order #{order['id']} rejected."))
        threading.Thread(target=work, daemon=True).start()

    def _decision_done(self, msg: str):
        if not self.winfo_exists():
            return
        self._set_buttons(True)
        self.selected_order = None
        self.refresh()
        self.event_generate("<<InventoryChanged>>")
        theme.show_info(self, "Done", msg)

    def _decision_failed(self, title: str, err: ApiError):
        if not self.winfo_exists():
            return
        self._set_buttons(True)
        theme.show_error(self, title, str(err))

    def _set_buttons(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.approve_btn.configure(state=state)
        self.reject_btn.configure(state=state)

    # ---- live polling ----------------------------------------------------------
    def _start_polling(self):
        def loop():
            while not self._poll_stop.is_set():
                try:
                    res = self.api.pending_orders_updates(self._since)
                    self._since = res["server_time"]
                    if res["orders"] and not self._poll_stop.is_set():
                        # something changed in the pending set: reload the
                        # full list (changes can also mean an order LEFT
                        # the pending set, which the delta doesn't say)
                        self.after(0, self.refresh)
                except SessionExpired:
                    return
                except ApiError:
                    if self._poll_stop.wait(4.0):
                        return
        self._poll_thread = threading.Thread(target=loop, daemon=True)
        self._poll_thread.start()

    def _on_destroy(self, evt):
        if evt.widget is self:
            self._poll_stop.set()
