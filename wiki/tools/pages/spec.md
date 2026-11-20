# Wiki site: spec

How the wiki site is built ([requirements](prd.md)). The writing rules for pages are in [wiki-site.md](../../bkm/workflow/wiki-site.md).

## Parts

| Part | Role |
|---|---|
| `mkdocs.yml` | Site config: MkDocs with the Material theme, `docs_dir: wiki`, search, nav plugin |
| `tools/pages/requirements.txt` | Pinned build dependencies |
| `wiki/**/.nav.yml` | Folder titles and page order (mkdocs-awesome-nav) |
| `.github/workflows/pages.yml` | `build` (strict) on pull requests; `build` and `deploy` to GitHub Pages on `main` |

## Behaviour

- **Build:** `mkdocs build --strict` reads `wiki/` and writes `site/`; tool documents are ordinary wiki pages ([repository layout](../../bkm/workflow/repo-layout.md)). Any warning, such as a broken link, fails the build.
- **Navigation:** follows the folder structure; each folder's `README.md` is its landing page; a `.nav.yml` sets a folder's title and order.
- **Deploy:** on `main`, the built `site/` is uploaded as a Pages artifact and deployed. GitHub Pages must be enabled with source "GitHub Actions" (`wiki/bkm/workflow/github-settings.md`).
- **URL:** `https://rtl-factory.github.io/factory/`, set as `site_url` so pages outside the root (the 404 page) link correctly.
