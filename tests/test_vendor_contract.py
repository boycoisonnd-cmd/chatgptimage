"""Verify the vendored engine's contract with our local shims.

Fails loudly if a vendored call would silently fall through the shim's
``__getattr__`` no-op - i.e. if upstream changed an interface the engine
depends on (see NOTICE: account_service.py is a LOCAL file, never re-vendored).
"""
from __future__ import annotations

import pytest

# Importing _vendor_path puts the vendored services.utils packages on sys.path;
# account_service lives under _vendor/services and is imported as `services.account_service`.
import aigpt._vendor_path  # noqa: F401
from services.account_service import account_service, set_token_provider
from services.openai_backend_api import OpenAIBackendAPI


@pytest.fixture(autouse=True)
def reset_provider():
    """Test isolation: other test files wire the pool provider globally
    (engine import side effect). Reset between tests so the unwired-shim
    expectation stays valid regardless of collection order."""
    from services import account_service as mod

    mod._select = None
    mod._on_result = None
    mod._account_lookup = None
    mod._text_token = None
    mod._refresh = None
    mod._remove = None
    yield


IMAGE_SERVICE_CALLS = (
    "get_available_access_token",  # pool token selection per image
    "mark_image_result",           # per-image success -> pool decrement
    "get_account",                 # token -> email for logging
    "refresh_access_token",        # proactive token refresh
    "mark_text_used",              # text-path quota accounting
    "remove_invalid_token",        # bench a revoked token
    "get_text_access_token",       # enhance path shares the active account
)


def test_account_service_defines_every_engine_call():
    """Every interface the vendored engine calls must exist EXPLICITLY on the
    shim - a missing one would hit __getattr__ and silently return None."""
    for name in IMAGE_SERVICE_CALLS:
        assert callable(getattr(account_service, name, None)), \
            f"account_service.{name} missing - vendored engine will break silently"


def test_unwired_shim_raises_on_token_selection():
    """Before any provider is wired, token selection must fail loudly."""
    with pytest.raises(RuntimeError, match="not logged in"):
        account_service.get_available_access_token()


def test_set_token_provider_wires_single_account():
    calls = {"got": []}

    def get_token():
        calls["got"].append("x")
        return "tok-1"

    set_token_provider(get_token=get_token, refresh=lambda force: "tok-1")
    assert account_service.get_available_access_token() == "tok-1"
    assert account_service.get_text_access_token() == "tok-1"
    assert account_service.mark_image_result("tok-1", True) is None


def test_image_pipeline_contract_symbols_present():
    """Symbols the local engine layer imports from the vendored protocol."""
    import services.protocol.conversation as conv

    for name in (
        "ConversationRequest",
        "ImageOutput",
        "stream_image_outputs_with_pool",
        "encode_images",
        "stream_text_deltas",  # enhance path
    ):
        assert hasattr(conv, name), f"conversation.{name} missing after vendor sync"

    assert hasattr(OpenAIBackendAPI, "get_user_info")
    assert hasattr(OpenAIBackendAPI, "download_image_bytes")
    assert hasattr(OpenAIBackendAPI, "_prepare_image_conversation")


def test_vendor_imports_do_not_pull_dead_modules():
    """The trimmed vendor tree must not import modules we removed (sentinel)."""
    import sys

    assert "utils.sentinel" not in sys.modules
