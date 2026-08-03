"""
admin_app/main.py — Oti-Warehouse Swag admin desktop app.

Run from the swag_system folder:
    python admin_app\\main.py          (Windows)
    python admin_app/main.py           (elsewhere)

Login window -> tabbed main window (Orders, Inventory, History, Users). Talks to
the backend API only — it never opens the database file, so it can run on
any machine that can reach the backend, exactly like the browser frontend.
The server address is remembered in a small settings file next to this
script.
"""
import json
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk

# allow running as a plain script from the repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin_app.app.services.api_client import ApiClient, ApiError  # noqa: E402
from admin_app.app.views import theme  # noqa: E402
from admin_app.app.views.orders_view import OrdersView  # noqa: E402
from admin_app.app.views.inventory_view import InventoryView  # noqa: E402
from admin_app.app.views.users_view import UsersView  # noqa: E402
from admin_app.app.views.projects_view import ProjectsView  # noqa: E402
from admin_app.app.views.inventory_history_view import InventoryHistoryView  # noqa: E402
from admin_app.app.views.nav_adjustments_view import NavAdjustmentsView  # noqa: E402
from admin_app.app.views.widgets import SpinnerLabel  # noqa: E402

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "settings.json")
DEFAULT_SERVER = "http://localhost:8000"


def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        pass  # settings are a convenience, never fatal


class LoginWindow(ttk.Frame):
    def __init__(self, root: tk.Tk, on_success):
        super().__init__(root, padding=28)
        self.root = root
        self.on_success = on_success
        settings = load_settings()

        self.pack(expand=True)
        ttk.Label(self, text="📦 OTI-WAREHOUSE SWAG 1.0",
                  font=("Segoe UI", 15, "bold")).grid(row=0, column=0,
                                                      columnspan=2, pady=(0, 2))
        ttk.Label(self, text="Admin 1.0 — approvals & inventory",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=2,
                                             pady=(0, 18))

        ttk.Label(self, text="Server").grid(row=2, column=0, sticky="w",
                                             pady=4, padx=(0, 10))
        self.server_var = tk.StringVar(
            value=settings.get("server", DEFAULT_SERVER))
        ttk.Entry(self, textvariable=self.server_var, width=28).grid(
            row=2, column=1, sticky="ew", pady=4)

        ttk.Label(self, text="Username").grid(row=3, column=0, sticky="w",
                                               pady=4, padx=(0, 10))
        self.user_var = tk.StringVar(value=settings.get("username", ""))
        user_entry = ttk.Entry(self, textvariable=self.user_var, width=28)
        user_entry.grid(row=3, column=1, sticky="ew", pady=4)

        ttk.Label(self, text="Password").grid(row=4, column=0, sticky="w",
                                               pady=4, padx=(0, 10))
        self.pass_var = tk.StringVar()
        pass_entry = ttk.Entry(self, textvariable=self.pass_var, width=28,
                               show="•")
        pass_entry.grid(row=4, column=1, sticky="ew", pady=4)

        self.err_lbl = ttk.Label(self, text="", style="Muted.TLabel",
                                 foreground=theme.RUST, wraplength=260)
        self.err_lbl.grid(row=5, column=0, columnspan=2, sticky="w",
                          pady=(6, 0))

        self.login_btn = ttk.Button(self, text="Log in",
                                    style="Primary.TButton",
                                    command=self._login)
        self.login_btn.grid(row=6, column=0, columnspan=2, sticky="ew",
                            pady=(14, 0))
        self.login_spinner = SpinnerLabel(self, text="Signing in",
                                          style="Muted.TLabel")
        self.login_spinner.grid(row=7, column=0, columnspan=2, pady=(7, 0))

        (user_entry if not self.user_var.get() else pass_entry).focus_set()
        root.bind("<Return>", lambda e: self._login())

    def _login(self):
        server = self.server_var.get().strip().rstrip("/") or DEFAULT_SERVER
        username = self.user_var.get().strip()
        password = self.pass_var.get()
        if not username or not password:
            self.err_lbl.configure(text="Enter a username and password.")
            return
        self.err_lbl.configure(text="")
        self.login_btn.configure(state="disabled")
        self.login_spinner.start("Signing in")

        def work():
            api = ApiClient(server)
            try:
                user = api.login(username, password)
            except ApiError as exc:
                self.after(0, lambda: self._login_failed(str(exc)))
                return
            if not api.has_role("admin", "approver"):
                try:
                    api.logout()
                except Exception:
                    pass
                message = (f"'{username}' can order from the catalog, but this "
                           "app needs the approver or admin role.")
                self.after(0, lambda: self._login_failed(message))
                return
            self.after(0, lambda: self._login_succeeded(
                api, user, server, username))

        threading.Thread(target=work, daemon=True).start()

    def _login_failed(self, message: str):
        if not self.winfo_exists():
            return
        self.err_lbl.configure(text=message)
        self.login_btn.configure(state="normal")
        self.login_spinner.stop()

    def _login_succeeded(self, api, user, server: str, username: str):
        if not self.winfo_exists():
            return
        save_settings({"server": server, "username": username})
        self.login_spinner.stop()
        self.root.unbind("<Return>")
        self.on_success(api, user)


class MainWindow(ttk.Frame):
    def __init__(self, root: tk.Tk, api: ApiClient, user: dict, on_logout):
        super().__init__(root)
        self.root = root
        self.api = api
        self.on_logout = on_logout
        self.pack(fill="both", expand=True)

        bar = tk.Frame(self, bg=theme.BAR)
        bar.pack(fill="x")
        tk.Label(bar, text="📦 OTI-WAREHOUSE SWAG  1.0 — ADMIN", bg=theme.BAR,
                 fg=theme.INK, font=("Segoe UI", 11, "bold"),
                 padx=14, pady=10).pack(side="left")
        tk.Frame(self, bg=theme.SAFETY, height=4).pack(fill="x")

        who = f'{user.get("full_name") or user["username"]}  ·  ' \
              f'{", ".join(user.get("roles", []))}'
        tk.Label(bar, text=who, bg=theme.BAR, fg=theme.MUTED,
                 font=("Segoe UI", 9)).pack(side="right", padx=(0, 6))
        logout = tk.Label(bar, text="Log out", bg=theme.BAR, fg=theme.INK,
                          font=("Segoe UI", 9, "underline"), cursor="hand2",
                          padx=12)
        logout.pack(side="right")
        logout.bind("<Button-1>", lambda e: self._logout())

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        self.orders_view = OrdersView(nb, api)
        self.inventory_view = InventoryView(nb, api)
        nb.add(self.orders_view, text="  Orders  ")
        nb.add(self.inventory_view, text="  Inventory  ")
        if api.has_role("admin"):
            self.inventory_history_view = InventoryHistoryView(nb, api)
            nb.add(self.inventory_history_view, text="  Inventory History  ")
            self.nav_adjustments_view = NavAdjustmentsView(nb, api)
            nb.add(self.nav_adjustments_view, text="  NAV Adjustments  ")
            self.projects_view = ProjectsView(nb, api)
            nb.add(self.projects_view, text="  Projects  ")
            self.users_view = UsersView(nb, api)
            nb.add(self.users_view, text="  Users & Access  ")

        # approving an order changes stock — keep the inventory tab honest
        self.orders_view.bind("<<InventoryChanged>>",
                              lambda e: self.inventory_view.refresh())
        if api.has_role("admin"):
            self.orders_view.bind("<<InventoryChanged>>",
                                  lambda e: self.inventory_history_view.refresh())
            self.inventory_history_view.bind("<<InventoryChanged>>",
                                             lambda e: self.inventory_view.refresh())

        # a dead session anywhere bounces the whole app back to login
        api.on_session_expired = lambda: self.after(0, self._logout)

    def _logout(self):
        try:
            self.api.logout()
        except Exception:
            pass
        self.on_logout()


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Oti-Warehouse Swag 1.0 — Admin")
        assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
        try:
            self._app_icon = tk.PhotoImage(file=os.path.join(assets_dir, "shipping_box.png"))
            self.root.iconphoto(True, self._app_icon)
        except tk.TclError:
            self._app_icon = None
        if sys.platform.startswith("win"):
            try:
                self.root.iconbitmap(os.path.join(assets_dir, "shipping_box.ico"))
            except tk.TclError:
                pass
        self.root.geometry("1080x640")
        self.root.minsize(860, 520)
        self.root.configure(bg=theme.PAPER)
        theme.apply_theme(self.root)
        self.current: ttk.Frame | None = None
        self.show_login()

    def _swap(self, frame: ttk.Frame):
        if self.current is not None:
            self.current.destroy()
        self.current = frame

    def show_login(self):
        self.root.geometry("420x360")
        self._swap(LoginWindow(self.root, self.show_main))

    def show_main(self, api: ApiClient, user: dict):
        self.root.geometry("1080x640")
        self._swap(MainWindow(self.root, api, user, self.show_login))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
