"""Spike B0 (throwaway): pin the conversation_id follow-up wire BEFORE PR B.

Answers the open questions from the plan (printed as ANSWER lines):
  Q1  Is a follow-up gen POST with conversation_id + parent_message_id=<leaf>
      accepted and does it produce an image?
  Q2  Does prepare WITHOUT conversation_id still work (current vendored
      behavior) for a follow-up generation?
  Q3  Is prepare WITH conversation_id + parent_message_id accepted?
  Q4  Which status/body does a BOGUS conversation id get on the gen POST?
      (confirms the rejection set 400/404/422 used by image_wire)
  Q5  Follow-up with ZERO new images attached - accepted?
  Q6  GET conversation-detail on a bogus id - does it surface as
      UpstreamHTTPError(404)? (the early-rejection route the prepare wrapper
      will rely on)

Quota budget: up to 3 image slots (base gen, accepted follow-up, no-image
follow-up). Rejection probes fail before any image is generated (free).

Run:  uv run python scripts/spike_followup.py
Reuse an existing conversation (saves 1 image slot) via env:
      SPIKE_BASE_CONV=<conversation_id> uv run python scripts/spike_followup.py
"""
from __future__ import annotations

import json
import os
import sys

import aigpt._vendor_path  # noqa: F401
from aigpt.engine import image_wire
from aigpt.engine.account_wiring import get_pool
from services.openai_backend_api import OpenAIBackendAPI
from services.protocol.conversation import extract_conversation_ids
from utils.helper import UpstreamHTTPError, iter_sse_payloads

MODEL = "gpt-image-2"
BOGUS_CONV = "6a8c0000-0000-4000-8000-000000000000"


def _log(label: str, obj) -> None:
    print(f"[spike] {label}: {json.dumps(obj, ensure_ascii=False, default=str)[:800]}")


def _answer(q: str, text: str) -> None:
    print(f"\n>>> ANSWER {q}: {text}\n")


def _make_backend() -> OpenAIBackendAPI:
    token = get_pool().select()
    return OpenAIBackendAPI(access_token=token)


def _consume_gen(backend, prompt, refs, inject_prepare: bool):
    """Run upload -> bootstrap -> requirements -> prepare -> gen, consume SSE.

    inject_prepare: ad-hoc interceptor that adds conversation_id +
    parent_message_id (from image_wire state) to the PREPARE body too, so Q3
    can be tested before the real prepare wrapper exists.

    Returns dict(status, conversation_id, file_ids, error).
    """
    result = {"status": "ok", "conversation_id": "", "file_ids": [], "error": ""}
    try:
        if refs:
            for idx, ref in enumerate(refs, start=1):
                backend._upload_image(ref, f"image_{idx}.png")
        backend._bootstrap()
        reqs = backend._get_chat_requirements()

        followup = image_wire.current_followup()
        restore = None
        if inject_prepare and followup is not None:
            real_post = backend.session.post

            def _post(url, *args, **kwargs):
                body = kwargs.get("json")
                if (
                    isinstance(body, dict)
                    and "partial_query" in body
                    and body.get("system_hints") == ["picture_v2"]
                ):
                    body = dict(
                        body,
                        conversation_id=followup.conversation_id,
                        parent_message_id=followup.parent_message_id,
                    )
                    kwargs["json"] = body
                    _log("prepare body (injected)", {
                        "conversation_id": followup.conversation_id,
                        "parent_message_id": followup.parent_message_id,
                    })
                return real_post(url, *args, **kwargs)

            backend.session.post = _post

            def restore():
                backend.session.post = real_post

        try:
            conduit = backend._prepare_image_conversation(prompt, reqs, MODEL)
        finally:
            if restore is not None:
                restore()

        response = backend._start_image_generation(
            prompt, reqs, conduit, MODEL, refs)
        try:
            for payload in iter_sse_payloads(response):
                conv_id, file_ids, _sediment = extract_conversation_ids(payload)
                if conv_id:
                    result["conversation_id"] = conv_id
                result["file_ids"].extend(file_ids)
        finally:
            response.close()
    except UpstreamHTTPError as exc:
        result["status"] = f"upstream_{exc.status_code}"
        result["error"] = str(exc.body)[:400]
    except Exception as exc:
        result["status"] = f"error_{type(exc).__name__}"
        result["error"] = str(exc)[:400]
    finally:
        image_wire.clear_followup()
    return result


def main() -> None:
    pool = get_pool()
    print(f"[spike] accounts: {[a.get('email') for a in pool._accounts]}")

    backend = _make_backend()

    # --- Step 1: base conversation (reuse via SPIKE_BASE_CONV to save a slot).
    conv_id = os.environ.get("SPIKE_BASE_CONV", "").strip()
    if conv_id:
        print(f"\n=== STEP 1: reusing existing conversation {conv_id} ===")
    else:
        print("\n=== STEP 1: base T2I generation ===")
        image_wire.begin_generation()
        base = _consume_gen(backend, "a simple blue circle on white background",
                            None, inject_prepare=False)
        _log("base gen", base)
        if base["status"] != "ok" or not base["conversation_id"]:
            print("[spike] base generation failed - aborting spike")
            sys.exit(1)
        conv_id = base["conversation_id"]

    # --- Step 2: conversation detail + leaf resolution. -------------------
    # Spike findings so far (this section encodes them):
    #   * create_time lives at MESSAGE level (msg.create_time), not metadata.
    #   * the TRUE leaf after an image gen is a role=tool node (image tool
    #     result) - excluding "tool" as the plan draft assumed picks the wrong
    #     parent. The response's `current_node` field points exactly at it.
    print("\n=== STEP 2: GET conversation detail, inspect mapping ===")
    detail = backend._get_conversation(conv_id)
    mapping = detail.get("mapping") or {}
    for nid, node in mapping.items():
        message = node.get("message") or {}
        role = ((message.get("author") or {}).get("role") or "")
        _log("node", {
            "id": nid[:12],
            "role": role,
            "children": len(node.get("children") or []),
            "create_time": message.get("create_time"),
        })
    current_node = detail.get("current_node") or ""
    if current_node and current_node in mapping:
        current_message = (mapping[current_node].get("message") or {})
        leaf_id = current_message.get("id") or current_node
        leaf_role = ((current_message.get("author") or {}).get("role") or "")
        print(f"[spike] leaf from current_node: {leaf_id} (role={leaf_role})")
    else:
        leaves = [
            (
                (node.get("message") or {}).get("create_time") or 0,
                (node.get("message") or {}).get("id"),
                ((node.get("message") or {}).get("author") or {}).get("role"),
            )
            for node in mapping.values()
            if not node.get("children") and (node.get("message") or {}).get("id")
        ]
        if not leaves:
            print("[spike] no leaf nodes - aborting")
            sys.exit(1)
        _, leaf_id, leaf_role = max(leaves)
        print(f"[spike] leaf from mapping walk: {leaf_id} (role={leaf_role})")

    # --- Q4 first: bogus id on the gen POST (free - rejected before gen). -
    print("\n=== Q4: gen POST with BOGUS conversation_id ===")
    image_wire.begin_generation()
    image_wire.set_followup(BOGUS_CONV, "client-created-root")
    bogus = _consume_gen(backend, "make it red", None, inject_prepare=False)
    _log("bogus gen", bogus)
    _answer("Q4", f"status={bogus['status']} body={bogus['error']!r} "
            f"(expected one of 400/404/422)")

    # --- Q6: GET conversation detail on the bogus id (free). --------------
    print("=== Q6: GET conversation detail on BOGUS id ===")
    try:
        backend._get_conversation(BOGUS_CONV)
        _answer("Q6", "unexpected SUCCESS - bogus id resolved?!")
    except UpstreamHTTPError as exc:
        _answer("Q6", f"UpstreamHTTPError status={exc.status_code} - the "
                "early-rejection route works")
    except Exception as exc:
        _answer("Q6", f"UNEXPECTED {type(exc).__name__}: {exc}")

    # --- Q2: follow-up gen with leaf pointer, prepare WITHOUT conv id. ----
    print("=== Q1+Q2: follow-up gen (prepare WITHOUT conversation_id) ===")
    image_wire.begin_generation()
    image_wire.set_followup(conv_id, leaf_id)
    followup = _consume_gen(backend, "make the circle red instead of blue",
                            None, inject_prepare=False)
    _log("follow-up gen", followup)
    _answer("Q1", f"status={followup['status']} "
            f"conversation_id={followup['conversation_id']!r}")
    _answer("Q2", "prepare WITHOUT conversation_id "
            + ("WORKS for follow-up" if followup["status"] == "ok"
               else f"does NOT work ({followup['status']})"))

    # --- Q3: prepare WITH conversation_id (ad-hoc injection). -------------
    print("=== Q3: prepare WITH conversation_id + leaf ===")
    image_wire.begin_generation()
    image_wire.set_followup(conv_id, leaf_id)
    prep = _consume_gen(backend, "now make it green", None, inject_prepare=True)
    _log("prepare-injected gen", prep)
    _answer("Q3", f"status={prep['status']} body={prep['error']!r} "
            f"conversation_id={prep['conversation_id']!r}")

    # --- Q5: follow-up with ZERO new images (costs 1 slot if accepted). ---
    print("=== Q5: follow-up with zero new images attached ===")
    image_wire.begin_generation()
    image_wire.set_followup(conv_id, leaf_id)
    noimg = _consume_gen(backend, "add a yellow square next to it", None,
                         inject_prepare=False)
    _log("no-image follow-up", noimg)
    _answer("Q5", f"status={noimg['status']} "
            f"conversation_id={noimg['conversation_id']!r}")

    print("\n[spike] done - record the ANSWER lines in the plan before PR B.")


if __name__ == "__main__":
    main()
