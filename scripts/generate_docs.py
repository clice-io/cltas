#!/usr/bin/env python3
"""Generate MkDocs site from TOML data files."""

import shutil
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
TEXT_ENCODING = "utf-8"

CATEGORIES = [
    ("compilers", "Compilers"),
    ("build-systems", "Build Systems"),
    ("tools", "Tools"),
    ("runtimes", "Runtimes & Libraries"),
    ("targets", "Targets & Platforms"),
]

# Display names for files that can't be title-cased naively
DISPLAY_NAMES = {
    "llvm-clang": "LLVM/Clang",
    "gcc": "GCC",
    "msvc": "MSVC",
    "amd-rocm": "AMD ROCm",
    "arm-compiler": "ARM Compiler",
    "intel": "Intel oneAPI",
    "nvidia": "NVIDIA CUDA/HPC",
    "zig": "Zig",
    "apple-clang": "Apple Clang",
    "edg": "EDG (Edison Design Group)",
    "cray": "Cray C++ (CCE)",
    "conda-forge-compilers": "conda-forge Compilers",
    "build-systems": "Build Systems",
    "build-tools": "Build Accelerators",
    "analysis-tools": "Analysis & Formatting Tools",
    "cxxstdlib": "C++ Standard Libraries",
    "libc": "C Libraries (libc)",
    "runtime-libs": "Runtime Libraries",
    "sanitizers": "Sanitizers & Instrumentation",
    "architectures": "CPU Architectures",
    "platforms": "Platforms & OS",
    "llvm-targets": "LLVM Registered Targets",
    "linkers": "Linkers",
    "debuggers": "Debuggers",
    "binary-utils": "Binary Utilities",
    "profilers": "Profilers",
    "coverage": "Code Coverage",
    "testing": "Testing Frameworks",
    "documentation": "Documentation Tools",
    "package-managers": "Package Managers",
}


def display_name(stem: str) -> str:
    return DISPLAY_NAMES.get(stem, stem.replace("-", " ").title())


def format_value(val):
    if isinstance(val, str):
        if val.startswith("http"):
            return f"[{val}]({val})"
        # Escape angle brackets for markdown
        return val.replace("<", "&lt;").replace(">", "&gt;")
    if isinstance(val, bool):
        return "`true`" if val else "`false`"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, list):
        if not val:
            return "—"
        if all(isinstance(v, str) for v in val):
            return ", ".join(f"`{v}`" for v in val)
        return str(val)
    return str(val)

def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding=TEXT_ENCODING)

def render_releases_table(releases, max_cols=6):
    if not releases:
        return ""

    # Sort newest first by date (or version as fallback)
    def sort_key(r):
        return r.get("date", r.get("version", ""))
    releases = sorted(releases, key=sort_key, reverse=True)

    skip = {"download_urls", "assets", "url"}
    keys = []
    for r in releases:
        for k in r:
            if k not in keys and k not in skip:
                keys.append(k)
    keys = keys[:max_cols]

    lines = [
        "| " + " | ".join(k for k in keys) + " |",
        "| " + " | ".join("---" for _ in keys) + " |",
    ]
    for r in releases:
        cells = []
        for k in keys:
            v = r.get(k, "")
            cells.append(format_value(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)

def render_mapping_table(mapping):
    if not mapping:
        return ""
    lines = [
        "| key | value |",
        "| --- | --- |",
    ]
    for k, v in mapping.items():
        lines.append(f"| `{k}` | {format_value(v)} |")
    return "\n".join(lines)

def render_details_block(title: str, table: str):
    if not table:
        return []
    lines = [f"??? note \"{title}\"", ""]
    for line in table.split("\n"):
        lines.append(f"    {line}")
    lines.append("")
    return lines


def render_item_card(item):
    lines = []
    name = item.get("name", item.get("full_name", "Unknown"))
    lines.append(f"### {name}\n")

    releases = None
    for k, v in item.items():
        if k == "name":
            continue
        if isinstance(v, list) and v and isinstance(v[0], dict):
            releases = (k, v)
            continue
        if isinstance(v, dict):
            table = render_mapping_table(v)
            if table:
                lines.extend(render_details_block(f"{k} ({len(v)})", table))
            else:
                lines.append(f"- **{k}**: {format_value(v)}")
            continue
        lines.append(f"- **{k}**: {format_value(v)}")

    lines.append("")
    if releases:
        key, data = releases
        lines.extend(render_details_block(f"{len(data)} {key}", render_releases_table(data)))

    return "\n".join(lines)


def render_file(filepath: Path) -> str:
    with open(filepath, "rb") as f:
        data = tomllib.load(f)

    parts = []
    stem = filepath.stem
    meta = data.get("meta")

    title = display_name(stem)
    if meta:
        title = meta.get("name", title)

    parts.append(f"# {title}\n")

    if meta:
        if vendor := meta.get("vendor"):
            parts.append(f"*by {vendor}*\n")
        if desc := meta.get("description"):
            parts.append(f"{desc}\n")
        if url := meta.get("url"):
            parts.append(f":material-link: [{url}]({url})\n")

    for key, value in data.items():
        if key == "meta":
            continue

        if isinstance(value, list) and value and isinstance(value[0], dict):
            has_names = "name" in value[0]
            parts.append(f"## {key} ({len(value)})\n")
            if has_names:
                for item in value:
                    parts.append(render_item_card(item))
            else:
                parts.append(render_releases_table(value))
                parts.append("")
        elif isinstance(value, dict):
            section_name = value.get("name", value.get("full_name", key))
            parts.append(f"## {section_name}\n")
            parts.append(render_item_card(value))
        else:
            parts.append(f"**{key}**: {format_value(value)}\n")

    return "\n".join(parts)


def generate():
    if DOCS_DIR.exists():
        shutil.rmtree(DOCS_DIR)
    DOCS_DIR.mkdir(parents=True)

    # Write index
    write_text(
        DOCS_DIR / "index.md",
        "# cltas\n\n"
        "**C/C++ Language Toolchain And System** — a unified, open database of C++ toolchain metadata.\n\n"
        "Browse the categories in the navigation to explore:\n\n"
        "- :material-cog: **Compilers** — LLVM/Clang, GCC, MSVC, NVIDIA, ARM, Intel, and more\n"
        "- :material-hammer-wrench: **Build Systems** — CMake, Meson, Ninja, Bazel, xmake, and more\n"
        "- :material-tools: **Tools** — Build accelerators, static analysis, formatting\n"
        "- :material-library: **Runtimes & Libraries** — libc, C++ stdlib, runtime libs, sanitizers\n"
        "- :material-target: **Targets & Platforms** — CPU architectures, OS, environments\n"
    )

    # Build nav structure for mkdocs.yml
    # Each category becomes a top-level tab, with its pages in the sidebar
    nav = [{"Home": [{"Overview": "index.md"}]}]

    for dir_name, label in CATEGORIES:
        dir_path = DATA_DIR / dir_name
        if not dir_path.exists():
            continue
        files = sorted(dir_path.glob("*.toml"))
        if not files:
            continue

        out_dir = DOCS_DIR / dir_name
        out_dir.mkdir(parents=True, exist_ok=True)

        # Write a category index page
        cat_index = out_dir / "index.md"
        file_links = "\n".join(
            f"- [{display_name(f.stem)}]({f.stem}.md)" for f in files
        )
        write_text(cat_index, f"# {label}\n\n{file_links}\n")

        section_items = [{"Overview": f"{dir_name}/index.md"}]
        for filepath in files:
            md_content = render_file(filepath)
            md_file = out_dir / f"{filepath.stem}.md"
            write_text(md_file, md_content)

            name = display_name(filepath.stem)
            rel = f"{dir_name}/{filepath.stem}.md"
            section_items.append({name: rel})

        nav.append({label: section_items})

    # Generate mkdocs.yml
    write_mkdocs_yml(nav)
    print(f"Generated MkDocs site in {DOCS_DIR}")
    print(f"Run: cd {ROOT} && mkdocs serve")


def write_mkdocs_yml(nav):
    """Write mkdocs.yml with Material theme config."""
    import yaml

    # Format nav section with yaml
    nav_yaml = yaml.dump({"nav": nav}, default_flow_style=False, sort_keys=False, allow_unicode=True)

    yml_path = ROOT / "mkdocs.yml"
    write_text(yml_path, f"""\
site_name: "cltas — C/C++ Language Toolchain And System"
site_url: https://clice-io.github.io/cltas
repo_url: https://github.com/clice-io/cltas
repo_name: clice-io/cltas

theme:
  name: material
  palette:
    - scheme: slate
      primary: indigo
      accent: blue
      toggle:
        icon: material/brightness-4
        name: Switch to light mode
    - scheme: default
      primary: indigo
      accent: blue
      toggle:
        icon: material/brightness-7
        name: Switch to dark mode
  features:
    - navigation.instant
    - navigation.tabs
    - navigation.tabs.sticky
    - navigation.top
    - search.suggest
    - search.highlight
    - content.tabs.link
  icon:
    repo: fontawesome/brands/github

markdown_extensions:
  - admonition
  - pymdownx.details
  - pymdownx.superfences
  - pymdownx.emoji:
      emoji_index: !!python/name:material.extensions.emoji.twemoji
      emoji_generator: !!python/name:material.extensions.emoji.to_svg
  - tables
  - attr_list

{nav_yaml}
""")
    print(f"Written {yml_path}")


if __name__ == "__main__":
    generate()
