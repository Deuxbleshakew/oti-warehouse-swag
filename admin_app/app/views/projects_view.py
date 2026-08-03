"""Admin project/event templates and shared project membership."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk, messagebox

from . import theme
from .widgets import SpinnerLabel, fade_in
from ..services.api_client import ApiClient, ApiError, SessionExpired

STATE_CODES = [
    "", "AL","AK","AZ","AR","CA","CO","CT","DE","DC","FL","GA","HI","ID","IL",
    "IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE",
    "NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","PR","RI","SC",
    "SD","TN","TX","UT","VT","VA","VI","WA","WV","WI","WY",
]


class ProjectsView(ttk.Frame):
    def __init__(self, parent, api: ApiClient):
        super().__init__(parent)
        self.api = api
        self.projects: list[dict] = []
        self._build()
        self.refresh()

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        ttk.Label(top, text="PROJECTS & EVENTS", style="Head.TLabel").pack(side="left")
        self.count_lbl = ttk.Label(top, text="", style="Muted.TLabel")
        self.count_lbl.pack(side="left", padx=10)
        self.spinner = SpinnerLabel(top, text="Loading", style="Muted.TLabel")
        self.spinner.pack(side="left")
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="right")
        self.delete_btn = ttk.Button(top, text="Remove", style="Danger.TButton",
                                     command=self._delete, state="disabled")
        self.delete_btn.pack(side="right", padx=6)
        self.members_btn = ttk.Button(top, text="Members…", command=self._members_dialog,
                                      state="disabled")
        self.members_btn.pack(side="right", padx=6)
        self.edit_btn = ttk.Button(top, text="Edit…", command=self._edit_dialog,
                                   state="disabled")
        self.edit_btn.pack(side="right", padx=6)
        ttk.Button(top, text="New Project…", style="Primary.TButton",
                   command=self._new_dialog).pack(side="right", padx=6)

        wrap = ttk.Frame(self)
        wrap.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        cols = ("name", "active", "address", "access", "members", "event", "state")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        headings = {
            "name": ("Project / Event", 250), "active": ("Available", 80),
            "address": ("Address", 90), "access": ("Visibility", 90),
            "members": ("People", 70), "event": ("Event date", 100),
            "state": ("State", 55),
        }
        for key, (label, width) in headings.items():
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.tag_configure("inactive", foreground=theme.MUTED)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _e: self._edit_dialog())

    def refresh(self):
        self.spinner.start("Loading projects")
        def work():
            try:
                rows = self.api.list_projects_admin()
            except SessionExpired:
                return
            except ApiError as exc:
                self.after(0, lambda: (self.spinner.stop(),
                                       theme.show_error(self, "Load failed", str(exc))))
                return
            self.after(0, lambda: self._set_projects(rows))
        threading.Thread(target=work, daemon=True).start()

    def _set_projects(self, rows: list[dict]):
        if not self.winfo_exists():
            return
        self.spinner.stop()
        self.projects = rows
        keep = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            members = row.get("members") or []
            tags = () if row.get("active") else ("inactive",)
            self.tree.insert("", "end", iid=str(row["id"]), values=(
                row.get("name") or "", "Yes" if row.get("active") else "No",
                (row.get("address_mode") or "variable").title(),
                "Members" if row.get("access_restricted") else "Everyone",
                len(members), row.get("event_date") or "—",
                row.get("shipping_state") or "—"), tags=tags)
        active = sum(1 for row in rows if row.get("active"))
        self.count_lbl.configure(text=f"{active} available · {len(rows)} total")
        if keep and self.tree.exists(keep[0]):
            self.tree.selection_set(keep[0])
        self._on_select()

    def _selected(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            return None
        pid = int(sel[0])
        return next((row for row in self.projects if row["id"] == pid), None)

    def _on_select(self, _event=None):
        state = "normal" if self._selected() else "disabled"
        self.edit_btn.configure(state=state)
        self.members_btn.configure(state=state)
        self.delete_btn.configure(state=state)

    def _new_dialog(self):
        self._project_dialog(None)

    def _edit_dialog(self):
        row = self._selected()
        if row:
            self._project_dialog(row)

    def _project_dialog(self, project: dict | None):
        win = tk.Toplevel(self)
        win.title("New Project / Event" if project is None else f"Edit {project['name']}")
        win.configure(bg=theme.PAPER)
        win.transient(self.winfo_toplevel())
        win.grab_set()
        form = ttk.Frame(win, padding=18)
        form.pack(fill="both", expand=True)
        form.columnconfigure(1, weight=1)

        values = project or {}
        vars_: dict[str, tk.Variable] = {
            "name": tk.StringVar(value=values.get("name", "")),
            "event_date": tk.StringVar(value=values.get("event_date", "")),
            "owner": tk.StringVar(value=values.get("owner", "")),
            "location": tk.StringVar(value=values.get("location", "")),
            "address_mode": tk.StringVar(value=values.get("address_mode", "variable")),
            "shipping_address1": tk.StringVar(value=values.get("shipping_address1", "")),
            "shipping_address2": tk.StringVar(value=values.get("shipping_address2", "")),
            "shipping_city": tk.StringVar(value=values.get("shipping_city", "")),
            "shipping_state": tk.StringVar(value=values.get("shipping_state", "")),
            "shipping_postal_code": tk.StringVar(value=values.get("shipping_postal_code", "")),
            "active": tk.BooleanVar(value=values.get("active", True)),
            "access_restricted": tk.BooleanVar(value=values.get("access_restricted", True)),
        }
        rows = [
            ("Project / event name", "name", "entry"),
            ("Event date (YYYY-MM-DD)", "event_date", "entry"),
            ("Owner / department", "owner", "entry"),
            ("Venue / location", "location", "entry"),
            ("Address behavior", "address_mode", "mode"),
            ("Address line 1", "shipping_address1", "entry"),
            ("Address line 2", "shipping_address2", "entry"),
            ("City", "shipping_city", "entry"),
            ("State", "shipping_state", "state"),
            ("ZIP / postal code", "shipping_postal_code", "entry"),
        ]
        address_widgets = []
        for idx, (label, key, kind) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=idx, column=0, sticky="w", padx=(0, 12), pady=4)
            if kind == "mode":
                widget = ttk.Combobox(form, textvariable=vars_[key], state="readonly",
                                      values=["variable", "fixed"], width=28)
            elif kind == "state":
                widget = ttk.Combobox(form, textvariable=vars_[key], values=STATE_CODES, width=28)
            else:
                widget = ttk.Entry(form, textvariable=vars_[key], width=34)
            widget.grid(row=idx, column=1, sticky="ew", pady=4)
            if key.startswith("shipping_"):
                address_widgets.append(widget)

        info_row = len(rows)
        ttk.Label(form, text="Variable means the requester supplies a different address per order.",
                  style="Muted.TLabel", wraplength=420).grid(
                      row=info_row, column=0, columnspan=2, sticky="w", pady=(2, 8))
        ttk.Checkbutton(form, text="Available in new-order project list",
                        variable=vars_["active"]).grid(row=info_row+1, column=0,
                                                       columnspan=2, sticky="w")
        ttk.Checkbutton(form, text="Only assigned project members can see this project",
                        variable=vars_["access_restricted"]).grid(
                            row=info_row+2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        error = ttk.Label(form, text="", foreground=theme.RUST, style="Muted.TLabel")
        error.grid(row=info_row+3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        buttons = ttk.Frame(form)
        buttons.grid(row=info_row+4, column=0, columnspan=2, sticky="e", pady=(14, 0))

        def update_address_state(*_):
            fixed = vars_["address_mode"].get() == "fixed"
            for widget in address_widgets:
                widget.configure(state="normal" if fixed else "disabled")
        vars_["address_mode"].trace_add("write", update_address_state)
        update_address_state()

        def save():
            name = vars_["name"].get().strip()
            if not name:
                error.configure(text="Project / event name is required.")
                return
            body = {key: (var.get().strip() if isinstance(var, tk.StringVar) else bool(var.get()))
                    for key, var in vars_.items()}
            if body["address_mode"] == "variable":
                # Keep saved defaults empty so requesters cannot mistake them for a fixed destination.
                for key in ("shipping_address1", "shipping_address2", "shipping_city",
                            "shipping_state", "shipping_postal_code"):
                    body[key] = ""
            call = (lambda: self.api.create_project(body)) if project is None else (
                lambda: self.api.update_project(project["id"], body))
            error.configure(text="Saving…")
            def work():
                try:
                    call()
                except SessionExpired:
                    return
                except ApiError as exc:
                    self.after(0, lambda: error.configure(text=str(exc)))
                    return
                self.after(0, lambda: (win.destroy(), self.refresh()))
            threading.Thread(target=work, daemon=True).start()

        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", style="Primary.TButton", command=save).pack(
            side="right", padx=(0, 8))
        win.bind("<Escape>", lambda _e: win.destroy())
        fade_in(win)

    def _members_dialog(self):
        project = self._selected()
        if not project:
            return
        win = tk.Toplevel(self)
        win.title(f"Project members · {project['name']}")
        win.geometry("620x520")
        win.configure(bg=theme.PAPER)
        win.transient(self.winfo_toplevel())
        win.grab_set()
        outer = ttk.Frame(win, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="WHO CAN SEE THIS PROJECT?", style="Head.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Viewers can follow status. Editors can also update pending orders. Owners have full project access.",
                  style="Muted.TLabel", wraplength=570).pack(anchor="w", pady=(2, 10))
        loading = SpinnerLabel(outer, text="Loading users", style="Muted.TLabel")
        loading.pack(anchor="w")
        table_wrap = ttk.Frame(outer)
        table_wrap.pack(fill="both", expand=True, pady=(8, 0))
        cols = ("include", "name", "username", "level")
        tree = ttk.Treeview(table_wrap, columns=cols, show="headings", selectmode="browse")
        for key, label, width in (("include", "Assigned", 75), ("name", "Name", 180),
                                  ("username", "Username", 130), ("level", "Access", 90)):
            tree.heading(key, text=label)
            tree.column(key, width=width, anchor="w")
        tree.pack(side="left", fill="both", expand=True)
        ttk.Scrollbar(table_wrap, orient="vertical", command=tree.yview).pack(side="right", fill="y")
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(10, 0))
        state: dict[int, dict] = {}
        error = ttk.Label(buttons, text="", style="Muted.TLabel", foreground=theme.RUST)
        error.pack(side="left")

        def redraw():
            tree.delete(*tree.get_children())
            for uid, row in sorted(state.items(), key=lambda pair: (pair[1]["name"].lower(), pair[1]["username"].lower())):
                tree.insert("", "end", iid=str(uid), values=(
                    "Yes" if row["assigned"] else "No", row["name"], row["username"],
                    row["level"].title() if row["assigned"] else "—"))

        def toggle():
            sel = tree.selection()
            if not sel:
                return
            row = state[int(sel[0])]
            row["assigned"] = not row["assigned"]
            redraw(); tree.selection_set(sel[0])

        def set_level(level):
            sel = tree.selection()
            if not sel:
                return
            row = state[int(sel[0])]
            row["assigned"] = True
            row["level"] = level
            redraw(); tree.selection_set(sel[0])

        ttk.Button(buttons, text="Assign / Remove", command=toggle).pack(side="left", padx=(8, 0))
        for level in ("viewer", "editor", "owner"):
            ttk.Button(buttons, text=level.title(), command=lambda level=level: set_level(level)).pack(side="left", padx=(6, 0))

        def save_members():
            members = [{"user_id": uid, "access_level": row["level"]}
                       for uid, row in state.items() if row["assigned"]]
            if not members and project.get("access_restricted"):
                if not messagebox.askyesno("No project members",
                                           "No users are assigned. Only admins will be able to see this restricted project. Continue?",
                                           parent=win):
                    return
            error.configure(text="Saving…")
            def work():
                try:
                    self.api.update_project_members(project["id"], members)
                except SessionExpired:
                    return
                except ApiError as exc:
                    self.after(0, lambda: error.configure(text=str(exc)))
                    return
                self.after(0, lambda: (win.destroy(), self.refresh()))
            threading.Thread(target=work, daemon=True).start()

        ttk.Button(buttons, text="Save Members", style="Primary.TButton",
                   command=save_members).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 8))

        current = {int(m["user_id"]): m for m in project.get("members") or []}
        def load():
            try:
                users = self.api.list_users()
            except SessionExpired:
                return
            except ApiError as exc:
                self.after(0, lambda: (loading.stop(), error.configure(text=str(exc))))
                return
            def finish():
                loading.stop(); loading.pack_forget()
                for user in users:
                    member = current.get(int(user["id"]))
                    state[int(user["id"])] = {
                        "name": user.get("full_name") or user["username"],
                        "username": user["username"], "assigned": bool(member),
                        "level": (member or {}).get("access_level", "viewer"),
                    }
                redraw()
            self.after(0, finish)
        loading.start("Loading users")
        threading.Thread(target=load, daemon=True).start()
        tree.bind("<Double-1>", lambda _e: toggle())
        win.bind("<Escape>", lambda _e: win.destroy())
        fade_in(win)

    def _delete(self):
        project = self._selected()
        if not project:
            return
        message = (f"Remove {project['name']} from new orders?\n\n"
                   "If any orders use it, the project is deactivated and remains in history. "
                   "Unused projects are permanently deleted.")
        if not messagebox.askyesno("Remove project", message, parent=self):
            return
        self.spinner.start("Removing project")
        def work():
            try:
                self.api.delete_project(project["id"])
            except SessionExpired:
                return
            except ApiError as exc:
                self.after(0, lambda: (self.spinner.stop(),
                                       theme.show_error(self, "Remove failed", str(exc))))
                return
            self.after(0, self.refresh)
        threading.Thread(target=work, daemon=True).start()
