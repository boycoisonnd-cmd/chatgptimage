# Error Catalog — Visible Public Errors

> Workstream 1I. Classify ONLY from visible text/state. Normalize lowercase+whitespace.
> Unrecognized error → `DOLA_VISIBLE_ERROR` (never guess quota). CAPTCHA always → BLOCKED.

## Records

```yaml
code: QUOTA_LIMIT
visible_patterns:
  - "usage limit"
  - "try again later"
severity: blocked
retry_policy: user-only
user_message_vi: "Dola đang báo hết lượt hoặc giới hạn sử dụng."
automation_action: stop
```

| Code | Visible patterns (lowercase) | Severity | Retry policy | User message (vi) | Automation action |
|---|---|---|---|---|---|
| LOGIN_REQUIRED |  | blocked | user-only | "Bạn cần đăng nhập Dola trước." | stop |
| SESSION_EXPIRED |  | blocked | user-only | "Phiên Dola hết hạn, đăng nhập lại." | stop |
| CAPTCHA |  | blocked | user-only | "Dola yêu cầu xác minh, xử lý thủ công." | stop |
| QUOTA_LIMIT |  | blocked | user-only | "Dola đang báo hết lượt hoặc giới hạn sử dụng." | stop |
| RATE_LIMIT |  | blocked | user-only | "Dola báo giới hạn tốc độ, chờ rồi thử lại." | stop |
| CONTENT_REJECTED |  | blocked | user-only | "Nội dung bị Dola từ chối." | stop |
| PROMPT_TOO_LONG |  | warn | user-only | "Prompt quá dài." | stop |
| NETWORK_OFFLINE |  | error | manual | "Mất kết nối mạng." | stop |
| SERVER_ERROR |  | error | manual | "Dola báo lỗi máy chủ." | stop |
| GENERATION_TIMEOUT |  | error | manual | "Tạo ảnh quá lâu, kiểm tra trong Dola." | stop |
| MAINTENANCE |  | blocked | user-only | "Dola đang bảo trì." | stop |
| TAB_NAVIGATED |  | info | none | "Tab đã đổi trang." | stop |
| TAB_CLOSED |  | info | none | "Tab đã đóng." | stop |
| UI_CHANGED |  | error | none | "Giao diện Dola có thể đã thay đổi." | stop + circuit breaker |
| DOLA_VISIBLE_ERROR |  | error | none | "Dola báo lỗi không xác định." | stop |

## Classification rules

- Text-based only. No token/API check.
- Multi-language via separate catalog.
- No error text sent to external server.

## Completion

- [ ] CAPTCHA always → BLOCKED
- [ ] No auto-retry for quota/CAPTCHA/moderation
- [ ] Vietnamese messages with next action
