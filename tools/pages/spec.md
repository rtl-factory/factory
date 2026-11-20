# Wiki site: spec

How the wiki site is built ([requirements](prd.md)). The writing rules for pages are in [wiki-site.md](https://github.com/rtl-factory/factory/blob/main/wiki/bkm/workflow/wiki-site.md).

## Parts

| Part | Role |
|---|---|
| `mkdocs.yml` | Site config: MkDocs with the Material theme, `docs_dir: wiki`, search, nav plugin, hook |
| `tools/pages/requirements.txt` | Pinned build dependencies |
| `tools/pages/hooks.py` | Adds each `tools/<tool>/prd.md` and `spec.md` to the site at `tools/<tool>/`, with "edit" links to the real file |
| `wiki/**/.nav.yml` | Folder titles and page order (mkdocs-awesome-nav) |
| `.github/workflows/pages.yml` | `build` (strict) on pull requests; `build` and `deploy` to GitHub Pages on `main` |

## Behaviour

- **Build:** `mkdocs build --strict` reads `wiki/`, adds the tool documents through the hook, and writes `site/`. Any warning, such as a broken link, fails the build.
- **Navigation:** follows the folder structure; each folder's `README.md` is its landing page; a `.nav.yml` sets a folder's title and order. Tool documents appear under Tools, one folder per tool.
- **Deploy:** on `main`, the built `site/` is uploaded as a Pages artifact and deployed. GitHub Pages must be enabled with source "GitHub Actions" (`wiki/bkm/workflow/github-settings.md`).
- **URL:** `https://rtl-factory.github.io/factory/`, set as `site_url` so pages outside the root (the 404 page) link correctly.
