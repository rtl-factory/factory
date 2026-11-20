---
status: draft
---

# Wiki site

How the `wiki/` folder is published as a GitHub Pages site, and how to keep it building.

## Rules

- Write wiki pages as plain Markdown under `wiki/`; the site is generated from them by MkDocs with the Material theme (`mkdocs.yml`). Why: the files stay readable on GitHub and in an editor; the site is only a view.
- Give every folder under `wiki/` a `README.md`; it is the folder's landing page. Why: a folder without one has no page to land on.
- Link between wiki pages with relative `.md` links. Link to files outside `wiki/` with full GitHub URLs. Why: relative links work both on GitHub and on the site; links out of `wiki/` break on the site.
- Set a folder's title and page order in its `.nav.yml`; without one, pages are listed alphabetically. Why: folder names like `prd` or `dv` are not readable titles.
- Pin the build dependencies in `tools/pages/requirements.txt`, and change them only through an issue. Why: an unpinned theme update can break the site without any wiki change.
- Keep `mkdocs build --strict` passing. Why: a warning is almost always a broken link.

## Steps

1. Preview locally: `pip install -r tools/pages/requirements.txt`, then `mkdocs serve` and open `http://127.0.0.1:8000`.
2. Before opening a pull request that touches `wiki/`, run `mkdocs build --strict` (as CI does) and fix every warning.
3. The `pages` workflow builds and deploys the site; when it runs is in [ci-levels.md](ci-levels.md).

## Exit criteria

- `mkdocs build` passes without warnings, and the deployed site shows the change after merge.
