"""Antigravity account pool and panel-facing operations."""
from __future__ import annotations

import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .client import (CALLBACK_PORT, AntigravityAuthError, AntigravityClient,
                     AntigravityHTTPError)
from . import store

_SERVICE = None
_SERVICE_LOCK = threading.Lock()


class _CallbackServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True


class _CallbackHandler(BaseHTTPRequestHandler):
    server_version = "aigpt-antigravity-callback"

    def do_GET(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        service: AntigravityService = self.server.service  # type: ignore[attr-defined]
        ok = service.receive_callback((query.get("code") or [""])[0], (query.get("state") or [""])[0],
                                      (query.get("error") or [""])[0])
        body = b"<h1>Login received</h1><p>You can close this window.</p>" if ok else b"<h1>Login failed</h1><p>You can close this window.</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        pass


class AntigravityService:
    def __init__(self, client: AntigravityClient | None = None):
        self.client = client or AntigravityClient()
        self._lock = threading.RLock()
        self._login: dict[str, Any] | None = None
        self._callback_server: _CallbackServer | None = None
        self._active_by_model: dict[str, str] = {}

    def start_login(self) -> dict[str, str]:
        with self._lock:
            if self._login and self._login.get("state") == "waiting" and time.time() - self._login["started"] < 300:
                return {"authorize_url": self._login["url"], "state": self._login["oauth_state"]}
            if self._login and self._login.get("state") == "processing" and time.time() - self._login["started"] < 300:
                raise RuntimeError("Antigravity login is still processing")
            if self._login and time.time() - self._login.get("started", 0) >= 300:
                self._login.update({"state": "error", "error": "OAuth flow timed out"})
                self._stop_callback()
            # Recover from an orphan listener left behind by an interrupted
            # login or an older server build. A live waiting session was
            # returned above; every other listener is safe to replace.
            if self._callback_server is not None:
                self._stop_callback()
            oauth_state = secrets.token_urlsafe(24)
            redirect = f"http://localhost:{CALLBACK_PORT}/oauth-callback"
            try:
                server = _CallbackServer(("localhost", CALLBACK_PORT), _CallbackHandler)
            except OSError as exc:
                raise RuntimeError(f"port {CALLBACK_PORT} is busy — close another Antigravity login") from exc
            server.service = self  # type: ignore[attr-defined]
            self._callback_server = server
            self._login = {"state": "waiting", "oauth_state": oauth_state,
                           "url": self.client.auth_url(oauth_state, redirect), "started": time.time()}
            threading.Thread(target=server.serve_forever, daemon=True).start()
            return {"authorize_url": self._login["url"], "state": oauth_state}

    def receive_callback(self, code: str, state: str, error: str) -> bool:
        with self._lock:
            session = self._login
            if not session or session.get("state") != "waiting":
                return False
            if state != session["oauth_state"]:
                session.update({"state": "error", "error": "OAuth state mismatch"})
                self._stop_callback()
                return False
            if error or not code:
                session.update({"state": "error", "error": error or "authorization code missing"})
                self._stop_callback()
                return False
            session["state"] = "processing"
            threading.Thread(target=self._finish_login, args=(code,), daemon=True).start()
            return True

    def _finish_login(self, code: str) -> None:
        try:
            redirect = f"http://localhost:{CALLBACK_PORT}/oauth-callback"
            tokens = self.client.exchange_code(code, redirect)
            access = str(tokens["access_token"])
            email = self.client.user_email(access)
            # Persist the valid Google identity before Cloud Code setup. If
            # project onboarding is temporarily rejected, the account remains
            # visible in the UI with an actionable setup error and can be
            # hydrated again without losing the OAuth grant.
            account = {"email": email, "access_token": access, "refresh_token": tokens.get("refresh_token", ""),
                       "expires_at": time.time() + int(tokens.get("expires_in") or 3600),
                       "project_id": "", "models": [], "credits": {"status": "unknown"},
                       "cooldowns": {}, "status": "setup"}
            store.upsert(account)
            project = self.client.project_id(access)
            models = self.client.models(access, project)
            # Credits are an observation only. Some Antigravity accounts do
            # not expose the optional field yet; login must still succeed.
            try:
                credits = self.client.credits(access)
            except Exception:
                credits = {"status": "unknown"}
            account.update({"project_id": project, "models": models, "credits": credits,
                            "status": "available"})
            store.upsert(account)
            with self._lock:
                if self._login:
                    self._login.update({"state": "done", "email": email})
        except Exception as exc:
            with self._lock:
                if self._login:
                    self._login.update({"state": "error", "error": _safe_error(exc)})
        finally:
            with self._lock:
                self._stop_callback()

    def _stop_callback(self) -> None:
        server = self._callback_server
        self._callback_server = None
        if server:
            server.shutdown()
            server.server_close()

    def login_status(self) -> dict[str, Any]:
        with self._lock:
            if not self._login:
                return {"state": "idle"}
            if time.time() - self._login["started"] > 300 and self._login.get("state") not in ("done", "error"):
                self._login.update({"state": "error", "error": "OAuth flow timed out"})
                self._stop_callback()
            return {k: self._login[k] for k in ("state", "email", "error") if k in self._login}

    def _refresh_if_needed(self, account: dict[str, Any]) -> str:
        if float(account.get("expires_at") or 0) > time.time() + 300:
            return str(account.get("access_token") or "")
        refresh = str(account.get("refresh_token") or "")
        if not refresh:
            account["status"] = "reauth"
            store.upsert(account)
            raise AntigravityAuthError(401, "Antigravity account needs login again")
        try:
            data = self.client.refresh(refresh)
        except AntigravityAuthError:
            account["status"] = "reauth"
            store.upsert(account)
            raise
        account["access_token"] = data["access_token"]
        if data.get("refresh_token"):
            account["refresh_token"] = data["refresh_token"]
        account["expires_at"] = time.time() + int(data.get("expires_in") or 3600)
        store.upsert(account)
        return str(account["access_token"])

    def _hydrate(self, account: dict[str, Any], *, refresh_models: bool = False) -> None:
        token = self._refresh_if_needed(account)
        if not account.get("project_id"):
            account["project_id"] = self.client.project_id(token)
        if refresh_models or not account.get("models"):
            account["models"] = self.client.models(token, account["project_id"])
        try:
            account["credits"] = self.client.credits(token)
        except Exception:
            account.setdefault("credits", {"status": "unknown"})
        store.upsert(account)

    def accounts(self) -> list[dict[str, Any]]:
        result = []
        for account in store.load():
            try:
                self._hydrate(account, refresh_models=True)
            except Exception as exc:
                account["status"] = "reauth" if isinstance(exc, AntigravityAuthError) else "error"
                account["error"] = _safe_error(exc)
            result.append(_public_account(account))
        return result

    def models(self) -> dict[str, Any]:
        accounts = self.accounts()
        by_id: dict[str, dict[str, Any]] = {}
        for account in accounts:
            for model in account.get("models", []):
                by_id.setdefault(model["id"], model)
        ids = sorted(by_id, key=lambda x: ("preview" in x.lower(), x))
        return {"models": [by_id[x] for x in ids], "default_model": ids[0] if ids else ""}

    def generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = str(payload.get("prompt") or "").strip()
        model = str(payload.get("model") or "").strip()
        aspect = str(payload.get("aspect") or "16:9")
        resolution = str(payload.get("resolution") or "1K").upper()
        n = int(payload.get("n") or 1)
        refs = payload.get("ref_images") or []
        if not prompt:
            raise ValueError('missing "prompt" string')
        if not model:
            model = self.models()["default_model"]
        if not model:
            raise RuntimeError("no Antigravity image model is available")
        if resolution not in ("1K", "2K"):
            raise ValueError("resolution must be 1K or 2K")
        if not 1 <= n <= 4:
            raise ValueError("n must be between 1 and 4")
        if not isinstance(refs, list) or len(refs) > 4:
            raise ValueError("ref_images must contain at most 4 images")

        accounts = store.load()
        preferred = self._active_by_model.get(model)
        ordered = sorted(accounts, key=lambda a: 0 if preferred and a.get("email") == preferred else 1)
        last_error: Exception | None = None
        unsupported_2k = False
        quota_seen = False
        for account in ordered:
            try:
                self._hydrate(account)
                model_info = next((m for m in account.get("models", []) if m.get("id") == model), None)
                if model_info is None:
                    continue
                if resolution == "2K" and model_info.get("supports_2k") is False:
                    unsupported_2k = True
                    continue
                cooldown = float((account.get("cooldowns") or {}).get(model) or 0)
                if cooldown > time.time():
                    quota_seen = True
                    continue
                token = self._refresh_if_needed(account)
                images = self.client.generate(token, account["project_id"], model, prompt, aspect, resolution, n, refs)
                self._active_by_model[model] = str(account.get("email") or "")
                account["status"] = "available"
                store.upsert(account)
                return {"images": images, "account_email": account.get("email", ""),
                        "model": model, "resolution": resolution}
            except AntigravityAuthError as exc:
                last_error = exc
                account["status"] = "reauth"
                store.upsert(account)
                continue
            except AntigravityHTTPError as exc:
                last_error = exc
                if exc.quota:
                    quota_seen = True
                    account.setdefault("cooldowns", {})[model] = time.time() + (exc.retry_after or 300)
                    account["status"] = "exhausted"
                    store.upsert(account)
                    continue
                raise
            except Exception as exc:
                # A timeout, validation error or 5xx is not evidence that a
                # second account should generate the same image. Do not
                # create duplicates by failing over on ambiguous failures.
                last_error = exc
                raise
        if quota_seen or (isinstance(last_error, AntigravityHTTPError) and last_error.quota):
            raise AntigravityHTTPError(429, "All Antigravity accounts are out of quota")
        if unsupported_2k:
            raise ValueError(f"model {model} does not support 2K; choose 1K or another model")
        raise last_error or RuntimeError("no Antigravity account supports the selected model")


def _safe_error(exc: Exception) -> str:
    text = str(exc)
    if isinstance(exc, AntigravityHTTPError) and isinstance(exc.body, dict):
        body_error = exc.body.get("error")
        if isinstance(body_error, dict):
            detail = body_error.get("message") or body_error.get("status")
            if detail:
                text += ": " + str(detail)
    return text[:300] if text else type(exc).__name__


def _public_account(account: dict[str, Any]) -> dict[str, Any]:
    credits = account.get("credits") or {}
    return {"email": account.get("email", ""), "status": account.get("status", "unknown"),
            "credits": credits, "models": account.get("models", []), "error": account.get("error", ""),
            "cooldowns": account.get("cooldowns", {})}


def get_service() -> AntigravityService:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = AntigravityService()
        return _SERVICE
