# Risk Register — Dola Extension

> Workstream 1K. Base risks from plan §17. Each risk needs owner, detection signal, mitigation, residual risk.

| # | Risk | Probability | Impact | Detection signal | Mitigation | Residual risk |
|---|---|---|---:|---|---|---|
| R1 | Dola changes DOM | high | high | adapter failures / UI_CHANGED | semantic selectors + fallbacks, circuit breaker | medium |
| R2 | Dola bans automation | high | high | account action / terms update | legal gate, internal prototype only | medium |
| R3 | Duplicate submit | medium | high | message count delta after submit | submittedAt lock, no auto-reclick | low |
| R4 | Old image grabbed as result | medium | high | baseline-delta mismatch in tests | baseline-delta, fingerprint, stability window | low |
| R5 | Image URL expires | high | medium | download 403/404 | download immediately, keep local metadata | low |
| R6 | CAPTCHA | medium | high | visible pattern match | stop, user completes manually | medium |
| R7 | Virtualized DOM | medium | high | missing nodes / reorder | fingerprint + observer scope | medium |
| R8 | Content script lost on reload | high | medium | runtime.lastError, port close | tab navigation cancellation → TAB_NAVIGATED | low |
| R9 | Image payload too large (blob/data) | medium | high | size check > limit | chunked transfer post-MVP, fallback to Dola action | medium |
| R10 | Service worker sleep | high | medium | idle timeout | persisted job summary + ports | low |
| R11 | Duplicate submit race (worker+content lock skew) | low | high | test double-click | single source of truth = worker; content lock second | low |
| R12 | Signed URL in history leaks via extension storage | medium | medium | audit storage | keep only while needed, allow clear | low |

## Log

| Date | Risk | Action |
|---|---|---|
| 2026-08-29 | All | Register created from plan §17; awaiting Phase 1 data |
