# Recursive Guardrails

Use this file to set explicit recursion and budget limits before calling `rlm_sub_query` or `rlm_sub_query_batch`.

## Default Limits

Use these defaults unless user constraints require otherwise:

| Limit | Default | Hard Ceiling | Rationale |
|---|---:|---:|---|
| `max_depth` | 0 | 2 | Shallow recursion is usually enough and safer. |
| Batch `concurrency` | 4 | 8 | Balance throughput with provider and host stability. |
| Total sub-calls per request | 48 | 96 | Prevent runaway fan-out. |
| Recursive sub-calls | 16 | 32 | Bound second-order expansion. |
| Chunks per wave | 24 | 48 | Keep aggregation and dedupe manageable. |
| Wall-clock budget | 6 min | 12 min | Preserve responsiveness. |

## Escalation Policy

Escalate only if all conditions hold:
- First pass did not satisfy acceptance criteria.
- Remaining uncertainty is localized to specific chunks.
- Remaining budget can support one more wave.

Escalation order:
1. Tighten query phrasing.
2. Re-chunk uncertain regions.
3. Enable `max_depth=1`.
4. Raise concurrency only if provider latency dominates runtime.

Do not jump directly to deeper recursion.

## Duplicate-Work Prevention

Track a signature for each semantic call:
- `(context_name, chunk_index, normalized_query, depth)`

Skip calls whose signature already exists unless prompt semantics changed.

Normalize query by:
- Lowercasing.
- Trimming whitespace.
- Removing dynamic run identifiers.

## Frontier Discipline

Partition results after each wave:
- `high`: strong evidence and clear answer.
- `medium`: partial evidence or ambiguity.
- `low`: weak evidence or irrelevant.

Recurse only on `medium` and `low`.
Never recurse on `high` unless conflict resolution is required.

## Stop Conditions

Stop immediately when any condition triggers:
- Depth reaches configured ceiling.
- Total sub-calls exceeds budget.
- Two waves add no new high-confidence findings.
- Estimated additional recall < 5%.
- Time budget exhausted.

On stop, return best-known answer and list unresolved uncertainty.
