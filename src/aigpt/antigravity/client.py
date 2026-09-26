"""Standalone Antigravity OAuth and Cloud Code client for aigpt."""
from __future__ import annotations

import base64
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CLIENT_SECRET = "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"
CALLBACK_PORT = 51121
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo?alt=json"
API_ENDPOINT = "https://cloudcode-pa.googleapis.com"
DAILY_ENDPOINT = "https://daily-cloudcode-pa.googleapis.com"
API_VERSION = "v1internal"
ANTIGRAVITY_VERSION = "2.9.1"
GOOG_API_CLIENT = "gl-node/22.21.1"
NODE_API_CLIENT = "google-api-nodejs-client/10.3.0"
SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
)
_IMAGE_RE = re.compile(r"image", re.I)
_MIME_TO_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
_MAX_INLINE_IMAGE_BYTES = 8 * 1024 * 1024


class AntigravityHTTPError(RuntimeError):
    def __init__(self, status: int, message: str, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body

    @property
    def quota(self) -> bool:
        text = str(self).lower() + " " + json.dumps(self.body, ensure_ascii=False).lower()
        return self.status == 429 or "resource_exhausted" in text or "quota_exhausted" in text or "quota exhausted" in text

    @property
    def retry_after(self) -> float | None:
        """Best-effort retry hint from Cloud Code error metadata."""
        candidates: list[Any] = []
        if isinstance(self.body, dict):
            for key in ("retryAfter", "retry_after", "retryDelay", "retry_delay"):
                if key in self.body:
                    candidates.append(self.body[key])
            error = self.body.get("error")
            if isinstance(error, dict):
                for key in ("retryAfter", "retry_after", "retryDelay", "retry_delay"):
                    if key in error:
                        candidates.append(error[key])
        for value in candidates:
            try:
                if isinstance(value, str) and value.endswith("s"):
                    value = value[:-1]
                seconds = float(value)
                if seconds >= 0:
                    return min(seconds, 24 * 3600)
            except (TypeError, ValueError):
                continue
        return None


class AntigravityAuthError(AntigravityHTTPError):
    pass


def _json_body(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, ValueError):
        return raw[:2000].decode("utf-8", "replace")


def http_request(method: str, url: str, *, headers: dict[str, str] | None = None,
                 body: bytes | None = None, timeout: float = 60.0) -> tuple[int, bytes]:
    req = Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except HTTPError as exc:
        return int(exc.code), exc.read()
    except URLError as exc:
        raise RuntimeError(f"Antigravity network error: {exc.reason}") from exc


def _ua() -> str:
    # Cloud Code validates the Antigravity Hub client family/version during setup.
    return f"antigravity/hub/{ANTIGRAVITY_VERSION} darwin/arm64"


def _node_ua() -> str:
    return _ua() + " " + NODE_API_CLIENT


def _inline_data(value: str) -> dict[str, Any]:
    if not isinstance(value, str) or not value.startswith("data:image/"):
        raise ValueError("ref_images must contain data:image PNG/JPEG/WebP URLs")
    head, sep, encoded = value.partition(",")
    if not sep or ";base64" not in head:
        raise ValueError("ref_images must contain base64 data URLs")
    mime = head[5:].split(";", 1)[0].lower()
    if mime not in _MIME_TO_EXT:
        raise ValueError("ref_images must be PNG, JPEG or WebP")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise ValueError("ref_images contains invalid base64") from None
    if len(decoded) > _MAX_INLINE_IMAGE_BYTES:
        raise ValueError("ref_images image is too large")
    return {"inlineData": {"mimeType": mime, "data": encoded}}


def _walk_images(value: Any) -> list[tuple[str, bytes]]:
    found: list[tuple[str, bytes]] = []
    if isinstance(value, dict):
        inline = value.get("inlineData") or value.get("inline_data")
        if isinstance(inline, dict) and inline.get("data"):
            mime = str(inline.get("mimeType") or inline.get("mime_type") or "image/png").lower()
            if mime in _MIME_TO_EXT:
                try:
                    decoded = base64.b64decode(str(inline["data"]), validate=True)
                    if len(decoded) <= _MAX_INLINE_IMAGE_BYTES:
                        found.append((mime, decoded))
                except (ValueError, TypeError):
                    pass
        # Do not descend into the inlineData object again: otherwise the
        # same image is returned twice (once by the fast path and once by the
        # recursive walk).
        for key, child in value.items():
            if key in ("inlineData", "inline_data"):
                continue
            found.extend(_walk_images(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_images(child))
    return found


@dataclass
class AntigravityClient:
    request_fn: Callable[..., tuple[int, bytes]] = http_request
    sleep_fn: Callable[[float], None] = time.sleep

    def auth_url(self, state: str, redirect_uri: str | None = None) -> str:
        redirect_uri = redirect_uri or f"http://localhost:{CALLBACK_PORT}/oauth-callback"
        return AUTH_ENDPOINT + "?" + urlencode({
            "access_type": "offline", "client_id": CLIENT_ID,
            "prompt": "select_account consent", "redirect_uri": redirect_uri,
            "response_type": "code", "scope": " ".join(SCOPES), "state": state,
        })

    def _call_json(self, method: str, url: str, payload: dict[str, Any] | None = None,
                   token: str | None = None, timeout: float = 60.0,
                   operation: str = "", user_agent: str | None = None,
                   extra_headers: dict[str, str] | None = None) -> Any:
        headers = {"Accept": "*/*", "User-Agent": user_agent or _ua()}
        if extra_headers:
            headers.update(extra_headers)
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        status, raw = self.request_fn(method, url, headers=headers, body=body, timeout=timeout)
        data = _json_body(raw)
        if status < 200 or status >= 300:
            # A Cloud Code 403 often means project/setup permission rather
            # than an invalid Google token. Only 401 is a definite re-login
            # signal; preserve 403 as an upstream/setup error.
            exc_cls = AntigravityAuthError if status == 401 else AntigravityHTTPError
            label = operation or url.rsplit("/", 1)[-1]
            raise exc_cls(status, f"Antigravity {label} failed (HTTP {status})", data)
        return data

    def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        form = urlencode({"code": code, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                          "redirect_uri": redirect_uri, "grant_type": "authorization_code"}).encode()
        status, raw = self.request_fn("POST", TOKEN_ENDPOINT,
                                      headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": _ua()},
                                      body=form, timeout=60)
        data = _json_body(raw)
        if status < 200 or status >= 300 or not data.get("access_token"):
            raise AntigravityAuthError(status, "Antigravity token exchange failed", data)
        return data

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        form = urlencode({"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                          "grant_type": "refresh_token", "refresh_token": refresh_token}).encode()
        status, raw = self.request_fn("POST", TOKEN_ENDPOINT,
                                      headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": _ua()},
                                      body=form, timeout=60)
        data = _json_body(raw)
        if status < 200 or status >= 300 or not data.get("access_token"):
            raise AntigravityAuthError(status, "Antigravity token refresh failed", data)
        return data

    def user_email(self, token: str) -> str:
        data = self._call_json("GET", USERINFO_ENDPOINT, token=token, operation="userinfo")
        email = str(data.get("email") or "").strip()
        if not email:
            raise AntigravityAuthError(502, "Antigravity userinfo did not return an email", data)
        return email

    def project_id(self, token: str) -> str:
        data = self._call_json("POST", f"{API_ENDPOINT}/{API_VERSION}:loadCodeAssist",
                               {"metadata": {"ideType": "ANTIGRAVITY"}}, token=token,
                               operation="loadCodeAssist")
        for key in ("cloudaicompanionProject", "projectId", "project"):
            value = data.get(key)
            if isinstance(value, dict):
                value = value.get("id")
            if isinstance(value, str) and value.strip():
                return value.strip()
        tier = "free-tier"
        for item in data.get("allowedTiers", []) if isinstance(data.get("allowedTiers"), list) else []:
            if isinstance(item, dict) and item.get("isDefault") and item.get("id"):
                tier = str(item["id"])
                break
        for _ in range(5):
            onboard = self._call_json("POST", f"{DAILY_ENDPOINT}/{API_VERSION}:onboardUser", {
                "tier_id": tier,
                "metadata": {"ide_type": "ANTIGRAVITY", "ide_version": ANTIGRAVITY_VERSION, "ide_name": "antigravity"},
            }, token=token, timeout=30, operation="onboardUser", user_agent=_node_ua(),
                extra_headers={"X-Goog-Api-Client": GOOG_API_CLIENT})
            if onboard.get("done"):
                response = onboard.get("response") or {}
                project = response.get("cloudaicompanionProject") or response.get("projectId")
                if not project:
                    project = onboard.get("cloudaicompanionProject") or onboard.get("projectId")
                if isinstance(project, dict):
                    project = project.get("id")
                if project:
                    return str(project).strip()
            self.sleep_fn(2)
        raise RuntimeError("Antigravity onboarding did not return a project ID")

    def credits(self, token: str) -> dict[str, Any]:
        data = self._call_json("POST", f"{API_ENDPOINT}/{API_VERSION}:loadCodeAssist",
                               {"metadata": {"ideType": "ANTIGRAVITY"}}, token=token,
                               operation="loadCodeAssist")
        paid = data.get("paidTier") if isinstance(data, dict) else None
        result: dict[str, Any] = {"status": "unknown", "paid_tier": (paid or {}).get("id") if isinstance(paid, dict) else None}
        rows = paid.get("availableCredits") if isinstance(paid, dict) else None
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict) or str(row.get("creditType", "")).upper() != "GOOGLE_ONE_AI":
                    continue
                try:
                    amount = float(row.get("creditAmount"))
                    minimum = float(row.get("minimumCreditAmountForUsage"))
                except (TypeError, ValueError):
                    continue
                result.update({"status": "available" if amount >= minimum else "exhausted",
                               "credit_amount": amount, "minimum_credit_amount": minimum})
                break
        return result

    def models(self, token: str, project_id: str) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        data: Any = None
        for endpoint in (DAILY_ENDPOINT, API_ENDPOINT):
            try:
                data = self._call_json("POST", f"{endpoint}/{API_VERSION}:fetchAvailableModels",
                                       {"project": project_id}, token=token,
                                       operation="fetchAvailableModels")
                break
            except Exception as exc:
                last_error = exc
        if data is None:
            raise last_error or RuntimeError("Antigravity model discovery failed")
        out = []
        raw_models = data.get("models") if isinstance(data, dict) else {}
        entries = raw_models.items() if isinstance(raw_models, dict) else []
        if isinstance(raw_models, list):
            entries = ((item.get("id") or item.get("name"), item) for item in raw_models if isinstance(item, dict))
        for model_id, info in entries:
            if not _IMAGE_RE.search(str(model_id)):
                continue
            info = info if isinstance(info, dict) else {}
            sizes = (info.get("supportedImageSizes") or info.get("supported_image_sizes")
                     or info.get("imageSizes") or info.get("image_sizes"))
            if not isinstance(sizes, list):
                sizes = None
            out.append({"id": str(model_id), "display_name": info.get("displayName") or str(model_id),
                        "max_tokens": info.get("maxTokens"), "max_output_tokens": info.get("maxOutputTokens"),
                        "supported_sizes": sizes,
                        "supports_2k": ("2K" in {str(s).upper() for s in sizes}) if sizes is not None else None})
        return sorted(out, key=lambda x: ("preview" in x["id"].lower(), x["id"]))

    def generate(self, token: str, project_id: str, model: str, prompt: str,
                 aspect: str, resolution: str, n: int, ref_images: list[str]) -> list[tuple[str, bytes]]:
        parts: list[dict[str, Any]] = [{"text": prompt}]
        parts.extend(_inline_data(value) for value in ref_images)
        payload = {
            "project": project_id,
            "request": {"contents": [{"role": "user", "parts": parts}],
                         "generationConfig": {"responseModalities": ["IMAGE"],
                                               "candidateCount": n,
                                               "imageConfig": {"aspectRatio": aspect, "imageSize": resolution}}},
            "model": model,
            "userAgent": "antigravity",
            "requestType": "image_gen",
            "requestId": f"image_gen/{int(time.time() * 1000)}/{uuid.uuid4()}/12",
        }
        data = self._call_json("POST", f"{DAILY_ENDPOINT}/{API_VERSION}:generateContent", payload,
                               token=token, timeout=300, operation="generateContent")
        images = _walk_images(data)
        if not images:
            raise RuntimeError("Antigravity response did not contain an image")
        return images
