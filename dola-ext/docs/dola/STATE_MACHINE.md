# State Machine — Dola Generation Lifecycle

> Workstream 1F. Observed states §12.1. Fill from real runs, not guesses.
> Hard rule §12.4: a job is NOT complete just because an `img` appeared. Six conditions must all hold (new container after baseline, valid image, loaded, stable URL after debounce, indicator ended, no visible error).

## Observed states

| State | DOM signal start | DOM signal end | Coexists with | Typical duration | Suggested timeout | Has text | Has animation | Has stop btn | Partial image | URL changes during render |
|---|---|---|---|---|---|---|---|---|---|---|
| READY |  |  |  |  |  |  |  |  |  |  |
| PREPARING |  |  |  |  |  |  |  |  |  |  |
| SUBMITTING |  |  |  |  |  |  |  |  |  |  |
| SUBMITTED |  |  |  |  |  |  |  |  |  |  |
| GENERATING |  |  |  |  |  |  |  |  |  |  |
| RESULT_APPEARING |  |  |  |  |  |  |  |  |  |  |
| RESULT_STABILIZING |  |  |  |  |  |  |  |  |  |  |
| COMPLETED |  |  |  |  |  |  |  |  |  |  |
| FAILED |  |  |  |  |  |  |  |  |  |  |
| BLOCKED |  |  |  |  |  |  |  |  |  |  |

## Timeouts (from data, not guesses)

| Timeout | Value (ms) | Source run ids |
|---|---|---|
| submitConfirmationTimeoutMs |  |  |
| generationStartTimeoutMs |  |  |
| generationTotalTimeoutMs |  |  |
| resultStabilityTimeoutMs |  |  |
| downloadTimeoutMs |  |  |

## Experimental runs log (§12.3)

Required: 10 simple prompts, 5 complex, 3 slow-network (DevTools), 2 manual cancel, 2 reload mid-generation.

```yaml
run_id: P1-GEN-001
prompt_type: simple
submitted_at: ...
generation_detected_at: ...
first_image_seen_at: ...
final_image_stable_at: ...
completed_at: ...
result_count: 1
url_changed_during_render: true
notes: ...
```

| run_id | prompt_type | submitted | gen detected | first img | stable | completed | count | url changed | notes |
|---|---|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  |  |  |
