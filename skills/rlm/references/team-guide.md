# Team Guide

Use this document when onboarding mixed technical/non-technical teammates to RLM.

## Plain-English Summary

RLM avoids stuffing huge files into one prompt.
It stores data externally, chunks it, runs small sub-calls, and merges answers.

## Provider Choices

| Provider | Key | Good For | Recursion |
|---|---|---|---|
| `openrouter` | `OPENROUTER_API_KEY` | **Recommended hosted default** | Yes |
| `openai` | `OPENAI_API_KEY` | Hosted setup without OpenRouter | Yes |
| `ollama` | none (local runtime) | Local/private/zero cost | Yes |
| `claude-sdk` | Claude SDK auth (auto) | Simple single-turn fallback only | **No** — fails fast if recursive tools requested |

## Recommended Defaults

- Hosted default: `provider="openrouter"`, model `openai/gpt-5.4-mini`
- Stronger retry pass: `provider="openrouter"`, model `openai/gpt-5.4`
- `provider="auto"` prefers `openrouter`, then `ollama`, then `openai`
- `max_depth=0` first pass
- raise to `max_depth=1` only for unresolved chunks
- do not exceed `max_depth=2`
- `concurrency=4` default
- filter first, reason second

## Safety Rules

- Set depth, call-count, and time budgets before running.
- Recurse only on uncertain chunks.
- Stop when new high-confidence findings stop improving.
- Return gaps explicitly instead of guessing.
