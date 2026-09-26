# aigpt-mcp

MCP server + CLI for ChatGPT and Antigravity image generation via the unofficial ChatGPT web backend (vendored `chatgpt2api`) and the standalone Antigravity OAuth client.

> This tool talks to the ChatGPT web backend (vendored `chatgpt2api`), not an official OpenAI API. **Using it may violate OpenAI's Terms of Service and can get your account rate-limited or banned.** For experimentation only. You are responsible for your use.

## Features

- **MCP tools** `generate_image`, `login_status`, `list_image_models` — ChatGPT or Antigravity, prompt/aspect/n, provider-specific model/resolution, and **image-to-image** (edit / compose / style reference)
- **CLI** — `login`, `accounts`, `logout`, `gen`
- **HTTP REST API** (`aigpt-api`) — generate images from any project via localhost JSON
- **Chrome extension** — auto-catches the OAuth callback (no copy-paste) + a **side panel** UI to generate images from any page (click the toolbar icon)
- **Multi-account pool** — stick-until-exhausted, auto-restore detection, quota backoff
- **OAuth 2.0 (PKCE)** — 2-step terminal login via `auth.openai.com`, state-verified callback
- **Image pipeline** — PoW bootstrap → SSE → poll → download
- **Slide styles** — `auto`, `slide`, `fintech` with brand-color palette + logo-corner reservation
- **Prompt enhancement** — ChatGPT text path, offline deterministic template fallback
- **`thinking` effort** — `standard` / `extended` / `max` for better rendered text (e.g. VN diacritics)

## Requirements

- Python ≥ 3.12
- [uv](https://docs.astral.sh/uv/)

## Install

```bash
uv sync
```

## Login

Each `aigpt login` adds **one** account (run again to add more — sign out of chatgpt.com or use an incognito window first).

### Option A — Chrome extension (no terminal needed)

1. Load the extension: `chrome://extensions` → enable **Developer mode** → **Load unpacked** → pick the `extension/` folder in this repo.
2. Install the native messaging host (one-time — no admin needed):
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\install_native_host.ps1
   ```
   Restart Chrome after running this.
3. Open the side panel (click the toolbar icon) and click **Add account** — the panel auto-starts the local API server, then opens the ChatGPT login tab.
4. Sign in; the panel finishes the login by itself (extension badge turns **green OK**; the account appears in the panel).

- The panel auto-starts the API server on first use via the native messaging host (`com.aigpt.launcher` → `extension\native_host\host.bat`), then asks it (`POST /login/start` on 8789) to build the OAuth URL and host the 8788 callback receiver — no terminal command needed.
- The extension only reads the callback URL (never credentials) and posts it to the local receiver. Badge: **OK** green = login done, **ERR** red = receiver rejected the URL, **!** yellow = receiver not reachable.
- **Log out**: click **Log out** in the panel — removes the account from the pool immediately.
- If the receiver port is busy (a stale `aigpt login --wait` still running), the panel reports it — close the old process and click Add account again.
- Fall back to Option B any time: `uv run aigpt login --callback "<URL>"`.
- **Native messaging host**: `scripts\install_native_host.ps1` registers the launcher under `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.aigpt.launcher`; `scripts\uninstall_native_host.ps1` removes it. The extension id is pinned via the `"key"` in `extension\manifest.json` — if it ever changes, re-run the installer. If the host is missing, the panel falls back to the manual `uv run aigpt-api` message.

### Side panel

After loading the extension, **click the toolbar icon** — Chrome opens the GPT Image Studio side panel (works on every page, instantly, no page reload needed):

- **TEXT TO IMAGE** tab — choose GPT or Antigravity, then use the shared prompt/aspect/reference controls with provider-specific model settings
- **IMAGE TO IMAGE** tab — choose GPT or Antigravity to edit one image or compose 2–4 references; shared History and “Edit this” continue to work
- **History** — last 60 generations, stored locally (`chrome.storage.local`); click a result to open it; images come from the local API (`/file?id=…`), which restarts with the server
- **ACCOUNT** tab — manages every ChatGPT and Antigravity account in one place, including login, status/quota and logout; both providers support **Copy link** for signing in from another browser profile

The panel auto-starts the API server on first use (via the native messaging host; see [HTTP REST API](#http-rest-api)). Reload the extension after updating files: `chrome://extensions` → **Reload**.

### Antigravity OAuth and quota semantics

Antigravity is a standalone Python implementation embedded in this project's API server. It does not import, execute or read another repository at runtime. The OAuth callback listener binds only to `http://localhost:51121/oauth-callback` and the credential file is `%APPDATA%\aigpt\antigravity_auth.json` (Linux: `~/.config/aigpt/antigravity_auth.json`). Access and refresh tokens never go to the extension API response.

Use **ACCOUNT → Antigravity → Mở đăng nhập** to open Google OAuth in a new tab. To sign in with another browser profile, choose **Copy link**, paste the URL into that profile, and finish OAuth within five minutes. The callback still returns to the same local API server, so the original extension panel detects and saves the account automatically. The panel then discovers image-capable models and the quota observation returned by Google. Accounts are sticky per model and move to the next account only for an explicit quota/rate-limit response (HTTP 429 or `RESOURCE_EXHAUSTED`); timeouts and 5xx responses are not replayed on another account. A 2K request is never silently downgraded to 1K.

The Antigravity endpoints are local-only: `POST /antigravity/login/start`, `GET /antigravity/login/status`, `GET /antigravity/accounts`, `GET /antigravity/models`, `DELETE /antigravity/account?email=...`, and `POST /antigravity/generate`. The server caps request bodies at 16 MiB and references at four images. Reload the extension from `chrome://extensions` after updating the extension files.

Third-party research attribution is kept separately in `docs/THIRD_PARTY_NOTICES.md`; it is a legal notice only and is not a runtime dependency.

### Option B — manual copy-paste

```bash
# Step 1: opens the authorize URL in your browser (and prints it)
uv run aigpt login --email "you@example.com"

# Step 2: after logging into ChatGPT you land on a platform.openai.com
# page (it may say "Oops"). Copy the FULL URL from the address bar:
uv run aigpt login --callback "https://platform.openai.com/auth/callback?code=...&state=..."
```

Tokens are stored in `%APPDATA%\aigpt\auth.json` (Linux: `~/.config/aigpt/auth.json`).

> **Windows note:** on Windows, `chmod 0600` is a no-op — the file's ACL is inherited from the folder. If you share this machine, restrict the ACL manually:
> `icacls "%APPDATA%\aigpt\auth.json" /inheritance:r /grant:r "%USERNAME%:F"`

## CLI

```bash
# Live quota of all accounts (probes the backend)
uv run aigpt accounts

# Remove one account by email/user_id, or all
uv run aigpt logout "someone@example.com"
uv run aigpt logout --all

# Generate ONE image to an exact file
uv run aigpt gen "a cute cat, blue background" --out out/cat.png

# Generate several images into a directory
uv run aigpt gen "modern dashboard ui" --out out/ --n 2

# Full option set
uv run aigpt gen "slide: quarterly sales report" \
  --aspect 16:9 \
  --n 1 \
  --style slide \
  --thinking extended \
  --accent "#10B981" \
  --reserve-corner top-left \
  --no-enhance
```

`gen` options:

| Flag | Default | Description |
|------|---------|-------------|
| `prompt` (positional) | *required* | Image description / edit instruction |
| `--ref` | — | Reference image: a LOCAL file path (PNG/JPEG/WebP) or a public `https://` URL. Repeat for up to 4. 1 ref = edit in place; 2+ = compose |
| `--mode` | inferred | `generate` / `edit` (default when `--ref` given) / `style` |
| `--aspect` | `16:9` | `16:9`, `1:1`, `3:4`, `4:3`, `9:16`, custom `WxH`, or `source` |
| `--n` | `1` | Number of images (1–4) |
| `--out` | `out` | A FILE (`shot.png`) when `n=1`, otherwise a DIRECTORY |
| `--style` | `auto` | `auto`, `slide` (clean editorial), `fintech` (light-blue dashboard) |
| `--thinking` | `auto` | `auto`, `standard`, `extended`, `max` (higher = better text, slower) |
| `--quality` | `auto` | `auto`, `low`, `medium`, `high` |
| `--transparent` | *(off)* | Append a transparent-background instruction |
| `--accent` | — | Brand accent `#RRGGBB` (applied to background/accents/text) |
| `--reserve-corner` | — | `top-left`, `top-right`, `bottom-left`, `bottom-right` — keep clear for a logo; bans model-drawn logos/text |
| `--no-enhance` | *(off)* | Skip prompt auto-expansion (with `--style`/`--accent`/`--reserve-corner`, uses the offline template instead) |

Edit example:

```bash
# Edit one image in place (keep identity), source aspect
uv run aigpt gen "make the background purple" --ref photo.png --out out/edited.png

# Compose two images: person + scene
uv run aigpt gen "put person into scene" --ref person.png --ref scene.png --out out/

# Style-only: match the look, don't copy content
uv run aigpt gen "slide about coffee" --ref poster.png --mode style --out out/
```

The exact absolute path of each saved PNG is printed — callers should use it directly and never re-generate to "find" the file.

## MCP server

Configure in your MCP client (e.g. Claude Desktop / VS Code). The generated
configuration exposes two named stdio servers, one per independent account
pool:

```json
{
  "mcpServers": {
    "aigpt-chatgpt": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--project", "C:/path/to/chatgptimage", "aigpt-mcp"],
      "env": {"AIGPT_MCP_PROVIDER": "chatgpt"}
    },
    "aigpt-antigravity": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--project", "C:/path/to/chatgptimage", "aigpt-mcp"],
      "env": {"AIGPT_MCP_PROVIDER": "antigravity"}
    }
  }
}
```

### Tools

| Tool | Description |
|------|-------------|
| `generate_image` | Generate/edit/compose image(s) with ChatGPT or Antigravity; returns exact absolute file paths |
| `login_status` | Hint-based (no network): both account pools, alive/status flags, ready counts |
| `list_image_models` | List the fixed ChatGPT model or live Antigravity image models |

### `generate_image` parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `prompt` | string | *required* | Image description / edit instruction |
| `provider` | string | `auto` | `chatgpt` or `antigravity`; `auto` keeps ChatGPT compatibility or uses `AIGPT_MCP_PROVIDER` |
| `model` | string | — | Antigravity model id; ignored by ChatGPT |
| `resolution` | string | `2K` | Antigravity only: `1K` or `2K`; ChatGPT does not receive this field |
| `mode` | string | inferred | `generate` (text only), `edit` (ref + instruction; default when refs given), `style` (match ref look only) |
| `ref_image` | string | — | Alias for a single `ref_images[0]` |
| `ref_images` | array of URL | — | 1–4 entries; each a public `https://` URL or `data:image/png|jpeg|webp;base64,…` |
| `aspect` | string | `"16:9"` | `16:9`, `1:1`, `3:4`, `4:3`, `9:16`, `WxH`, or `"source"` (ratio of first ref) |
| `n` | integer | `1` | Number of images (1–4; rejected outside this range) |
| `out_dir` | string | `"out"` | Output directory (created if missing) |
| `enhance` | boolean | `true` | Auto-expand the prompt via the ChatGPT text path (never used in `edit` mode) |
| `style` | string | `"auto"` | `auto`, `slide`, `fintech` |
| `thinking` | string | `"auto"` | `auto`, `standard`, `extended`, `max` |
| `quality` | string | `"auto"` | `auto`, `low`, `medium`, `high` |
| `transparent` | boolean | `false` | Append a transparent-background instruction |
| `brand_colors` | array of hex | — | e.g. `["#10B981", "#7C3AED"]` (each must be `#RRGGBB`) |
| `reserve_corner` | string | — | `top-left` / `top-right` / `bottom-left` / `bottom-right` |

Returns `{"paths": ["C:/abs/path/img-<ts>-0.png", ...], "conversation_id": "...", "provider": "..."}`.

Antigravity uses its own Google OAuth account pool, model discovery, quota and
sticky-account/failover behavior. It ignores ChatGPT-only `thinking`, `style`,
`enhance`, and `conversation_id` fields. ChatGPT and Antigravity credentials are
never returned by the MCP tool.

> **Mode semantics** (matches ChatGPT): `edit` with 1 ref keeps the subject/identity and applies the instruction; `edit` with 2–4 refs **composes** them and honors the user's numbering ("image 1" / "ảnh 1") if they named one. `style` borrows palette/layout/type/mood only — content is **not** copied. Local file paths are rejected (H4); the CLI reads local files to `data:` URLs for you.

### Use from another project

`aigpt-mcp` shares the ChatGPT account pool (`%APPDATA%\aigpt\auth.json`) and
the separate Antigravity pool (`%APPDATA%\aigpt\antigravity_auth.json`) across
the panel, CLI, MCP and REST entry points. Any other project can use the same
logged-in accounts without re-login or touching this repo's server.

**Get the config with the right path for your machine** — run from this repo's folder (derives the path, works after copying/moving the repo):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\gen_mcp_config.ps1
```

It prints separate ChatGPT and Antigravity Claude Code commands plus a current
Claude Desktop/Cursor/VS Code JSON block. `-WriteMcpJson` also writes a
`.mcp.json` into the current folder (run it from the other project's
directory). The extension's **MCP** panel tab shows the same guide.

Manually: point your MCP client's `command` at `uv run --project <this-repo>` so `uv` resolves `aigpt-mcp` from this repo (replace `<this-repo>` with the actual path):

```json
{
  "mcpServers": {
    "aigpt-chatgpt": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--project", "C:/path/to/chatgptimage", "aigpt-mcp"],
      "env": {"AIGPT_MCP_PROVIDER": "chatgpt"}
    },
    "aigpt-antigravity": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--project", "C:/path/to/chatgptimage", "aigpt-mcp"],
      "env": {"AIGPT_MCP_PROVIDER": "antigravity"}
    }
  }
}
```

Claude Code (project-scoped, from the other project's folder):

```bash
claude mcp add --scope user aigpt-chatgpt --env AIGPT_MCP_PROVIDER=chatgpt -- uv run --project C:/path/to/chatgptimage aigpt-mcp
claude mcp add --scope user aigpt-antigravity --env AIGPT_MCP_PROVIDER=antigravity -- uv run --project C:/path/to/chatgptimage aigpt-mcp
```

Notes:
- **No server needs to be running** — the MCP client spawns `aigpt-mcp` per session over stdio. The REST server on 8789 is only for the Chrome extension side panel; other projects should use MCP, not REST.
- **No API key is required by default for this local stdio setup.** The extension's MCP tab can create an optional local API key; put it in the client's `env` as `AIGPT_MCP_API_KEY` when the client requires explicit authentication. The server stores only its hash and validates the key when the MCP process starts. ChatGPT still uses its OAuth pool and Antigravity uses Google OAuth; never put access/refresh tokens into MCP JSON. A remote HTTP MCP deployment is not enabled here.
- `generate_image` returns absolute `paths`; read the file there. Local file paths are **not** accepted for `ref_images` — upload as a public `https://` URL or `data:image/...;base64,...`.
- Accounts are provider-scoped. `aigpt login` handles ChatGPT; add Antigravity from the extension Account tab. `login_status` shows both pools without exposing tokens.

## HTTP REST API

Start the localhost API server (uses the same account pool as the CLI/MCP). The Chrome extension auto-starts it via the native messaging host; run it manually when using the CLI/MCP:

```bash
uv run aigpt-api            # http://127.0.0.1:8789
uv run aigpt-api --host 0.0.0.0 --port 9000   # LAN (exposes your accounts — be careful)
```

### POST `/generate_image`

```bash
curl -s http://127.0.0.1:8789/generate_image \
  -H "Content-Type: application/json" \
  -d '{"prompt": "a cute cat, blue background", "aspect": "16:9", "n": 1}'
```

Request body — all keys optional except `prompt`:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `prompt` | string | *required* | Image description / edit instruction |
| `mode` | string | inferred | `generate` / `edit` (default when refs given) / `style` |
| `ref_image` | string | — | Alias for a single `ref_images[0]` |
| `ref_images` | array of URL | — | 1–4 entries; each `https://` or `data:image/png|jpeg|webp;base64,…` |
| `aspect` | string | `"16:9"` | `16:9`, `1:1`, `3:4`, `4:3`, `9:16`, `WxH`, or `"source"` |
| `n` | integer | `1` | Number of images (1–4) |
| `out_dir` | string | `"out"` | Output directory (created if missing) |
| `enhance` | boolean | `true` | Auto-expand the prompt (never used in `edit` mode) |
| `style` | string | `"auto"` | `auto`, `slide`, `fintech` |
| `thinking` | string | `"auto"` | `auto`, `standard`, `extended`, `max` |
| `quality` | string | `"auto"` | `auto`, `low`, `medium`, `high` |
| `transparent` | boolean | `false` | Transparent-background instruction |
| `brand_colors` | array of hex | — | e.g. `["#10B981"]` (each must be `#RRGGBB`) |
| `reserve_corner` | string | — | `top-left` / `top-right` / `bottom-left` / `bottom-right` |

> **Breaking change:** a bare `ref_image`/`ref_images` now defaults to **`edit`** mode (edit in place / compose), not style-only. To keep the old look-alike behavior, send `"mode": "style"`. The extension's IMAGE tab already sends `mode: "style"`.

Body cap is **16 MiB** (reference images arrive as base64). Responses:

| Status | Body | Meaning |
|--------|------|---------|
| `200` | `{"paths": ["C:/abs/path/img-<ts>-0.png"], "files": ["/file?id=<hex>", …], "conversation_id": "..."}` | Done — `files` are the same images served over HTTP |
| `400` | `{"error": "..."}` | Bad request (invalid `n`, unknown param, bad JSON…) |
| `401` | `{"error": "No accounts logged in - run `aigpt login`."}` | No account |
| `403` | `{"error": "origin not allowed"}` | Cross-origin web page (see below) |
| `409` | `{"busy": true, "error": "..."}` | Another generation is running — retry later |
| `413` | `{"error": "body too large..."}` | Body over 16 MiB |
| `429` | `{"error": "...", "restore_at_epoch": 1735689600.0}` | All accounts exhausted (epoch = soonest reset) |
| `500` | `{"error": "internal error"}` | Unexpected |
| `502` | `{"error": "..."}` | Engine/upstream failure |

Generation is **sequential** (the engine is single-threaded): concurrent requests get `409 busy` — retry with a small delay.

### GET `/file?id=<hex>`

Each `200` generation response includes a `files` array of URLs like `/file?id=1a2b3c4d`. GET them to fetch the image (PNG/JPEG/WebP). The id is a random per-file token that only resolves to files **this server generated** — arbitrary paths (`/file?id=../../…`) are rejected with `404`. The registry is in-memory (last 32 images) and resets when the server restarts; stale ids return `404` — the panel shows "expired" for those.

```bash
curl -sO "http://127.0.0.1:8789/file?id=1a2b3c4d"
```

### Security model

- Binds to `127.0.0.1` only by default.
- `/generate_image` rejects requests whose `Origin` header is a web page (403). Cross-origin pages always send an Origin on POST — even a CORS-"simple" `text/plain` one — and this server never sends `Access-Control-Allow-Origin`, so a rogue site cannot burn your quota. No Origin (local curl/scripts) or a `chrome-extension://` origin passes.
- `/file` never opens a path from the query string — only registry ids.
- The login receiver (`:8788`) serves its callback token via `GET /handshake` to the extension origin only, and `POST /callback` requires that token — a rogue local process cannot replay a captured callback.

### GET `/accounts` and `/health`

```bash
curl -s http://127.0.0.1:8789/accounts   # hint-based account status (no network probe)
curl -s http://127.0.0.1:8789/health     # {"ok": true}
```

## Accounts & quota

- The pool picks an account and sticks to it until exhausted; then it moves to the next.
- `last_quota` is decremented per successful image; a `refresh_error_at` backoff bench-pauses accounts whose refresh keeps failing.
- When every account is exhausted, generation raises `NoQuotaError` carrying the earliest restore time (also visible in `aigpt accounts`).
- `login_status` is hint-based (no network probe) so MCP clients can check cheaply; `aigpt accounts` probes live quota.

## Project structure

```
├── src/aigpt/
│   ├── auth/               # OAuth (PKCE, state-verified), token store, quota pool
│   ├── engine/             # generate, enhance, thinking-effort patch
│   ├── _vendor/            # chatgpt2api engine (byte-identical, pin in VENDOR_REV)
│   │   └── services/account_service.py   # LOCAL shim — never re-vendored
│   ├── server.py           # FastMCP stdio server (aigpt-mcp)
│   ├── api.py              # HTTP REST API (aigpt-api)
│   ├── login_wait.py       # extension callback receiver (aigpt login --wait)
│   ├── cli.py              # CLI entrypoint (aigpt)
│   └── sizes.py, types.py, console.py
├── extension/              # Chrome extension (MV3): side panel + auto-catch login callback
│   ├── background.js       # Service worker: open panel on icon click + OAuth callback detection + handshake token
│   ├── iframe.html         # Side panel root: CSP-restricted, no external resources
│   ├── iframe.js           # Panel app: generate, edit, history, ref_image upload
│   ├── studio.css          # Purple dark theme, responsive
│   └── manifest.json
├── tests/                  # Pure-logic unit tests (no network)
├── scripts/update-vendor.sh
├── VENDOR_REV              # pinned upstream commit
└── pyproject.toml
```

## Vendor policy

`src/aigpt/_vendor/` is a **trimmed, byte-identical** copy of `chatgpt2api` at the commit pinned in `VENDOR_REV`. Trimmed files drop unused paths (editable export, search, codex) but never touch the interfaces the engine calls. Re-sync with `scripts/update-vendor.sh` — never hand-edit vendored files. `services/account_service.py` is a **local shim** (injected providers), not part of the vendored tree.

## Development

```bash
uv run pytest          # pure-logic tests, no network
uvx ruff check src/aigpt
```

## Disclaimer

This tool is not affiliated with, maintained by, or endorsed by OpenAI.

- Uses the ChatGPT web backend (`chatgpt2api`) — **not** the official OpenAI API
- May violate OpenAI's Terms of Service
- Your account may be rate-limited or banned
- For **experimentation only**

You are responsible for your use.

## License

MIT — see [LICENSE](LICENSE).
