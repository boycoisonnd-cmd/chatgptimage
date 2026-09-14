"""Unit tests for the REST API (aigpt.api) - pure functions + handler mapping.

The generation itself is monkeypatched (never touches the engine/network).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aigpt import api, storage
from aigpt.auth.pool import NoQuotaError


@pytest.fixture(autouse=True)
def _isolate_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_IMAGES_ROOT", tmp_path / "storage_images")
    monkeypatch.setattr(storage, "_INDEX_FILE", tmp_path / "storage_images_index.json")


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
        if name == "ImagePollTimeoutError":
            continue  # mapped to 504, not 502
        # Create a dynamic subclass of RuntimeError with that __name__
        cls = type(name, (RuntimeError,), {})
        exc = cls(f"{name}: boom")
        status, body = api.build_response_from_exception(exc)
        assert status == 502
        assert "boom" in body["error"]


def test_exception_mapping_poll_timeout_is_504():
    cls = type("ImagePollTimeoutError", (RuntimeError,), {})
    status, body = api.build_response_from_exception(cls("ChatGPT 生图超时"))
    assert status == 504
    assert "timed out" in body["error"]
    assert "生图超时" not in body["error"]


def test_exception_mapping_strips_chinese_from_upstream():
    """Vendor/upstream errors with Chinese text are sanitized to English at the API edge."""
    cls = type("ImageGenerationError", (RuntimeError,), {})
    status, body = api.build_response_from_exception(cls("上游生成失败 upstream failure"))
    assert status == 502
    assert "upstream failure" in body["error"]
    assert "上游" not in body["error"]


def test_exception_mapping_chinese_only_returns_fallback():
    cls = type("ImageGenerationError", (RuntimeError,), {})
    status, body = api.build_response_from_exception(cls("ChatGPT 生图超时（已等待 120 秒）"))
    assert status == 502
    assert "image generation failed" in body["error"]
    assert "生图超时" not in body["error"]


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


def test_parse_generate_request_accepts_conversation_id():
    got = api.parse_generate_request(
        json.dumps(_req(conversation_id=" conv-9 ")).encode())
    assert got["conversation_id"] == "conv-9"  # coerced + stripped


def test_parse_generate_request_empty_conversation_id_omitted():
    got = api.parse_generate_request(
        json.dumps(_req(conversation_id="   ")).encode())
    assert "conversation_id" not in got  # empty -> fresh generation


def test_parse_generate_request_rejects_parent_message_id():
    with pytest.raises(ValueError, match="unknown parameter"):
        api.parse_generate_request(
            json.dumps(_req(parent_message_id="msg-1")).encode())


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
    from aigpt.engine.result import GenerateResult

    captured = {}
    monkeypatch.setattr(engine, "generate_image",
                        lambda **kw: captured.update(kw)
                        or GenerateResult.from_list(["C:/out/img.png"], "conv-9"))
    got = api._gen({"prompt": "cat", "n": 2, "brand_colors": ["#10B981"]})
    assert got["paths"] == ["C:\\out\\img.png"]  # abspath is OS-specific
    assert got["conversation_id"] == "conv-9"
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
    from aigpt.auth import store, tokens
    from aigpt.engine import account_wiring as pool_mod
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


# ------------------------------------------------------------ storage & fallback

def test_storage_save_lookup_roundtrip(tmp_path):
    img = tmp_path / "sample.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nsample-image-bytes")
    fid = storage.save(img, conversation_id="conv-42")
    assert fid

    # Lookup returns real guarded path and correct data
    path = storage.lookup(fid)
    assert path is not None
    assert path.exists()
    assert path.read_bytes() == b"\x89PNG\r\n\x1a\nsample-image-bytes"

    # Index details
    items = storage.list_items()
    assert len(items) == 1
    assert items[0]["id"] == fid
    assert items[0]["conversation_id"] == "conv-42"
    assert items[0]["name"].endswith(".png")


def test_storage_lookup_rejects_traversal(tmp_path):
    # Inject a traversal path directly into the index
    idx = {
        "items": {
            "evil": {
                "rel": "../secret.png",
                "path": str(tmp_path / "secret.png"),
                "name": "secret.png",
                "created_at": "2026-08-27 12:00:00",
            }
        }
    }
    storage._save_index(idx)
    assert storage.lookup("evil") is None


def test_storage_lookup_cleans_drift(tmp_path):
    img = tmp_path / "drift.png"
    img.write_bytes(b"\x89PNG fake")
    fid = storage.save(img)

    # Delete the stored file on disk
    path = storage.lookup(fid)
    assert path is not None
    path.unlink()

    # Next lookup detects drift, self-heals by removing index row, and returns None
    assert storage.lookup(fid) is None
    assert storage.list_items() == []


def test_storage_evicts_oldest_over_max(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_INDEX_MAX", 2)

    img1 = tmp_path / "img1.png"
    img1.write_bytes(b"img1")
    img2 = tmp_path / "img2.png"
    img2.write_bytes(b"img2")
    img3 = tmp_path / "img3.png"
    img3.write_bytes(b"img3")

    id1 = storage.save(img1)
    id2 = storage.save(img2)
    id3 = storage.save(img3)

    # id1 should be evicted (both index and disk file)
    assert storage.lookup(id1) is None
    assert storage.lookup(id2) is not None
    assert storage.lookup(id3) is not None
    assert len(storage.list_items()) == 2


def test_file_by_id_falls_back_to_index(tmp_path):
    img = tmp_path / "fallback.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfallback-bytes")

    file_id = api._register_file(str(img), conversation_id="conv-fallback")
    # Verify it was registered in memory
    assert file_id in api._file_registry

    # Clear in-memory registry (simulate server restart)
    api._file_registry.clear()
    assert file_id not in api._file_registry

    # _file_by_id should fall back to disk index
    data, ctype = api._file_by_id(file_id)
    assert data == b"\x89PNG\r\n\x1a\nfallback-bytes"
    assert ctype == "image/png"


def test_images_endpoint_returns_list(tmp_path):
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\ntest")
    fid = storage.save(img, conversation_id="conv-1")

    handler = api.ApiServer.__new__(api.ApiServer)
    handler.path = "/images"
    handler.headers = {}
    handler.server = _FakeApiServer()
    handler.sent = None

    def _json(status, payload):
        handler.sent = (status, payload)

    handler._json = _json
    handler.do_GET()

    assert handler.sent[0] == 200
    images = handler.sent[1].get("images")
    assert len(images) == 1
    assert images[0]["id"] == fid
    assert images[0]["conversation_id"] == "conv-1"
