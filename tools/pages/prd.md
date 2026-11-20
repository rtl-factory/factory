# Wiki site: requirements

## Problem

Captains and agents need to read the factory's BKMs, glossary, decisions, block specs and tool documents in a browser, without cloning the repo or reading raw Markdown.

## Requirements

1. Every page under `wiki/` is published, with navigation that follows the folder structure.
2. Each tool's `prd.md` and `spec.md` (`tools/<tool>/`) is published under Tools, without living under `wiki/`.
3. The site is rebuilt and published on every merge to `main` that changes what it shows.
4. A pull request that would break the site (a broken link, a missing page) fails before it merges.
5. Pages are searchable, readable in light and dark mode, and link to their source file for editing.

## Out of scope

- Generated dashboards and metrics (the tracker); they need data from CI first.
- Access control beyond what GitHub Pages gives the repository.

## Acceptance

| Requirement | Check |
|---|---|
| 1, 2 | `mkdocs build --strict` passes and the built site has a page for every `wiki/` page and every tool document |
| 3 | The `pages` workflow deploys on a merge to `main` |
| 4 | The `pages` workflow's `build` job runs `--strict` on pull requests |
| 5 | Visual check of the built site |
