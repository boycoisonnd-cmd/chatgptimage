# Security Boundary — Dola Extension

> Plan §2.2. Non-negotiable. Code review gate.

## Banned (never in design)

- [ ] Read/copy/export `document.cookie`
- [ ] Read access/refresh/session token
- [ ] Read Dola localStorage/sessionStorage for auth material
- [ ] Intercept/modify/replay Dola internal requests
- [ ] Reverse-engineer Dola JS bundle
- [ ] Solve CAPTCHA or bypass verification
- [ ] Evade rate limits
- [ ] Random delays to hide automation
- [ ] Auto account switching
- [ ] Cookie deletion to keep running
- [ ] Unlimited auto-retry
- [ ] Batch runs without user present
- [ ] Send prompt/image/history to extension's own server
- [ ] Inject remote JavaScript into extension

## Permissions (MVP)

Planned: `tabs`, `storage`, `sidePanel`, `downloads`. Host permission only for Phase-1-confirmed origin.

NEVER add without need: `cookies`, `webRequest`, `webRequestBlocking`, `<all_urls>`, `history`, `clipboardRead`, `nativeMessaging`.

## Data handling

- History local-only (`chrome.storage.local`), no image bytes, no signed URLs longer than needed
- No prompt in logs by default
- No full outerHTML in logs
- Diagnostic snapshot (§31): adapterVersion, urlOrigin, urlPath, boolean checks, visible button labels, timestamp — no prompt/email/history/DOM dump

## Legal gate (§2.3)

- [ ] Re-read https://www.dola.com/legal/terms/en before public release
- [ ] Record date + version reviewed
- [ ] Legal opinion if distributed to third parties
- [ ] Written approval from Dola preferred
- [ ] No official-looking name/logo
- Until gate passes: internal prototype, human-operated only
