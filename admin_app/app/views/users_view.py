"""
admin_app/app/views/users_view.py — the Users tab (admin role only).

Create users, edit roles, reset passwords, deactivate accounts, and remove
accounts while keeping historical orders and audit records attributable.
Same threading rules as the other views.
"""
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from . import theme
from .widgets import SpinnerLabel, fade_in
from ..services.api_client import ApiClient, ApiError, SessionExpired

ALL_ROLES = ["requester", "approver", "admin"]
ROLE_HELP = {
    "requester": "Can browse the catalog and place orders (the web page).",
    "approver": "Can approve/reject orders in this app.",
    "admin": "Everything: items, stock, users, audit log.",
}


class UsersView(ttk.Frame):
    def __init__(self, parent, api: ApiClient):
        super().__init__(parent)
        self.api = api
        self.users: list[dict] = []
        self._build()
        self.refresh()

    # ---- UI ------------------------------------------------------------------
    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        ttk.Label(top, text="USERS", style="Head.TLabel").pack(side="left")
        self.count_lbl = ttk.Label(top, text="", style="Muted.TLabel")
        self.count_lbl.pack(side="left", padx=10)
        self.spinner = SpinnerLabel(top, text="Loading", style="Muted.TLabel")
        self.spinner.pack(side="left")
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="right")
        self.delete_btn = ttk.Button(top, text="Delete", style="Danger.TButton",
                                     command=self._delete_user, state="disabled")
        self.delete_btn.pack(side="right", padx=6)
        self.access_btn = ttk.Button(top, text="Catalog Access…",
                                     command=self._catalog_access_dialog,
                                     state="disabled")
        self.access_btn.pack(side="right", padx=6)
        self.edit_btn = ttk.Button(top, text="Edit…", command=self._edit_dialog,
                                   state="disabled")
        self.edit_btn.pack(side="right", padx=6)
        ttk.Button(top, text="New User…", style="Primary.TButton",
                   command=self._new_dialog).pack(side="right", padx=6)
        ttk.Label(top, text="Passwords are hidden after save; use Edit to reset.",
                  style="Muted.TLabel").pack(side="right", padx=12)

        wrap = ttk.Frame(self)
        wrap.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        cols = ("username", "full_name", "roles", "catalog", "active")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings",
                                 selectmode="browse")
        headings = {"username": ("Username", 140),
                    "full_name": ("Full name", 200),
                    "roles": ("Roles", 190), "catalog": ("Catalog", 100),
                    "active": ("Active", 70)}
        for c, (label, width) in headings.items():
            self.tree.heading(c, text=label)
            self.tree.column(c, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")

        self.tree.tag_configure("inactive", foreground=theme.MUTED)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda e: self._edit_dialog())

    # ---- data ---------------------------------------------------------------
    def refresh(self):
        self.spinner.start("Loading users")
        def work():
            try:
                users = self.api.list_users()
            except SessionExpired:
                return
            except ApiError as e:
                self.after(0, lambda: (self.spinner.stop(),
                                       theme.show_error(self, "Load failed", str(e))))
                return
            self.after(0, lambda: self._set_users(users))
        threading.Thread(target=work, daemon=True).start()

    def _set_users(self, users: list[dict]):
        if not self.winfo_exists():
            return
        self.spinner.stop()
        self.users = users
        keep = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for u in users:
            tags = () if u["active"] else ("inactive",)
            self.tree.insert("", "end", iid=str(u["id"]), values=(
                u["username"], u["full_name"], ", ".join(u["roles"]),
                "Only selected items" if u.get("catalog_access_mode") == "restricted" else "All items",
                "yes" if u["active"] else "no"), tags=tags)
        active_n = sum(1 for u in users if u["active"])
        self.count_lbl.configure(text=f"{len(users)} users · {active_n} active")
        if keep and self.tree.exists(keep[0]):
            self.tree.selection_set(keep[0])
        else:
            self._on_select()

    def _selected_user(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            return None
        uid = int(sel[0])
        return next((u for u in self.users if u["id"] == uid), None)

    def _on_select(self, _evt=None):
        state = "normal" if self.tree.selection() else "disabled"
        self.edit_btn.configure(state=state)
        self.access_btn.configure(state=state)
        selected = self._selected_user()
        is_self = bool(selected and self.api.user and selected["id"] == self.api.user["id"])
        self.delete_btn.configure(state="disabled" if is_self else state)

    def _catalog_access_dialog(self):
        user = self._selected_user()
        if not user:
            return
        win = tk.Toplevel(self)
        win.title(f"Catalog access · {user['username']}")
        win.geometry("900x620")
        win.configure(bg=theme.PAPER)
        win.transient(self.winfo_toplevel())
        win.grab_set()
        outer = ttk.Frame(win, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="CATALOG VISIBILITY", style="Head.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text=("Choose exactly what this user can see and order. Hidden products are also "
                  "blocked by the API, direct links, search, favorites, and recent-item lists."),
            style="Muted.TLabel", wraplength=820).pack(anchor="w", pady=(2, 10))

        mode = tk.StringVar(value=user.get("catalog_access_mode") or "all")
        modes = ttk.Frame(outer, style="Surface.TFrame", padding=10)
        modes.pack(fill="x")
        ttk.Radiobutton(modes, text="All active catalog items", variable=mode,
                        value="all").pack(side="left")
        ttk.Radiobutton(modes, text="Only selected catalog items", variable=mode,
                        value="restricted").pack(side="left", padx=(18, 0))

        loading = SpinnerLabel(outer, text="Loading catalog", style="Muted.TLabel")
        loading.pack(anchor="w", pady=(8, 0))
        panels = ttk.Frame(outer)
        panels.pack(fill="both", expand=True, pady=(8, 0))
        for i in range(3):
            panels.columnconfigure(i, weight=1)
        panels.rowconfigure(0, weight=1)

        listboxes = {}
        labels = (("categories", "CATEGORIES"), ("brands", "BRANDS"),
                  ("items", "INDIVIDUAL ITEMS"))
        for col, (key, label) in enumerate(labels):
            card = ttk.Frame(panels, style="Surface.TFrame", padding=10)
            card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 5, 0))
            ttk.Label(card, text=label, style="SurfaceHead.TLabel").pack(anchor="w")
            lb = tk.Listbox(card, selectmode="multiple", exportselection=False,
                            bg=theme.SURFACE2, fg=theme.INK,
                            selectbackground=theme.SAFETY,
                            selectforeground=theme.SAFETY_INK,
                            highlightthickness=1, highlightbackground=theme.LINE,
                            borderwidth=0, font=theme.FONT_BASE)
            lb.pack(fill="both", expand=True, pady=(8, 0))
            listboxes[key] = lb

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(12, 0))
        error = ttk.Label(footer, text="", style="Muted.TLabel", foreground=theme.RUST)
        error.pack(side="left")
        ttk.Button(footer, text="Cancel", command=win.destroy).pack(side="right")
        save_btn = ttk.Button(footer, text="Save Access", style="Primary.TButton",
                              state="disabled")
        save_btn.pack(side="right", padx=(0, 8))
        state = {"options": None, "permissions": None}

        def select_values(lb, displayed, selected_values):
            selected_norm = {str(value).strip().casefold() for value in selected_values}
            for index, value in enumerate(displayed):
                if str(value).strip().casefold() in selected_norm:
                    lb.selection_set(index)

        def finish_load(options, permissions):
            if not win.winfo_exists():
                return
            loading.stop(); loading.pack_forget()
            state["options"] = options
            state["permissions"] = permissions
            categories = options.get("categories") or []
            brands = options.get("brands") or []
            items = options.get("items") or []
            for value in categories:
                listboxes["categories"].insert("end", value)
            for value in brands:
                listboxes["brands"].insert("end", value)
            item_labels = [f"{row['code']} · {row['name']}" for row in items]
            for value in item_labels:
                listboxes["items"].insert("end", value)
            select_values(listboxes["categories"], categories,
                          permissions.get("categories") or [])
            select_values(listboxes["brands"], brands,
                          permissions.get("brands") or [])
            item_ids = {int(value) for value in permissions.get("item_ids") or []}
            for index, row in enumerate(items):
                if int(row["id"]) in item_ids:
                    listboxes["items"].selection_set(index)
            mode.set(permissions.get("catalog_access_mode") or "all")
            save_btn.configure(state="normal")

        def load():
            try:
                options = self.api.catalog_options()
                permissions = self.api.get_catalog_permissions(user["id"])
            except SessionExpired:
                return
            except ApiError as exc:
                self.after(0, lambda: (loading.stop(), error.configure(text=str(exc))))
                return
            self.after(0, lambda: finish_load(options, permissions))

        def chosen(lb, values):
            return [values[index] for index in lb.curselection()]

        def save_access():
            options = state["options"] or {}
            categories = options.get("categories") or []
            brands = options.get("brands") or []
            items = options.get("items") or []
            body = {
                "catalog_access_mode": mode.get(),
                "categories": chosen(listboxes["categories"], categories),
                "brands": chosen(listboxes["brands"], brands),
                "item_ids": [int(items[index]["id"])
                             for index in listboxes["items"].curselection()],
            }
            if body["catalog_access_mode"] == "restricted" and not (
                    body["categories"] or body["brands"] or body["item_ids"]):
                if not messagebox.askyesno(
                        "Hide the entire catalog",
                        "No categories, brands, or items are selected. This user will see no catalog products. Continue?",
                        parent=win):
                    return
            save_btn.configure(state="disabled")
            error.configure(text="Saving…")
            def work():
                try:
                    self.api.update_catalog_permissions(user["id"], body)
                except SessionExpired:
                    return
                except ApiError as exc:
                    self.after(0, lambda: (save_btn.configure(state="normal"),
                                           error.configure(text=str(exc))))
                    return
                self.after(0, lambda: (win.destroy(), self.refresh()))
            threading.Thread(target=work, daemon=True).start()

        save_btn.configure(command=save_access)
        loading.start("Loading catalog")
        threading.Thread(target=load, daemon=True).start()
        win.bind("<Escape>", lambda _e: win.destroy())
        fade_in(win)

    def _delete_user(self):
        user = self._selected_user()
        if not user:
            return
        if self.api.user and user["id"] == self.api.user["id"]:
            theme.show_error(self, "Delete blocked", "You cannot delete your own account.")
            return
        if not messagebox.askyesno(
                "Delete user",
                f"Delete {user['username']}?\n\n"
                "Their login will be removed immediately. Existing orders, "
                "inventory actions, approvals, and audit records will remain "
                "labeled as a deleted user.", parent=self):
            return
        self.spinner.start("Deleting user")

        def work():
            try:
                self.api.delete_user(user["id"])
            except SessionExpired:
                return
            except ApiError as exc:
                self.after(0, lambda: (self.spinner.stop(),
                                       theme.show_error(self, "Delete failed", str(exc))))
                return
            self.after(0, self.refresh)
        threading.Thread(target=work, daemon=True).start()

    # ---- dialogs -------------------------------------------------------------
    def _new_dialog(self):
        self._user_dialog(None)

    def _edit_dialog(self):
        user = self._selected_user()
        if user:
            self._user_dialog(user)

    def _user_dialog(self, user: dict | None):
        win = tk.Toplevel(self)
        win.title("New User" if user is None else f"Edit {user['username']}")
        win.configure(bg=theme.PAPER)
        win.transient(self.winfo_toplevel())
        win.grab_set()
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)

        r = 0
        ttk.Label(frm, text="Username").grid(row=r, column=0, sticky="w",
                                              pady=3, padx=(0, 10))
        user_var = tk.StringVar(value="" if user is None else user["username"])
        user_ent = ttk.Entry(frm, textvariable=user_var, width=26)
        user_ent.grid(row=r, column=1, sticky="w", pady=3)
        if user is not None:
            user_ent.configure(state="disabled")   # usernames are permanent
        r += 1

        ttk.Label(frm, text="Full name").grid(row=r, column=0, sticky="w",
                                               pady=3, padx=(0, 10))
        name_var = tk.StringVar(value="" if user is None else user["full_name"])
        ttk.Entry(frm, textvariable=name_var, width=26).grid(
            row=r, column=1, sticky="w", pady=3)
        r += 1

        pass_label = "Password" if user is None else "New password"
        ttk.Label(frm, text=pass_label).grid(row=r, column=0, sticky="w",
                                              pady=3, padx=(0, 10))
        pass_var = tk.StringVar()
        ttk.Entry(frm, textvariable=pass_var, width=26, show="•").grid(
            row=r, column=1, sticky="w", pady=3)
        r += 1
        if user is not None:
            ttk.Label(frm, text="Leave blank to keep their current password.",
                      style="Muted.TLabel").grid(row=r, column=0, columnspan=2,
                                                 sticky="w")
            r += 1

        ttk.Label(frm, text="Access").grid(row=r, column=0, sticky="nw",
                                            pady=(10, 3), padx=(0, 10))
        roles_frame = ttk.Frame(frm)
        roles_frame.grid(row=r, column=1, sticky="w", pady=(10, 3))
        role_vars: dict[str, tk.BooleanVar] = {}
        current_roles = set() if user is None else set(user["roles"])
        for role in ALL_ROLES:
            var = tk.BooleanVar(value=(role in current_roles) if user
                                else (role == "requester"))
            role_vars[role] = var
            cb = ttk.Checkbutton(roles_frame, text=role.capitalize(),
                                 variable=var)
            cb.pack(anchor="w")
            ttk.Label(roles_frame, text=ROLE_HELP[role],
                      style="Muted.TLabel").pack(anchor="w", padx=(22, 0))
        r += 1

        active_var = tk.BooleanVar(value=True if user is None
                                   else user["active"])
        if user is not None:
            ttk.Checkbutton(frm, text="Active (can log in)",
                            variable=active_var).grid(
                row=r, column=0, columnspan=2, sticky="w", pady=(8, 0))
            r += 1
            is_self = (self.api.user and
                       self.api.user["id"] == user["id"])
            if is_self:
                ttk.Label(frm, text="This is your own account — you can't "
                                    "lock yourself out or drop your own "
                                    "admin role here.",
                          style="Muted.TLabel", wraplength=280).grid(
                    row=r, column=0, columnspan=2, sticky="w")
                r += 1

        err_lbl = ttk.Label(frm, text="", style="Muted.TLabel",
                            foreground=theme.RUST, wraplength=300)
        err_lbl.grid(row=r, column=0, columnspan=2, sticky="w", pady=(8, 0))
        r += 1

        btns = ttk.Frame(frm)
        btns.grid(row=r, column=0, columnspan=2, sticky="e", pady=(12, 0))

        def save():
            roles = [name for name, var in role_vars.items() if var.get()]
            if not roles:
                err_lbl.configure(text="Pick at least one role.")
                return

            if user is None:
                username = user_var.get().strip()
                password = pass_var.get()
                if not username:
                    err_lbl.configure(text="Username is required.")
                    return
                if len(password) < 8:
                    err_lbl.configure(text="Password needs at least 8 characters.")
                    return
                body = {"username": username,
                        "full_name": name_var.get().strip(),
                        "password": password, "roles": roles}
                call = lambda: self.api.create_user(**body)
            else:
                # guard rails against locking yourself out
                is_self = (self.api.user and
                           self.api.user["id"] == user["id"])
                if is_self and not active_var.get():
                    err_lbl.configure(text="You can't deactivate your own account.")
                    return
                if is_self and "admin" not in roles:
                    err_lbl.configure(text="You can't remove your own admin role.")
                    return
                body = {"full_name": name_var.get().strip(),
                        "active": bool(active_var.get()), "roles": roles}
                pw = pass_var.get()
                if pw:
                    if len(pw) < 8:
                        err_lbl.configure(text="Password needs at least 8 characters.")
                        return
                    body["password"] = pw
                call = lambda: self.api.update_user(user["id"], **body)

            def work():
                try:
                    call()
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
        (user_ent if user is None else frm).focus_set()
        fade_in(win)
