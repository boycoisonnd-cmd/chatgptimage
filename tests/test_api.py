"""Unit tests for the REST API (aigpt.api) - pure functions + handler mapping.

The generation itself is monkeypatched (never touches the engine/network).
"""
from __future__ import annotations

import json

import pytest

from aigpt import api
from aigpt.auth.pool import NoQuotaError

# ---------------------------------------------------------------- exceptions

def test_exception_mapping_value_error():
    exc = ValueError("n must be between 1 and 4, got 9")
    assert api.build_response_from_exception(exc) == (400, {"error": str(exc)})


def test_exception_mapping_noquota_with_restore():
    exc = NoQuotaError("all accounts exhausted", 1_735_689_600.0)
    status, body = api.build_response_from_exception(exc)
    assert status == 429
    assert body["restore_at_epoch"] == 1_735_689_600.0
    assert body["error"]


def test_exception_mapping_noquota_without_restore_is_401():
    exc = NoQuotaError("No accounts logged in - run `aigpt login`.")
    assert api.build_response_from_exception(exc) == (401, {"error": str(exc)})


def test_exception_mapping_upstream_names():
    for name in api._UPSTREAM_ERROR_NAMES:
        # Create a dynamic subclass of RuntimeError with that __name__
        cls = type(name, (RuntimeError,), {})
        exc = cls(f"{name}: boom")
        status, body = api.build_response_from_exception(exc)
        assert status == 502
        assert "boom" in body["error"]


def test_exception_mapping_generic_runtime_error_is_502():
    exc = RuntimeError("image generation produced no images")
    assert api.build_response_from_exception(exc) == (502, {"error": str(exc)})


def test_exception_mapping_unexpected_is_500():
    exc = KeyError("nope")
    assert api.build_response_from_exception(exc) == (500, {"error": "internal error"})


# ------------------------------------------------------------ request parsing

def _req(**kw) -> dict:
    return {"prompt": "cat", **kw}


def test_parse_generate_request_valid_defaults():
    got = api.parse_generate_request(json.dumps(_req()).encode())
    assert got["prompt"] == "cat"
    assert "n" not in got  # absent -> engine default; coercion happens in _gen


def test_parse_generate_request_coerces_types():
    body = json.dumps(_req(n="2", enhance="true", brand_colors=["#10B981"])).encode()
    got = api.parse_generate_request(body)
    assert got["n"] == 2
    assert got["enhance"] is True
    assert got["brand_colors"] == ["#10B981"]


def test_parse_generate_request_rejects_bad_json():
    with pytest.raises(ValueError):
        api.parse_generate_request(b"{not json")


def test_parse_generate_request_rejects_non_dict():
    with pytest.raises(TypeError):
        api.parse_generate_request(b"[1, 2]")


def test_parse_generate_request_rejects_unknown_key():
    with pytest.raises(ValueError, match="unknown parameter"):
        api.parse_generate_request(json.dumps(_req(evil="x")).encode())


def test_parse_generate_request_rejects_missing_prompt():
    with pytest.raises(ValueError, match="prompt"):
        api.parse_generate_request(json.dumps({"n": 1}).encode())


def test_parse_generate_request_rejects_oversize():
    big = json.dumps(_req(prompt="x" * (api._MAX_BODY + 10))).encode()
    with pytest.raises(ValueError, match="too large"):
        api.parse_generate_request(big)


def test_parse_generate_request_rejects_bad_type_hint():
    with pytest.raises(ValueError, match="must be an integer"):
        api.parse_generate_request(json.dumps(_req(n="lots")).encode())


# ---------------------------------------------------------------- _gen plumbing

def test_gen_validates_n_and_raises():
    """_gen calls _validate_n before the engine; out-of-range n raises ValueError."""
    with pytest.raises(ValueError, match="n must be between 1 and 4"):
        api._gen({"prompt": "cat", "n": 9})


def test_gen_validates_brand_colors_and_raises():
    with pytest.raises(ValueError, match="must be #RRGGBB hex"):
        api._gen({"prompt": "cat", "brand_colors": ["bad"]})


def test_gen_validates_n_and_passes_to_engine(monkeypatch):
    from aigpt.engine import generate as engine

    captured = {}
    monkeypatch.setattr(engine, "generate_image",
                        lambda **kw: captured.update(kw) or ["C:/out/img.png"])
    got = api._gen({"prompt": "cat", "n": 2, "brand_colors": ["#10B981"]})
    assert got["paths"] == ["C:\\out\\img.png"]  # abspath is OS-specific
    assert captured["n"] == 2
    assert captured["brand_colors"] == ["#10B981"]


def test_gen_engine_error_propagates(monkeypatch):
    """_gen does not catch engine errors; the handler maps them via build_response."""
    from aigpt.engine import generate as engine

    monkeypatch.setattr(engine, "generate_image",
                        lambda **kw: (_ for _ in ()).throw(ValueError("bad n")))
    with pytest.raises(ValueError, match="bad n"):
        api._gen({"prompt": "cat"})


def test_file_registry_serves_only_registered_images(tmp_path):
    """Registry-gated file serving: an id only resolves to a file this server
    generated; arbitrary paths (traversal) are not servable."""
    img = tmp_path / "img.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfakepixels")
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"top secret")
    file_id = api._register_file(str(img))
    # registered image is servable
    data, ctype = api._file_by_id(file_id)
    assert data == b"\x89PNG\r\n\x1a\nfakepixels"
    assert ctype == "image/png"
    # unregistered path -> None (no traversal)
    assert api._file_by_id("../secret.png") is None
    assert api._file_by_id(str(secret)) is None
    assert api._file_by_id("nope") is None


def test_file_registry_evicts_oldest_when_serving():
    """With serving on, the registry stays bounded: oldest entries evicted."""
    api._file_registry.clear()
    try:
        api._serving_files = True
        old_registry_max = api._MAX_REGISTRY
        api._MAX_REGISTRY = 3
        try:
            first = api._register_file("C:/out/a.png")
            api._register_file("C:/out/b.png")
            api._register_file("C:/out/c.png")
            api._register_file("C:/out/d.png")
            assert first not in api._file_registry  # oldest evicted
            assert len(api._file_registry) == 3  # stays capped
        finally:
            api._MAX_REGISTRY = old_registry_max
    finally:
        api._file_registry.clear()
        api._serving_files = False


def test_decode_data_url_accepts_https():
    assert api._decode_data_url("https://example.com/img.png") == "https://example.com/img.png"


def test_decode_data_url_accepts_valid_base64():
    import base64

    b64 = base64.b64encode(b"\x89PNG fake").decode()
    url = f"data:image/png;base64,{b64}"
    assert api._decode_data_url(url) == url


def test_decode_data_url_rejects_non_image_and_bad_b64():
    with pytest.raises(ValueError, match="data:image URL"):
        api._decode_data_url("data:text/html;base64,PGI+")
    with pytest.raises(ValueError, match="invalid base64"):
        api._decode_data_url("data:image/png;base64,not!!base64")
    with pytest.raises(TypeError, match="data: URL string"):
        api._decode_data_url(42)
# ------------------------------------------------------------ login session (panel-driven)

def _reset_login_state():
    api._login_session = None


def test_login_start_starts_session(monkeypatch, tmp_path):
    import aigpt.login_wait as login_wait_mod
    _reset_login_state()
    monkeypatch.setattr(api.oauth_login, "build_and_stash",
                        lambda email, open_browser=True: "https://auth.example/auth")
    monkeypatch.setattr(api.oauth_login, "discard_pending", lambda: None)
    # Real start_login_session on port 0 would bind 8788; monkeypatch it.
    fake_session = object()
    monkeypatch.setattr(login_wait_mod, "start_login_session",
                        lambda url, port=8788: fake_session)
    status, body = api._login_start("a@b")
    assert status == 200
    assert body["authorize_url"] == "https://auth.example/auth"
    api._login_session = None


def test_login_start_idempotent_while_waiting(monkeypatch):
    import time
    import aigpt.login_wait as login_wait_mod
    _reset_login_state()
    class _FakeSess:
        stopped = False
        started_at = time.time()  # fresh, so the TTL branch does not fire
        authorize_url = "https://auth.example/auth"
    monkeypatch.setattr(login_wait_mod, "login_session_status",
                        lambda s: {"state": "waiting"})
    api._login_session = _FakeSess()
    status, body = api._login_start("")
    assert status == 200
    assert body["authorize_url"] == "https://auth.example/auth"
    api._login_session = None


def test_login_start_409_when_port_busy(monkeypatch):
    import aigpt.login_wait as login_wait_mod
    _reset_login_state()
    monkeypatch.setattr(api.oauth_login, "build_and_stash",
                        lambda email, open_browser=True: "https://auth.example/auth")
    discarded = []
    monkeypatch.setattr(api.oauth_login, "discard_pending",
                        lambda: discarded.append(1))
    def boom(url, port=8788):
        raise OSError("port busy")
    monkeypatch.setattr(login_wait_mod, "start_login_session", boom)
    status, body = api._login_start("")
    assert status == 409
    assert "busy" in body["error"]
    assert discarded  # pending file cleaned up


def test_login_poll_idle_without_session():
    _reset_login_state()
    assert api._login_poll() == {"state": "idle"}


def test_login_poll_done_reloads_and_clears(monkeypatch):
    import time
    import aigpt.login_wait as login_wait_mod
    from aigpt.engine import account_wiring as pool_mod
    _reset_login_state()
    class _FakeSess:
        stopped = False
        started_at = time.time()  # fresh, so the TTL branch does not fire
    reloaded = []
    class _FakePool:
        def reload_accounts(self):
            reloaded.append(1)
    monkeypatch.setattr(pool_mod, "get_pool", lambda: _FakePool())
    monkeypatch.setattr(login_wait_mod, "login_session_status",
                        lambda s: {"state": "done", "email": "a@b"})
    stopped = []
    monkeypatch.setattr(login_wait_mod, "stop_login_session",
                        lambda s: stopped.append(1))
    api._login_session = _FakeSess()
    st = api._login_poll()
    assert st["state"] == "done"
    assert reloaded
    assert stopped
    assert api._login_session is None


def test_login_poll_error_clears(monkeypatch):
    import time
    import aigpt.login_wait as login_wait_mod
    _reset_login_state()
    class _FakeSess:
        stopped = False
        started_at = time.time()  # fresh, so the TTL branch does not fire
    monkeypatch.setattr(login_wait_mod, "login_session_status",
                        lambda s: {"state": "error", "error": "boom"})
    stopped = []
    monkeypatch.setattr(login_wait_mod, "stop_login_session",
                        lambda s: stopped.append(1))
    api._login_session = _FakeSess()
    st = api._login_poll()
    assert st["state"] == "error"
    assert stopped
    assert api._login_session is None


# ------------------------------------------------------------ DELETE /account

class _FakeApiServer:
    semaphore = type("Sem", (), {"acquire": lambda self, b=False: True,
                                 "release": lambda self: None})()


def _del_req(path, headers=None):
    handler = api.ApiServer.__new__(api.ApiServer)
    handler.path = path
    handler.headers = headers or {}
    handler.server = _FakeApiServer()
    handler.sent = None
    def _json(status, payload):
        handler.sent = (status, payload)
    handler._json = _json
    handler.do_DELETE()
    return handler.sent


def test_delete_account_requires_origin(monkeypatch, tmp_path):
    sent = _del_req("/account?email=a@b", headers={"Origin": "https://evil.example"})
    assert sent[0] == 403


def test_delete_account_missing_selector(monkeypatch, tmp_path):
    sent = _del_req("/account", headers={})
    assert sent[0] == 400


def test_delete_account_not_found(monkeypatch, tmp_path):
    from aigpt.auth import tokens
    monkeypatch.setattr(tokens, "_config_dir", lambda: tmp_path)
    sent = _del_req("/account?email=nobody@example.com", headers={})
    assert sent[0] == 404


def test_delete_account_removes_and_reloads(monkeypatch, tmp_path):
    from aigpt.engine import account_wiring as pool_mod
    from aigpt.auth import tokens, store
    monkeypatch.setattr(tokens, "_config_dir", lambda: tmp_path)
    store.upsert_account({"user_id": "u1", "email": "a@b", "access_token": "t1"})
    reloaded = []
    # Replace get_pool at its DEFINITION site so api.do_DELETE sees the fake.
    class _FakePool:
        def reload_accounts(self):
            reloaded.append(1)
    monkeypatch.setattr(pool_mod, "get_pool", lambda: _FakePool())
    sent = _del_req("/account?email=a%40b", headers={})
    assert sent[0] == 200
    assert sent[1]["removed"] == 1
    assert reloaded
    assert store.load_accounts() == []
