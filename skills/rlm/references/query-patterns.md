# Query Patterns

Use these templates for fast, high-recall chunk analysis.

## 1) Exact Fact Extraction

Use when you need precise entities (IDs, functions, keys, errors).

Template:

```text
Task: Extract only items that match <criterion>.
Return JSON array with objects:
{ "item": "...", "evidence": "...", "confidence": "high|medium|low" }.
Rules:
- Use evidence from this chunk only.
- If none found, return [].
- Do not infer missing values.
```

## 2) Candidate Triage

Use when searching many chunks for likely relevance.

Template:

```text
Task: Decide if this chunk is relevant to <question>.
Return JSON:
{
  "relevant": true|false,
  "reason": "<one sentence>",
  "confidence": "high|medium|low",
  "follow_up_hint": "<what to ask next if relevant>"
}
```

## 3) Conflict Check

Use when two chunks disagree.

Template:

```text
Task: Verify whether this chunk supports or contradicts: "<claim>".
Return JSON:
{
  "verdict": "supports|contradicts|insufficient",
  "evidence": "<short quote or line reference>",
  "confidence": "high|medium|low"
}
```

## 4) Recursive Follow-Up

Use only for uncertain frontier chunks after first pass.

Template:

```text
Task: Resolve uncertainty from prior pass.
Prior uncertainty: <summary>.
Focus only on:
1) missing fields,
2) ambiguous evidence,
3) conflicting statements.
Return JSON:
{
  "resolved": true|false,
  "new_facts": [],
  "remaining_gaps": [],
  "confidence": "high|medium|low"
}
```

## 5) Final Aggregation

Use after collecting structured chunk outputs.

Template:

```text
Task: Aggregate chunk-level JSON outputs into one final result.
Requirements:
- Deduplicate by canonical key.
- Preserve strongest evidence.
- Surface unresolved gaps separately.
Return JSON:
{
  "answer": ...,
  "evidence": [...],
  "unresolved": [...],
  "overall_confidence": "high|medium|low"
}
```
