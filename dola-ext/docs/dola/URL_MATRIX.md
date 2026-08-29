# URL Matrix — Dola Survey

> Workstream 1A. Fill during live survey. One row per visited URL.
> Origin candidates from plan §7.1: `https://www.dola.com/`, `https://www.dola.com/chat`, `https://chat.dola.com/`

## Live observations (fill per navigation)

| URL pattern | Status | Content script | Purpose | Redirect | SPA route | Notes |
|---|---|---:|---|---|---|---|
| `https://www.dola.com/` |  |  | landing | 302 → /chat/ (observed) |  |  |
| `https://www.dola.com/chat` |  |  |  |  |  |  |
| `https://chat.dola.com/` |  |  |  |  |  |  |
| post-login |  |  |  |  |  |  |
| new conversation |  |  |  |  |  |  |
| image mode |  |  |  |  |  |  |
| old conversation |  |  |  |  |  |  |
| logout |  |  |  |  |  |  |
| verification page |  |  |  |  |  |  |

## Per-URL checklist (Workstream 1B §7.2)

For each supported URL record:

- [ ] Origin
- [ ] Path
- [ ] Redirect? → where
- [ ] SPA route? uses `history.pushState`?
- [ ] Content script survives route change?
- [ ] DOM root
- [ ] iframe? cross-origin iframe?
- [ ] Shadow DOM?
- [ ] lazy loading?
- [ ] virtualized list?
- [ ] layout differs narrow window?
- [ ] light/dark theme differs?
- [ ] UI language differs?

## Completion criteria (1A)

- [ ] Origin confirmed for manifest host permission (no wildcard)
- [ ] Conversation URL pattern identified
- [ ] Route-change behavior identified
- [ ] Which tabs qualify for a job

## Evidence

| Date | URL | Method (manual/DevTools) | Notes |
|---|---|---|---|
| 2026-08-29 | https://www.dola.com/ | WebFetch | 302 → http://www.dola.com/chat/; title "Dola AI — Your everyday AI assistant"; body not observable remotely |
