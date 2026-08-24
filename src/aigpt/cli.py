"""aigpt CLI: login / accounts / logout / gen (image generation only)."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from typing import get_args

from aigpt.cli_accounts import _cmd_accounts, _cmd_logout
from aigpt.console import force_utf8
from aigpt.types import Style, Thinking

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


def _add_thinking_arg(p: argparse.ArgumentParser) -> None:
    """Shared --thinking option for image generation."""
    p.add_argument("--thinking", default="auto", choices=list(get_args(Thinking)),
                   help="image reasoning effort (auto<standard<extended<max); higher "
                        "= better rendered text (e.g. VN diacritics) but slower")


def _add_style_args(p: argparse.ArgumentParser) -> None:
    """Optional image-styling: brand accent color + reserved logo corner."""
    p.add_argument("--accent", default=None,
                   help="brand accent color hex, e.g. #10B981 (applied to bg/accents/text)")
    p.add_argument("--reserve-corner", dest="reserve_corner", default=None,
                   choices=["top-left", "top-right", "bottom-left", "bottom-right"],
                   help="keep this corner clear for a logo you add later")


def _cmd_login(args: argparse.Namespace) -> int:
    from aigpt.auth import oauth_login, store
    if args.wait and args.callback:
        print("error: --wait and --callback are mutually exclusive", file=sys.stderr)
        return 2
    if args.wait:
        from aigpt.login_wait import wait_for_callback
        url = oauth_login.build_and_stash(args.email or "")
        print("\n1. A browser should have opened. If not, open this URL:\n")
        print("   " + url + "\n")
        print("2. Log into ChatGPT. You'll land on a platform.openai.com page (may say 'Oops').")
        print("3. The Chrome extension catches the callback automatically.")
        print("   Or click 'Sign in with ChatGPT' in the extension panel - it opens this URL.\n")
        print(f"Waiting for the extension to post the callback to 127.0.0.1:{args.port} ...\n")
        try:
            acc = wait_for_callback(port=args.port, authorize_url=url)
        except TimeoutError as exc:
            print(f"error: {exc}", file=sys.stderr)
            print("Tip: if the extension is not installed, open chrome://extensions -> "
                  "'Load unpacked' -> this repo's extension/ folder.", file=sys.stderr)
            print("     Or fall back to: aigpt login --callback \"<URL>\"", file=sys.stderr)
            return 1
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            print("Run `aigpt login` again to start a fresh login.", file=sys.stderr)
            return 1
        except OSError as exc:
            # Port bind failure (Windows: WinError 10048, SO_EXCLUSIVEADDRUSE).
            print(f"error: could not listen on 127.0.0.1:{args.port}: {exc}",
                  file=sys.stderr)
            print("Is another `aigpt login --wait` (or the API server's login "
                  "session) already running? Close it and retry.", file=sys.stderr)
            return 1
        email = acc.get("email") or "(email unknown until first run)"
        total = len(store.load_accounts())
        print(f"[OK] Added/updated {email}. {total} account(s) logged in.")
        return 0
    if args.callback:
        acc = oauth_login.complete(args.callback)
        email = acc.get("email") or "(email unknown until first run)"
        total = len(store.load_accounts())
        print(f"[OK] Added/updated {email}. {total} account(s) logged in.")
        return 0
    url = oauth_login.build_and_stash(args.email or "")
    print("\n1. A browser should have opened. If not, open this URL:\n")
    print("   " + url + "\n")
    print("2. Log into ChatGPT. You'll land on a platform.openai.com page (may say 'Oops').")
    print("3. Copy the FULL URL from the address bar, then run:\n")
    print('   aigpt login --callback "<paste the URL here>"\n')
    print("Tip: to add a DIFFERENT account, sign out of chatgpt.com first or use an")
    print("     incognito/private window - login captures whichever account is signed in.\n")
    print("Tip: `aigpt login --wait` + the Chrome extension does this automatically.\n")
    return 0


_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _cmd_gen(args: argparse.Namespace) -> int:
    """Generate image(s) ONCE and print the exact absolute path(s) saved.

    Reliability fix: if --out looks like a file (single image), the result is
    moved to exactly that path, so callers never have to guess where the file
    landed or re-generate. Otherwise --out is treated as an output directory.
    """
    from aigpt.engine.generate import generate_image
    if args.accent and not _HEX_RE.match(args.accent):
        print(f"error: --accent must be #RRGGBB hex, got {args.accent!r}", file=sys.stderr)
        return 2
    brand_colors = [args.accent] if args.accent else None

    out = args.out
    as_file = args.n == 1 and os.path.splitext(out)[1].lower() in _IMAGE_EXTS
    work_dir = tempfile.mkdtemp(prefix="aigpt_") if as_file else out

    paths = generate_image(
        args.prompt, aspect=args.aspect, n=args.n, out_dir=work_dir,
        enhance=args.enhance, style=args.style, thinking=args.thinking,
        brand_colors=brand_colors, reserve_corner=args.reserve_corner,
    )

    if as_file:
        if paths:
            dest = os.path.abspath(out)
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            shutil.move(paths[0], dest)
            paths = [dest]
        shutil.rmtree(work_dir, ignore_errors=True)

    for p in paths:
        print(os.path.abspath(p))
    return 0 if paths else 1


def main(argv: list[str] | None = None) -> int:
    force_utf8()  # UTF-8 output so non-ASCII paths/emails never crash a cp1252 console
    p = argparse.ArgumentParser(prog="aigpt", description="ChatGPT image generation (CLI + MCP).")
    sub = p.add_subparsers(dest="cmd", required=True)

    lg = sub.add_parser("login", help="log in a ChatGPT account (additive - run again to add more)")
    lg.add_argument("--callback", default=None, help="callback URL or code (step 2)")
    lg.add_argument("--email", default=None, help="optional email hint")
    lg.add_argument("--wait", action="store_true",
                    help="wait for the Chrome extension to post the callback "
                         "(needs `aigpt login --wait` + extension/ loaded)")
    lg.add_argument("--port", type=int, default=8788,
                    help="port to listen on for the extension callback (default 8788)")
    lg.set_defaults(func=_cmd_login)

    ac = sub.add_parser("accounts", help="list logged-in accounts with live quota")
    ac.set_defaults(func=_cmd_accounts)

    lo = sub.add_parser("logout", help="remove an account (by email/user_id) or --all")
    lo.add_argument("selector", nargs="?", default=None, help="email or user_id to remove")
    lo.add_argument("--all", dest="all_accounts", action="store_true", help="remove ALL accounts")
    lo.set_defaults(func=_cmd_logout)

    g = sub.add_parser("gen", help="generate image(s); --out FILE.png for one exact file, or --out DIR")
    g.add_argument("prompt")
    g.add_argument("--aspect", default="16:9")
    g.add_argument("--n", type=int, default=1, choices=range(1, 5),
                   help="number of images (1-4)")
    g.add_argument("--out", default="out",
                   help="output FILE (e.g. shot.png) when n=1, or a DIRECTORY")
    g.add_argument("--no-enhance", dest="enhance", action="store_false",
                   help="skip auto-expanding the prompt via the ChatGPT text path")
    g.add_argument("--style", default="auto", choices=list(get_args(Style)),
                   help="'slide' = clean editorial; 'fintech' = light-blue dashboard")
    _add_thinking_arg(g)
    _add_style_args(g)
    g.set_defaults(func=_cmd_gen, enhance=True)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
