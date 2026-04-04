# RLM Skill (Claude Compatibility Bridge)

Use the canonical cross-agent skill in this repository:

`../../../skills/rlm/SKILL.md`

## Required Action

1. Read `../../../skills/rlm/SKILL.md`.
2. Follow that workflow and its references.
3. Prefer that file as source of truth for recursion limits, budgets, and query patterns.

## Fallback If Canonical Skill Is Missing

Apply this minimal profile:
- `max_depth=0` by default; raise to `1` only for unresolved chunks.
- Never exceed `max_depth=2`.
- `concurrency=4` default, `8` maximum.
- Run lexical filtering before semantic sub-queries.
- Stop when utility flattens or budgets are exhausted.
