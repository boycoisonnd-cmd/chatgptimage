# Result Extraction — Baseline-Delta Strategy

> Workstream 1G. New-vs-old result discrimination §13.

## Baseline (captured before submit)

```ts
interface SubmissionBaseline {
  capturedAt: number;
  conversationUrl: string;
  visibleMessageCount: number;
  visibleAssistantResultCount: number;
  existingImageUrls: string[];
  lastMessageFingerprint: string | null;
  promptText: string;
}
```

## Fingerprint (§13.2)

```
SHA-256(
  normalized conversation URL
  + normalized visible prompt text
  + message role
  + DOM sequence index
  + first observed image URL
)
```

Do NOT put email, account name, or full DOM into fingerprint.

## URL normalization (§13.4)

- Trim whitespace
- Resolve relative via `new URL(value, location.href)`
- Strip fragment unless it affects resource
- KEEP signed query params (they may encode signature/size)

## Edge cases to verify (§13.3)

- [ ] Placeholder node replaced in place
- [ ] Thumbnail swapped to HD URL
- [ ] Same URL, changed query param
- [ ] `srcset` usage
- [ ] Lazy-load on scroll
- [ ] Old messages virtualized away
- [ ] Multiple images in one container
- [ ] Multiple containers for one prompt
- [ ] Action buttons carry icon/thumbnail

## Matching trials

Required: 20 trials, 0 old-conversation images, no avatar/icon as result, no multi-image dupes.

| run_id | baseline msg count | new containers | img found | dupes | old img leaked | avatar leaked | URL swap observed |
|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  |
