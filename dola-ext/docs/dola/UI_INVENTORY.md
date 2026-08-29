# UI Inventory — Dola Survey

> Workstream 1B. Fill during live survey. Records for the 20 required controls (§8.1).
> Selector priority (§8.3): data-testid → ARIA role+name → name/type/placeholder → label relation → normalized text → container relation → short scoped CSS.
> Banned: class-hash-only, positional `querySelectorAll("button")[n]`, hidden elements, stale conversation elements, full-tree XPath.

## Control records

One record per control. Template from plan §8.5:

```yaml
name: prompt-editor
required_for: [probe, generate]
primary:
  strategy: role
  role: textbox
  accessible_name: "Ask Dola"
fallbacks:
  - strategy: textarea-placeholder
    value: "Ask anything"
  - strategy: contenteditable-in-composer
visible_check: true
enabled_check: true
postcondition: send-button-enabled
risk: medium
```

| # | Control | Required for | Primary strategy | Fallback 1 | Fallback 2 | Visible | Enabled | Postcondition | Risk |
|---|---|---:|---|---|---|---|---|---|---|
| 1 | prompt editor | probe, generate |  |  |  |  |  |  |  |
| 2 | send/generate button | generate |  |  |  |  |  |  |  |
| 3 | image mode trigger | generate |  |  |  |  |  |  |  |
| 4 | model selector | probe |  |  |  |  |  |  |  |
| 5 | new conversation button | generate |  |  |  |  |  |  |  |
| 6 | upload button | i2i (post-MVP) |  |  |  |  |  |  |  |
| 7 | file input | i2i (post-MVP) |  |  |  |  |  |  |  |
| 8 | upload preview | i2i (post-MVP) |  |  |  |  |  |  |  |
| 9 | remove attachment | i2i (post-MVP) |  |  |  |  |  |  |  |
| 10 | stop generation | generate |  |  |  |  |  |  |  |
| 11 | retry button | probe |  |  |  |  |  |  |  |
| 12 | loading indicator | observe |  |  |  |  |  |  |  |
| 13 | result message root | observe |  |  |  |  |  |  |  |
| 14 | result image root | observe |  |  |  |  |  |  |  |
| 15 | download/open image action | download |  |  |  |  |  |  |  |
| 16 | visible error container | classify |  |  |  |  |  |  |  |
| 17 | login button/banner | classify |  |  |  |  |  |  |  |
| 18 | quota banner | classify |  |  |  |  |  |  |  |
| 19 | CAPTCHA/verification container | classify |  |  |  |  |  |  |  |
| 20 | toast notification region | classify |  |  |  |  |  |  |  |

## Selector validation log

Every selector tried ≥3 reloads, wide+narrow window, new+old conversation:

| Selector | Reloads | Narrow | Wide | New conv | Old conv | Result |
|---|---:|---|---|---|---|---|
|  |  |  |  |  |  |  |
