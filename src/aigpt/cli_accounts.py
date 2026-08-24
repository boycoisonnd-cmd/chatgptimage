"""CLI subcommands for the multi-account pool: `accounts` (list + live probe)
and `logout` (remove one or all). Kept out of cli.py to keep that file < 200 lines.

Output is English (machine/log-facing, matching the rest of the tool's output).
"""
from __future__ import annotations

import argparse

from aigpt.auth import store


def _cmd_accounts(args: argparse.Namespace) -> int:
    """List every logged-in account with a LIVE quota probe of each."""
    from aigpt.auth import tokens
    from aigpt.auth.pool import AccountPool

    if not store.load_accounts():
        print("No accounts logged in. Run `aigpt login`.")
        return 0
    pool = AccountPool(refresh_fn=tokens.refresh_for)
    rows = pool.status(probe=True)  # decision #4: `accounts` probes live each call
    print(f"{'EMAIL':<30} {'TYPE':<7} {'REMAINING':<10} {'RESETS AT':<22} STATUS")
    for r in rows:
        email = r["email"] or "(unknown)"
        remaining = "unknown" if r["remaining"] is None else str(r["remaining"])
        resets = r.get("quota_reset_at") or r.get("restore_at") or "-"
        state = ("probe failed" if r.get("probe_failed")
                 else "alive" if r["alive"] else "exhausted")
        print(f"{email:<30} {r['type']:<7} {remaining:<10} {resets:<22} {state}")
    return 0


def _cmd_logout(args: argparse.Namespace) -> int:
    """Remove one account (by email or user_id) or all accounts with --all."""
    if args.all_accounts:
        store.remove_all()
        print("[OK] Logged out all accounts.")
        return 0
    if not args.selector:
        print("Provide <email|user_id> to remove, or --all to remove everything.")
        return 2
    n = store.remove_account(args.selector)
    print(f"[OK] Removed {n} account(s).")
    return 0
