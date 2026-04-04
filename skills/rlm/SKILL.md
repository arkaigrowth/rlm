---
name: rlm
description: Budgeted recursive analysis for large contexts using RLM MCP tools. Use when code, logs, JSON, spreadsheets, or documents are too large to read directly, when searching across many files for exact facts, or when repo exploration needs chunked map-reduce analysis with strict recursion, cost, and latency guardrails.
argument-hint: [target or question]
---

# Recursive RLM

Use recursive sub-model calls to search and analyze large contexts without overloading the main context window.

Apply this workflow with the RLM MCP tools:
- `rlm_load_context`
- `rlm_inspect_context`
- `rlm_chunk_context`
- `rlm_filter_context`
- `rlm_provider_status`
- `rlm_sub_query`
- `rlm_sub_query_batch`
- `rlm_exec`
- `rlm_store_result`
- `rlm_get_results`

If these tools are unavailable, fall back to bounded local search (`rg`, targeted reads, and narrow follow-up passes).

## When To Use

Use this skill when any of the following are true:
- One file is too large to safely paste or read end-to-end.
- The task requires searching across many files for exact facts or structured evidence.
- The user wants a repo explored quickly without stuffing everything into context.
- The job naturally fits map-reduce: chunk, analyze locally, then aggregate.
- You need better recall than a single pass can provide, but want hard limits on recursion.

Do not use this skill for small, local tasks where `rg` plus a few direct reads will be faster and simpler.

## Direct Invocation

If invoked directly, treat `$ARGUMENTS` as the explicit target and/or question to answer.
- If the target is clear, proceed.
- If only the question is clear, infer the target from the working set.
- Ask a clarifying question only when ambiguity blocks safe execution.

## Operating Profile

Use this default profile unless the user asks for a different tradeoff:
- Prefer breadth-first decomposition over deep recursion.
- Leave `provider` unset unless there is a good reason to force one.
- Start with `max_depth=0`.
- Raise to `max_depth=1` only after first-pass uncertainty.
- Treat `max_depth=2` as an emergency ceiling.
- Keep concurrency at `4` by default (raise toward `8` only for I/O-heavy or cheap-model jobs).
- Filter before semantic calls whenever a lexical pre-filter is available.

This profile aligns with practical RLM behavior where shallow recursion is usually enough and deep recursion can waste budget.

## Fail-Fast Contract

Before the first semantic sub-call:
- Set explicit budgets for depth, total sub-calls, chunk count, concurrency, and time.
- State which context or file set is being externalized.
- State the fallback path if MCP tools fail or are unavailable.
- If provider setup is unclear, run `rlm_provider_status` before the first semantic call.

Abort early and fall back when any of the following are true:
- The task is already small enough for direct inspection.
- Lexical filtering reduces the search to a handful of files or snippets.
- The MCP toolchain is unavailable or returns unstable errors.
- A first-pass wave already yields high-confidence results.

Do not keep recursing just because recursion is available.

## Research-Aligned Defaults

Anchor behavior to these RLM principles:
- Keep context external and operate over references/chunks, not full prompt stuffing.
- Use iterative tool calls to decompose tasks and aggregate evidence.
- Prefer shallow recursion (`depth=1`) for most tasks; deeper trees are higher risk and often lower ROI.
- Use asynchronous batch calls where possible to lower wall-clock latency.

## Safety Contract

Enforce these safeguards on every run:

1. Set explicit budgets before the first sub-call.
2. Track call signatures and skip duplicate work.
3. Stop recursion when marginal utility is low.
4. Fail closed: if a budget trips, return best-known results plus gaps.
5. Do not recursively fan out from already-recursive branches unless the expected gain is clear and budget allows.

Load [guardrails.md](references/guardrails.md) only when setting or tuning budgets.

## Visibility Rules

When you use this skill, make the process inspectable:
- Briefly announce why RLM is being used instead of direct reads.
- Surface the initial budget: depth, concurrency, and stopping condition.
- After each wave, keep a compact tally of chunks examined, semantic calls made, and uncertain frontier size.
- At the end, report the maximum recursion depth reached, whether any budget tripped, and what remains unverified.

Keep these updates short. Visibility is required; narration is not.

## Observability Modes

Use one of these modes based on user need:
- Default: one short start update, one short finish update.
- `visibility=progress`: add wave-level counters and stop reasons.
- `visibility=debug`: include duplicate-work skips, filters applied, chunking choices, and budget consumption.

If the user asks for logs or a trail:
- Prefer a compact run summary over noisy per-call transcripts.
- Include: target, budgets, chunking strategy, wave counts, max depth reached, stop condition, unresolved gaps.
- Write a file only if the user asked for one or the task already involves artifact output; otherwise keep it inline.
- Never dump large chunk contents into the log unless the user explicitly asks for raw evidence.

## Workflow

1. Frame the target precisely.
- Convert the request into a narrow objective with acceptance criteria.
- Choose one primary output shape: exact match list, ranked candidates, structured extraction, or concise synthesis.

For repo exploration:
- Start with local file discovery first (`rg --files`, targeted `rg`).
- Externalize only the candidate files, directories, or large blobs that survive the lexical pass.

2. Externalize context.
- Load raw content with `rlm_load_context`.
- Inspect with `rlm_inspect_context`; do not paste full context into prompt.

3. Reduce search space early.
- Apply `rlm_filter_context` for obvious lexical constraints (IDs, symbols, path segments, timestamps, error tokens).
- Use `rlm_exec` for deterministic extraction/counting before semantic analysis.

4. Choose chunking intentionally.
- Code/logs: `strategy="lines"` with `size` in the 120-300 range.
- Prose/docs: `strategy="paragraphs"` with `size` in the 20-60 range.
- Semi-structured blobs: `strategy="chars"` with `size` in the 4000-12000 range.

5. Run first-pass analysis without recursion.
- Use `rlm_sub_query_batch` over candidate chunks.
- Keep prompt strict and schema-like.
- Score each chunk result as `high`, `medium`, or `low` confidence.

6. Recurse only on uncertain frontier.
- Re-run only `medium`/`low` confidence chunks.
- Increase specificity of the query and set `max_depth=1`.
- Keep branching narrow; avoid reprocessing high-confidence chunks.
- If uncertainty is still broad after one recursive wave, stop and return the best frontier plus gaps unless the user explicitly asked for exhaustive search.

7. Aggregate and verify.
- Store intermediate results with `rlm_store_result`.
- Merge with `rlm_get_results`.
- Resolve conflicts by re-checking only disagreeing evidence.
- Return final answer with confidence and unresolved gaps.

## Query Design Rules

Use query prompts that are:
- Local: answer from this chunk only.
- Bounded: fixed output fields.
- Evidence-first: include exact snippets or line anchors when possible.
- Non-speculative: state `not found` when evidence is absent.

Load [query-patterns.md](references/query-patterns.md) when you need templates for extraction, triage, and recursive follow-ups.
Load [team-guide.md](references/team-guide.md) when the output needs beginner-friendly language for mixed audiences.

## Examples

- "Find where this repo constructs Catsy import headers without reading every file into context."
- "Analyze this 2 MB JSON for all Shopify option attributes and summarize only the relevant parts."
- "Search these logs for the real cause of repeated auth failures, with evidence and counts."
- "Explore this unfamiliar repo and identify the files most likely responsible for the export flow."

## Recursion Termination Rules

Stop recursion when any condition is true:
- Depth budget reached.
- Total sub-call budget reached.
- Two consecutive waves add no new high-confidence findings.
- Additional recall estimate is below 5%.
- Time budget reached.

At stop, emit:
- Best answer so far.
- What was not verified.
- Exactly which budget ended the run.

## Performance Notes

- Prefer one expensive planning pass + many cheap narrow passes.
- Keep prompts compact and repetitive so batch calls are fast and stable.
- Use deterministic filters (`rlm_exec`, regex keep/remove) to reduce semantic token spend.
- Avoid large chunk overlap unless boundary effects are clearly harming recall.
