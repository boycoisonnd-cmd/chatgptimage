#!/usr/bin/env bash
# Re-vendor chatgpt2api into src/aigpt/_vendor/ from the pinned commit.
#
# - The VERBATIM subset (image-gen + OAuth core) is synced byte-for-byte from
#   upstream at the SHA in VENDOR_REV (last non-comment, non-empty line).
# - LOCAL adaptations are never overwritten - they are restored afterwards
#   (see NOTICE). Missing local files are a hard error, not silently dropped.
# - After syncing, any diff to the vendored files is printed so you can see
#   exactly what upstream changed.
#
# Requires: git, cp, diff, cmp
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/src/aigpt/_vendor"
UPSTREAM="https://github.com/basketikun/chatgpt2api.git"

# Last non-comment, non-empty line of VENDOR_REV is the pinned SHA.
REV="$(sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$ROOT/VENDOR_REV" | tail -n 1)"
if [ -z "$REV" ]; then
    echo "error: no pinned revision in VENDOR_REV" >&2
    exit 1
fi
echo "Pinned revision: $REV"

# Local files that MUST NOT be overwritten by upstream. They are backed up to
# $SCRATCH before the rm -rf below and restored afterwards.
LOCAL_FILES=(
    "services/account_service.py"
    "services/protocol/__init__.py"
    "services/storage/factory.py"
)

SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

for rel in "${LOCAL_FILES[@]}"; do
    src="$VENDOR/$rel"
    if [ ! -f "$src" ]; then
        echo "error: missing local file (expected): $src" >&2
        exit 1
    fi
    mkdir -p "$SCRATCH/local/$(dirname "$rel")"
    cp "$src" "$SCRATCH/local/$rel"
done

echo "Cloning $UPSTREAM at $REV ..."
git clone --quiet --filter=blob:none --no-checkout "$UPSTREAM" "$SCRATCH/up"
git -C "$SCRATCH/up" checkout --quiet "$REV"

# VERBATIM dirs: services/ and utils/ (their __init__.py are re-synced too).
echo "Syncing services/ and utils/ ..."
rm -rf "$VENDOR/services" "$VENDOR/utils"
cp -R "$SCRATCH/up/services" "$VENDOR/services"
cp -R "$SCRATCH/up/utils" "$VENDOR/utils"

# Restore LOCAL adaptations from the pre-sync backup (the cp -R above
# overwrote them with upstream's same-named files).
echo "Restoring local adaptations ..."
for rel in "${LOCAL_FILES[@]}"; do
    cp "$SCRATCH/local/$rel" "$VENDOR/$rel"
    if [ -f "$SCRATCH/up/$rel" ]; then
        if cmp -s "$VENDOR/$rel" "$SCRATCH/up/$rel"; then
            echo "  restored $rel (identical to upstream)"
        else
            echo "  restored $rel (local version, differs from upstream)"
        fi
    else
        echo "  restored $rel (no upstream counterpart)"
    fi
done

echo "Done. Vendored files synced to $REV."
