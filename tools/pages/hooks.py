"""MkDocs hook: publish each tool's prd.md and spec.md (tools/<tool>/) under the site's Tools section.

The documents live next to their tool, not under wiki/ (wiki/bkm/workflow/wiki-site.md).
"""
from pathlib import Path

from mkdocs.structure.files import File

DOCS = ("prd.md", "spec.md")


def on_files(files, config):
    tools = Path(config["config_file_path"]).parent / "tools"
    for doc in sorted(tools.glob("*/*.md")):
        if doc.name in DOCS:
            uri = f"tools/{doc.parent.name}/{doc.name}"
            files.append(File.generated(config, uri, abs_src_path=str(doc)))
    return files


def on_page_context(context, page, config, nav):
    # Point "edit this page" at the tool's real file, not at wiki/.
    src = page.file.src_uri
    if src.startswith("tools/") and src.endswith(DOCS):
        page.edit_url = f"{config['repo_url']}/edit/main/{src}"
    return context
