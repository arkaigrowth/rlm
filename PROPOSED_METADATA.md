# Proposed GitHub Repository Metadata

These are suggestions for the GitHub-side repository settings. They cannot be set
from the repository contents, so apply them manually in the repository settings
(Settings, then the "About" panel) or via the GitHub API. Nothing here is applied
automatically.

## Description

Recommended short description (shown under the repo name and in search results):

> MCP server for recursive analysis of very large contexts. Loads context as an
> external variable, then chunks and sub-queries it so an LLM can reason over
> millions of tokens without overflowing its context window.

Keep it under the ~120 character practical limit if GitHub truncates it; a shorter
variant:

> MCP server that treats huge contexts as external variables for recursive,
> chunked LLM analysis.

## Topics

Proposed topics (lowercase, hyphenated per GitHub convention):

- `mcp`
- `model-context-protocol`
- `llm`
- `claude`
- `long-context`
- `python`

## Homepage

No dedicated project site exists yet. Options:

- Leave the homepage field empty.
- Point it at the source paper: `https://arxiv.org/abs/2512.24601`.

## Notes

- These settings are proposals only. A human should review and apply them.
- The topic list mirrors the `keywords` in `pyproject.toml` but uses GitHub's
  hyphenated topic style; keep the two roughly in sync when either changes.
