"""
admin_app/app/services/api_client.py — the ONLY place the admin app talks
to the backend. No view imports requests or builds a URL; they all go
through this class.

Why it's built this way:
- Pure stdlib (urllib) — no pip dependency for the client machine beyond
  what the backend already needs, and nothing to break when this module
  gets lifted into another app (OpsDeck integration is the plan).
- No tkinter imports here, ever. This file must stay importable from a
  non-GUI context (scripts, another app's backend thread, tests).
- Long-poll support: pending_orders_updates() blocks up to ~25s server-
  side. Call it from a background thread, not the Tk mainloop.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
import urllib.parse
from typing import Any, Callable, Optional


class ApiError(Exception):
    """Raised for any non-2xx response. .status carries the HTTP code
    (0 for network-level failures) and str(e) is the human-readable
    detail from the backend when it sent one."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


class SessionExpired(ApiError):
    """401 after a successful login — token expired or was revoked.
    Views catch this specifically to bounce back to the login screen."""

    def __init__(self, detail: str = "Session expired. Log in again."):
        super().__init__(401, detail)


class ApiClient:
    def __init__(self, base_url: str = "http://localhost:8000",
                 timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token: Optional[str] = None
        self.user: Optional[dict] = None
        # set by the app to be told when the session dies mid-use
        # (e.g. show the login window again); called from whatever thread
        # hit the 401, so GUI code must marshal back to the Tk thread.
        self.on_session_expired: Optional[Callable[[], None]] = None

    # ---- plumbing -----------------------------------------------------------
    def _request(self, method: str, path: str, body: Any = None,
                 timeout: Optional[float] = None) -> Any:
        url = self.base_url + path
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = "Bearer " + self.token

        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(
                    req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = f"Request failed ({e.code})."
            try:
                payload = json.loads(e.read())
                if isinstance(payload.get("detail"), str):
                    detail = payload["detail"]
                elif payload.get("detail") is not None:
                    detail = json.dumps(payload["detail"])
            except Exception:
                pass
            if e.code == 401 and self.token is not None:
                self.token = None
                self.user = None
                if self.on_session_expired:
                    self.on_session_expired()
                raise SessionExpired(detail) from None
            raise ApiError(e.code, detail) from None
        except urllib.error.URLError as e:
            raise ApiError(0, f"Can't reach the server at {self.base_url} "
                              f"({e.reason}). Is the backend running?") from None

    def _get(self, path: str, timeout: Optional[float] = None) -> Any:
        return self._request("GET", path, timeout=timeout)

    def _post(self, path: str, body: Any = None) -> Any:
        return self._request("POST", path, body=body)

    def _put(self, path: str, body: Any = None) -> Any:
        return self._request("PUT", path, body=body)

    # ---- auth ---------------------------------------------------------------
    def login(self, username: str, password: str) -> dict:
        res = self._request("POST", "/auth/login",
                            {"username": username, "password": password})
        self.token = res["token"]
        self.user = res["user"]
        return res["user"]

    def logout(self) -> None:
        if self.token:
            try:
                self._post("/auth/logout")
            except ApiError:
                pass  # best-effort; local state clears regardless
        self.token = None
        self.user = None

    def health(self) -> bool:
        try:
            res = self._get("/health", timeout=3.0)
            return bool(res and res.get("status") == "ok")
        except ApiError:
            return False

    def has_role(self, *names: str) -> bool:
        if not self.user:
            return False
        return bool(set(self.user.get("roles", [])) & set(names))

    # ---- orders / approvals -------------------------------------------------
    def pending_orders(self) -> list[dict]:
        return self._get("/admin/orders/pending")

    def all_orders(self, status: str = "all") -> list[dict]:
        return self._get("/admin/orders?status=" + urllib.parse.quote(status))

    def edit_order(self, order_id: int, data: dict) -> dict:
        return self._put(f"/admin/orders/{order_id}", data)

    def delete_order(self, order_id: int) -> None:
        self._request("DELETE", f"/admin/orders/{order_id}")

    def pending_orders_updates(self, since: Optional[str]) -> dict:
        """Long-polls the backend for order changes across every status.
        Blocks up to ~25s server-side; run in a background thread. `since` is the
        server_time from the previous call (None to bootstrap)."""
        path = "/admin/orders/updates"
        if since:
            path += "?since=" + urllib.parse.quote(since)
        # server holds up to 25s; give the socket headroom past that
        return self._get(path, timeout=35.0)

    def approve_order(self, order_id: int, reason: str = "",
                      line_overrides: Optional[dict[int, int]] = None,
                      allow_negative: bool = False) -> dict:
        body: dict[str, Any] = {"reason": reason,
                                "allow_negative": allow_negative}
        if line_overrides:
            body["line_overrides"] = line_overrides
        return self._post(f"/admin/orders/{order_id}/approve", body)

    def reject_order(self, order_id: int, reason: str) -> dict:
        return self._post(f"/admin/orders/{order_id}/reject",
                          {"reason": reason})

    def pick_order(self, order_id: int) -> dict:
        return self._post(f"/admin/orders/{order_id}/pick", {})

    def fulfill_order(self, order_id: int, tracking_numbers: list[str],
                      photo_paths: list[str]) -> dict:
        import mimetypes
        import os
        import secrets
        boundary = "----otiswagproof" + secrets.token_hex(8)
        parts: list[bytes] = []
        parts.append((f"--{boundary}\r\n"
                      'Content-Disposition: form-data; name="tracking_numbers"\r\n\r\n'
                      + json.dumps(tracking_numbers) + "\r\n").encode())
        for path in photo_paths:
            with open(path, "rb") as fh:
                content = fh.read()
            filename = os.path.basename(path)
            ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            parts.append((f"--{boundary}\r\n"
                          f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
                          f"Content-Type: {ctype}\r\n\r\n").encode()
                         + content + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)
        headers = {"Accept": "application/json",
                   "Content-Type": f"multipart/form-data; boundary={boundary}"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(
            self.base_url + f"/admin/orders/{order_id}/fulfill",
            data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60.0) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = f"Request failed ({e.code})."
            try:
                payload = json.loads(e.read())
                if isinstance(payload.get("detail"), str):
                    detail = payload["detail"]
            except Exception:
                pass
            if e.code == 401 and self.token is not None:
                self.token = None
                self.user = None
                if self.on_session_expired:
                    self.on_session_expired()
                raise SessionExpired(detail) from None
            raise ApiError(e.code, detail) from None
        except urllib.error.URLError as e:
            raise ApiError(0, f"Can't reach the server ({e.reason}).") from None

    # ---- items / inventory --------------------------------------------------
    def all_items(self) -> list[dict]:
        return self._get("/admin/items")

    def create_item(self, data: dict) -> dict:
        return self._post("/admin/items", data)

    def update_item(self, item_id: int, data: dict) -> dict:
        return self._put(f"/admin/items/{item_id}", data)

    def adjust_inventory(self, item_id: int, delta: int, reason: str,
                         allow_negative: bool = False) -> dict:
        return self._post("/admin/inventory/adjust",
                          {"item_id": item_id, "delta": delta,
                           "reason": reason,
                           "allow_negative": allow_negative})

    def upload_item_image(self, item_id: int, filename: str,
                          content: bytes) -> dict:
        """Multipart upload built by hand — stdlib urllib has no multipart
        helper and pulling in `requests` just for this isn't worth a new
        dependency on every admin machine."""
        import secrets as _secrets
        boundary = "----otiswag" + _secrets.token_hex(8)
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
        ctype = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                 "png": "image/png", "gif": "image/gif",
                 "webp": "image/webp"}.get(ext, "application/octet-stream")
        head = (f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; '
                f'filename="{filename}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n").encode()
        body = head + content + f"\r\n--{boundary}--\r\n".encode()

        import urllib.request as _rq
        req = _rq.Request(
            self.base_url + f"/admin/items/{item_id}/images", data=body,
            headers={"Authorization": "Bearer " + (self.token or ""),
                     "Content-Type":
                         f"multipart/form-data; boundary={boundary}"},
            method="POST")
        import urllib.error as _err, json as _json
        try:
            with _rq.urlopen(req, timeout=30.0) as resp:
                return _json.loads(resp.read())
        except _err.HTTPError as e:
            detail = f"Upload failed ({e.code})."
            try:
                payload = _json.loads(e.read())
                if isinstance(payload.get("detail"), str):
                    detail = payload["detail"]
            except Exception:
                pass
            if e.code == 401 and self.token is not None:
                self.token = None
                self.user = None
                if self.on_session_expired:
                    self.on_session_expired()
                raise SessionExpired(detail) from None
            raise ApiError(e.code, detail) from None
        except _err.URLError as e:
            raise ApiError(0, f"Can't reach the server ({e.reason}).") from None

    def delete_item_image(self, item_id: int, image_id: int) -> dict:
        return self._request(
            "DELETE", f"/admin/items/{item_id}/images/{image_id}")

    def delete_item(self, item_id: int) -> None:
        self._request("DELETE", f"/admin/items/{item_id}")

    def inventory_transactions(self) -> list[dict]:
        return self._get("/admin/inventory/transactions")

    def update_inventory_transaction(self, transaction_id: int, delta: int,
                                     reason: str,
                                     allow_negative: bool = False) -> dict:
        return self._put(f"/admin/inventory/transactions/{transaction_id}",
                         {"delta": delta, "reason": reason,
                          "allow_negative": allow_negative})

    def delete_inventory_transaction(self, transaction_id: int) -> None:
        self._request("DELETE", f"/admin/inventory/transactions/{transaction_id}")

    def count_requests(self, status: str = "open") -> list[dict]:
        return self._get("/admin/count-requests?status="
                         + urllib.parse.quote(status))

    def resolve_count_request(self, request_id: int, physical_quantity: int,
                              resolution_note: str = "") -> dict:
        return self._post(f"/admin/count-requests/{request_id}/resolve",
                          {"physical_quantity": int(physical_quantity),
                           "resolution_note": resolution_note})

    def item_image_url(self, image_id: int) -> str:
        return (self.base_url + f"/item-images/{int(image_id)}?access_token=" +
                urllib.parse.quote(self.token or ""))

    def item_image_data_uri(self, image_id: int) -> str:
        """Download an item image with the bearer token and return a data URI.

        Pick slips embed this value so temporary HTML files never contain an
        authentication token and remain printable after the session ends.
        """
        import base64
        req = urllib.request.Request(
            self.base_url + f"/item-images/{int(image_id)}",
            headers={"Authorization": "Bearer " + (self.token or "")},
            method="GET")
        try:
            with urllib.request.urlopen(req, timeout=20.0) as resp:
                content = resp.read()
                content_type = resp.headers.get_content_type() or "image/png"
        except urllib.error.HTTPError as e:
            detail = f"Image download failed ({e.code})."
            try:
                payload = json.loads(e.read())
                if isinstance(payload.get("detail"), str):
                    detail = payload["detail"]
            except Exception:
                pass
            if e.code == 401 and self.token is not None:
                self.token = None
                self.user = None
                if self.on_session_expired:
                    self.on_session_expired()
                raise SessionExpired(detail) from None
            raise ApiError(e.code, detail) from None
        except urllib.error.URLError as e:
            raise ApiError(0, f"Can't reach the server ({e.reason}).") from None
        return (f"data:{content_type};base64," +
                base64.b64encode(content).decode("ascii"))


    # ---- NAV manual adjustments ---------------------------------------------
    def nav_adjustments(self, status: str = "pending") -> list[dict]:
        return self._get("/admin/nav-adjustments?status=" +
                         urllib.parse.quote(status))

    def update_nav_adjustment(self, task_id: int, status: str,
                              notes: str = "") -> dict:
        return self._put(f"/admin/nav-adjustments/{task_id}",
                         {"status": status, "notes": notes})

    # ---- users (admin only) -------------------------------------------------
    def list_users(self) -> list[dict]:
        return self._get("/admin/users")

    def create_user(self, username: str, full_name: str, password: str,
                    roles: list[str]) -> dict:
        return self._post("/admin/users",
                          {"username": username, "full_name": full_name,
                           "password": password, "roles": roles})

    def update_user(self, user_id: int, **fields) -> dict:
        return self._put(f"/admin/users/{user_id}", fields)

    def delete_user(self, user_id: int) -> None:
        self._request("DELETE", f"/admin/users/{user_id}")

    # ---- projects / shared status ------------------------------------------
    def list_projects_admin(self) -> list[dict]:
        return self._get("/admin/projects")

    def create_project(self, data: dict) -> dict:
        return self._post("/admin/projects", data)

    def update_project(self, project_id: int, data: dict) -> dict:
        return self._put(f"/admin/projects/{project_id}", data)

    def update_project_members(self, project_id: int,
                               members: list[dict]) -> dict:
        return self._put(f"/admin/projects/{project_id}/members",
                         {"members": members})

    def delete_project(self, project_id: int) -> None:
        self._request("DELETE", f"/admin/projects/{project_id}")

    # ---- catalog visibility -------------------------------------------------
    def catalog_options(self) -> dict:
        return self._get("/admin/catalog-options")

    def get_catalog_permissions(self, user_id: int) -> dict:
        return self._get(f"/admin/users/{user_id}/catalog-permissions")

    def update_catalog_permissions(self, user_id: int, data: dict) -> dict:
        return self._put(f"/admin/users/{user_id}/catalog-permissions", data)

