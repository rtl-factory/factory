# Wiki site: requirements

## Problem

Captains and agents need to read the factory's BKMs, glossary, decisions, block specs and tool documents in a browser, without cloning the repo or reading raw Markdown.

## Requirements

1. Every wiki page is published, with navigation that follows the wiki's folder structure.
2. Each tool's requirements and spec are published under Tools, while staying next to the tool rather than in the wiki.
3. The site is rebuilt and published on every merge to `main` that changes what it shows.
4. A pull request that would break the site (a broken link, a missing page) fails before it merges.
5. Pages are searchable, readable in light and dark mode, and link to their source file for editing.

## Out of scope

- Generated dashboards and metrics: the tracker tool produces those.
- Access control beyond what GitHub Pages gives the repository.

## Acceptance

| Requirement | Check |
|---|---|
| 1, 2 | The strict site build passes, and the built site has a page for every wiki page and every tool's requirements and spec |
| 3 | The site deploys on every merge to `main` that touches the wiki or a tool's documents |
| 4 | The site build runs in strict mode on every pull request that touches the wiki or a tool's documents |
| 5 | Visual check of the built site |
