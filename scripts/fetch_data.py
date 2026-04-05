#!/usr/bin/env python3
"""
Fetch C++ toolchain data from various sources and write TOML files.

Usage:
    python3 scripts/fetch_data.py              # fetch all
    python3 scripts/fetch_data.py compilers    # fetch only compilers
    python3 scripts/fetch_data.py runtimes     # fetch only runtimes
    ...

Requires: requests, tomli_w, tomllib (Python 3.11+)
"""

import json
import re
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path

import requests
import tomli_w

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def gh_api(endpoint: str, paginate: bool = True) -> list:
    """Call GitHub API via gh CLI, using --jq to flatten paginated arrays."""
    cmd = ["gh", "api", endpoint, "--cache", "1h"]
    if paginate:
        cmd += ["--paginate", "--jq", ".[]"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=300)
        text = result.stdout.strip()
        if not text:
            return []
        if paginate:
            # --jq '.[]' outputs one JSON object per line
            items = []
            for line in text.splitlines():
                line = line.strip()
                if line:
                    items.append(json.loads(line))
            return items
        return json.loads(text) if text else []
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"  WARNING: gh api {endpoint} failed: {e}")
        return []


def gh_releases(owner_repo: str, include_prerelease: bool = False) -> list[dict]:
    """Fetch GitHub releases, return list of {version, date, tag, platforms, assets}."""
    data = gh_api(f"repos/{owner_repo}/releases")
    releases = []
    for r in data:
        if not include_prerelease and r.get("prerelease"):
            continue
        if r.get("draft"):
            continue
        tag = r.get("tag_name", "")
        version = tag.lstrip("vV").removeprefix("release/").removeprefix("release-")
        date = (r.get("published_at") or r.get("created_at") or "")[:10]
        assets = [a["name"] for a in r.get("assets", [])]
        entry = {"version": version, "date": date, "tag": tag}
        if assets:
            entry["assets"] = assets
        releases.append(entry)
    releases.sort(key=lambda x: x["date"])
    return releases


def gh_tags(owner_repo: str) -> list[dict]:
    """Fetch GitHub tags, return list of {version, tag}."""
    data = gh_api(f"repos/{owner_repo}/tags")
    tags = []
    for t in data:
        name = t.get("name", "")
        version = name.lstrip("vV")
        tags.append({"version": version, "tag": name})
    return tags


def fetch_url(url: str, timeout: int = 30) -> str:
    """Fetch a URL and return text content."""
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "cltas-bot/1.0"})
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        print(f"  WARNING: fetch {url} failed: {e}")
        return ""


def write_toml(path: Path, data: dict):
    """Write a TOML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(data, f)
    print(f"  Written {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")


def merge_toml_list(path: Path, key: str, fetched_items: list[dict]):
    """Merge fetched items into an existing TOML file's list, keyed by 'name'.

    Items with matching names get their 'releases' updated.
    Items not in fetched data are preserved as-is (static/curated entries).
    New items from fetch are appended.
    """
    existing = {}
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)
        for item in data.get(key, []):
            existing[item.get("name", "")] = item

    fetched_names = set()
    for item in fetched_items:
        name = item.get("name", "")
        fetched_names.add(name)
        if name in existing:
            # Update releases but keep other curated fields
            existing[name]["releases"] = item.get("releases", [])
        else:
            existing[name] = item

    # Preserve order: fetched first, then existing-only
    merged = []
    for item in fetched_items:
        merged.append(existing[item["name"]])
    for name, item in existing.items():
        if name not in fetched_names:
            merged.append(item)

    write_toml(path, {key: merged})


def extract_platforms_from_assets(assets: list[str]) -> list[str]:
    """Extract platform identifiers from release asset filenames."""
    platforms = set()
    for name in assets:
        # Skip signatures, checksums, source tarballs
        if any(name.endswith(ext) for ext in [".sig", ".asc", ".sha256", ".md5", ".txt"]):
            continue
        if "source" in name.lower() or name.endswith(".src.rpm"):
            continue
        # Try to extract platform from filename
        for pat in [
            r"(x86_64|amd64|i[36]86|aarch64|arm64|armv\d+\w*|ppc64le?|s390x|riscv64|wasm32)",
            r"(linux|darwin|macos|windows|win64|win32|freebsd|woa64)",
            r"(ubuntu[\d.]*|rhel\d+|centos\d+|debian\d+)",
        ]:
            for m in re.finditer(pat, name, re.IGNORECASE):
                platforms.add(m.group(1).lower())
    return sorted(platforms)


# ---------------------------------------------------------------------------
# Fetchers: Compilers
# ---------------------------------------------------------------------------

def fetch_llvm_clang():
    print("Fetching LLVM/Clang releases...")
    releases = gh_releases("llvm/llvm-project", include_prerelease=True)
    # Only keep llvmorg- tags
    filtered = []
    for r in releases:
        tag = r.get("tag", "")
        if tag.startswith("llvmorg-") or tag.startswith("llvm-"):
            r["version"] = tag.removeprefix("llvmorg-").removeprefix("llvm-")
            platforms = extract_platforms_from_assets(r.pop("assets", []))
            if platforms:
                r["platforms"] = platforms
            filtered.append(r)
    write_toml(DATA_DIR / "compilers" / "llvm-clang.toml", {
        "meta": {
            "name": "LLVM/Clang",
            "vendor": "LLVM Project",
            "url": "https://github.com/llvm/llvm-project",
            "description": "LLVM compiler infrastructure and Clang C/C++/Objective-C frontend",
        },
        "releases": filtered,
    })


def fetch_gcc():
    print("Fetching GCC releases...")
    html = fetch_url("https://gcc.gnu.org/releases.html")
    if not html:
        print("  Skipping GCC (fetch failed)")
        return
    releases = []
    # Pattern: <td><a ...>GCC X.Y</a></td>  <td>Month Day, Year</td>
    for m in re.finditer(
        r'GCC\s+([\d.]+)\s*</a>\s*</td>\s*<td>\s*(\w+\s+\d+,?\s+\d{4})', html
    ):
        version = m.group(1)
        date_str = m.group(2).replace(",", "")
        try:
            dt = datetime.strptime(date_str, "%B %d %Y")
            date = dt.strftime("%Y-%m-%d")
        except ValueError:
            date = date_str
        major = version.split(".")[0]
        series = major if int(major) >= 5 else ".".join(version.split(".")[:2])
        releases.append({"version": version, "date": date, "series": series})
    releases.sort(key=lambda x: x["date"])
    write_toml(DATA_DIR / "compilers" / "gcc.toml", {
        "meta": {
            "name": "GCC",
            "vendor": "GNU Project",
            "url": "https://gcc.gnu.org/",
            "description": "GNU Compiler Collection",
        },
        "releases": releases,
    })


def fetch_amd_rocm():
    print("Fetching AMD ROCm releases...")
    releases = gh_releases("ROCm/ROCm")
    for r in releases:
        r.pop("assets", None)
    write_toml(DATA_DIR / "compilers" / "amd-rocm.toml", {
        "meta": {
            "name": "AMD ROCm",
            "vendor": "AMD",
            "url": "https://github.com/ROCm/ROCm",
            "description": "AMD ROCm open software platform for GPU computing (hipcc, amdclang++)",
        },
        "releases": releases,
    })


def fetch_zig():
    print("Fetching Zig releases...")
    releases = gh_releases("ziglang/zig")
    for r in releases:
        platforms = extract_platforms_from_assets(r.pop("assets", []))
        if platforms:
            r["platforms"] = platforms
    write_toml(DATA_DIR / "compilers" / "zig.toml", {
        "meta": {
            "name": "Zig",
            "vendor": "Zig Software Foundation",
            "url": "https://ziglang.org/",
            "description": "Zig programming language and toolchain, usable as a C/C++ cross-compiler (zig cc/c++)",
        },
        "releases": releases,
    })


# ---------------------------------------------------------------------------
# Fetchers: Build Systems
# ---------------------------------------------------------------------------

GITHUB_BUILD_SYSTEMS = {
    "CMake":    {"repo": "Kitware/CMake",           "vendor": "Kitware",     "url": "https://cmake.org/",            "desc": "Cross-platform build system generator"},
    "Meson":    {"repo": "mesonbuild/meson",        "vendor": "Meson Project","url": "https://mesonbuild.com/",      "desc": "Fast and user-friendly build system"},
    "Ninja":    {"repo": "ninja-build/ninja",       "vendor": "Google",      "url": "https://ninja-build.org/",      "desc": "Small, fast build system focused on speed"},
    "Bazel":    {"repo": "bazelbuild/bazel",        "vendor": "Google",      "url": "https://bazel.build/",          "desc": "Fast, scalable, multi-language build system"},
    "Buck2":    {"repo": "facebook/buck2",          "vendor": "Meta",        "url": "https://buck2.build/",          "desc": "Large-scale build system from Meta"},
    "xmake":    {"repo": "xmake-io/xmake",         "vendor": "xmake-io",    "url": "https://xmake.io/",            "desc": "Cross-platform build utility based on Lua"},
    "Premake":  {"repo": "premake/premake-core",    "vendor": "Premake",     "url": "https://premake.github.io/",   "desc": "Build configuration tool generating IDE projects and makefiles", "prerelease": True},
    "SCons":    {"repo": "SCons/scons",             "vendor": "SCons Foundation","url": "https://scons.org/",        "desc": "Software construction tool using Python"},
    "Conan":    {"repo": "conan-io/conan",          "vendor": "JFrog",       "url": "https://conan.io/",            "desc": "C/C++ package manager and build integrator"},
    "vcpkg":    {"repo": "microsoft/vcpkg",         "vendor": "Microsoft",   "url": "https://vcpkg.io/",            "desc": "C/C++ library manager for Windows, Linux, and macOS"},
}


def fetch_build_systems():
    print("Fetching build systems releases...")
    systems = []
    for name, info in GITHUB_BUILD_SYSTEMS.items():
        print(f"  {name}...")
        releases = gh_releases(info["repo"], include_prerelease=info.get("prerelease", False))
        for r in releases:
            platforms = extract_platforms_from_assets(r.pop("assets", []))
            if platforms:
                r["platforms"] = platforms
        systems.append({
            "name": name,
            "vendor": info["vendor"],
            "url": info["url"],
            "repo": f"https://github.com/{info['repo']}",
            "description": info["desc"],
            "releases": releases,
        })
    merge_toml_list(DATA_DIR / "build-systems" / "build-systems.toml", "build_systems", systems)


# ---------------------------------------------------------------------------
# Fetchers: Tools
# ---------------------------------------------------------------------------

GITHUB_BUILD_TOOLS = {
    "ccache":     {"repo": "ccache/ccache",                       "vendor": "ccache project",   "url": "https://ccache.dev/",            "desc": "Compiler cache for fast recompilation",           "category": "build-accelerator"},
    "sccache":    {"repo": "mozilla/sccache",                     "vendor": "Mozilla",          "url": "https://github.com/mozilla/sccache","desc": "Shared compilation cache with cloud storage",   "category": "build-accelerator"},
    "FASTBuild":  {"repo": "fastbuild/fastbuild",                 "vendor": "FASTBuild",        "url": "http://www.fastbuild.org/",      "desc": "High performance build system with caching and distribution","category": "build-accelerator"},
    "buildcache": {"repo": "mbitsnbites/buildcache",              "vendor": "mbitsnbites",      "url": "https://github.com/mbitsnbites/buildcache","desc": "Advanced compiler cache",                 "category": "build-accelerator"},
    "distcc":     {"repo": "distcc/distcc",                       "vendor": "distcc project",   "url": "https://github.com/distcc/distcc","desc": "Distributed C/C++ compilation across networked machines","category": "distributed-build"},
    "icecream":   {"repo": "icecc/icecream",                      "vendor": "SUSE",             "url": "https://github.com/icecc/icecream","desc": "Distributed compiler system based on distcc, with automatic job scheduling","category": "distributed-build"},
}

GITHUB_ANALYSIS_TOOLS = {
    "cppcheck":              {"repo": "danmar/cppcheck",                              "vendor": "Cppcheck",  "url": "https://cppcheck.sourceforge.io/","desc": "Static analysis tool for C/C++",                "category": "static-analysis"},
    "include-what-you-use":  {"repo": "include-what-you-use/include-what-you-use",    "vendor": "IWYU",      "url": "https://include-what-you-use.org/","desc": "Analyze #includes in C/C++ source files",      "category": "static-analysis"},
}


def fetch_tools():
    print("Fetching build tools...")
    tools = []
    for name, info in GITHUB_BUILD_TOOLS.items():
        print(f"  {name}...")
        releases = gh_releases(info["repo"])
        for r in releases:
            r.pop("assets", None)
        tools.append({
            "name": name,
            "vendor": info["vendor"],
            "url": info["url"],
            "category": info["category"],
            "description": info["desc"],
            "releases": releases,
        })
    merge_toml_list(DATA_DIR / "tools" / "build-tools.toml", "tools", tools)

    print("Fetching analysis tools...")
    atools = []
    for name, info in GITHUB_ANALYSIS_TOOLS.items():
        print(f"  {name}...")
        releases = gh_releases(info["repo"])
        for r in releases:
            r.pop("assets", None)
        atools.append({
            "name": name,
            "vendor": info["vendor"],
            "url": info["url"],
            "category": info["category"],
            "description": info["desc"],
            "releases": releases,
        })
    merge_toml_list(DATA_DIR / "tools" / "analysis-tools.toml", "tools", atools)


# ---------------------------------------------------------------------------
# Fetchers: Runtimes
# ---------------------------------------------------------------------------

def fetch_glibc_versions() -> list[dict]:
    """Fetch glibc release tags from sourceware git."""
    # Use GitHub mirror for tags
    data = gh_api("repos/bminor/glibc/tags")
    releases = []
    seen = set()
    for t in data:
        name = t.get("name", "")
        # Tags like "glibc-2.39", "glibc-2.38"
        m = re.match(r'glibc-([\d.]+)$', name)
        if not m:
            continue
        version = m.group(1)
        if version in seen:
            continue
        seen.add(version)
        releases.append({"version": version, "tag": name})
    # Sort by version
    def version_key(r):
        parts = r["version"].split(".")
        return tuple(int(p) for p in parts)
    releases.sort(key=version_key)
    return releases


def fetch_musl_versions() -> list[dict]:
    """Parse musl release history."""
    html = fetch_url("https://musl.libc.org/releases.html")
    if not html:
        tags = gh_tags("bminor/musl")
        return [{"version": t["version"], "tag": t["tag"]}
                for t in tags if t["version"] and t["version"][0].isdigit()]
    releases = []
    seen = set()
    # Pattern: musl-VERSION.tar.gz</a> ... - Month Day, Year
    for m in re.finditer(
        r'musl-([\d.]+)\.tar\.gz</a>.*?-\s*(\w+\s+\d+,?\s+\d{4})', html, re.DOTALL
    ):
        version = m.group(1)
        if version in seen:
            continue
        seen.add(version)
        date_str = m.group(2).replace(",", "")
        try:
            dt = datetime.strptime(date_str, "%B %d %Y")
            date = dt.strftime("%Y-%m-%d")
        except ValueError:
            date = date_str
        releases.append({"version": version, "date": date})
    releases.sort(key=lambda x: x.get("date", ""))
    return releases


def fetch_newlib_versions() -> list[dict]:
    """Fetch newlib release tags from GitHub mirror."""
    data = gh_api("repos/mirror/newlib-cygwin/tags")
    releases = []
    seen = set()
    for t in data:
        name = t.get("name", "")
        m = re.match(r'newlib-([\d.]+)$', name)
        if not m:
            continue
        version = m.group(1)
        if version in seen:
            continue
        seen.add(version)
        releases.append({"version": version, "tag": name})
    def version_key(r):
        parts = r["version"].split(".")
        return tuple(int(p) for p in parts)
    releases.sort(key=version_key)
    return releases


def fetch_uclibc_ng_versions() -> list[dict]:
    """Fetch uClibc-ng releases from GitHub."""
    return gh_releases("wbx-github/uclibc-ng")


def fetch_mingw_w64_versions() -> list[dict]:
    """Fetch mingw-w64 releases from GitHub."""
    releases = gh_releases("mingw-w64/mingw-w64")
    for r in releases:
        r.pop("assets", None)
    return releases


# Static libc entries (no reliable API for automated fetching)
STATIC_LIBC = [
    {
        "name": "bionic",
        "full_name": "Android Bionic",
        "vendor": "Google",
        "url": "https://android.googlesource.com/platform/bionic/",
        "platforms": ["android"],
        "description": "Android's C library, math library, and dynamic linker",
    },
    {
        "name": "dietlibc",
        "full_name": "diet libc",
        "vendor": "Felix von Leitner",
        "url": "https://www.fefe.de/dietlibc/",
        "platforms": ["linux"],
        "description": "Small C library optimized for small size, for embedded Linux",
    },
    {
        "name": "msvcrt",
        "full_name": "Microsoft Visual C Runtime (Legacy)",
        "vendor": "Microsoft",
        "url": "https://learn.microsoft.com/en-us/cpp/c-runtime-library/",
        "platforms": ["windows"],
        "description": "Legacy Windows C runtime library (msvcrt.dll), shipped with older Visual Studio versions",
    },
    {
        "name": "ucrt",
        "full_name": "Universal C Runtime",
        "vendor": "Microsoft",
        "url": "https://learn.microsoft.com/en-us/cpp/c-runtime-library/",
        "platforms": ["windows"],
        "description": "Modern Windows C runtime (ucrtbase.dll), ships with Windows 10+ and Visual Studio 2015+",
    },
    {
        "name": "libsystem",
        "full_name": "Apple libSystem",
        "vendor": "Apple",
        "url": "https://opensource.apple.com/",
        "platforms": ["darwin", "ios", "tvos", "watchos", "visionos"],
        "description": "Apple's system C library for macOS, iOS, and other Apple platforms",
    },
    {
        "name": "freebsd-libc",
        "full_name": "FreeBSD libc",
        "vendor": "FreeBSD Project",
        "url": "https://www.freebsd.org/",
        "platforms": ["freebsd"],
        "description": "FreeBSD's standard C library, derived from 4.4BSD",
    },
    {
        "name": "openbsd-libc",
        "full_name": "OpenBSD libc",
        "vendor": "OpenBSD Project",
        "url": "https://www.openbsd.org/",
        "platforms": ["openbsd"],
        "description": "OpenBSD's standard C library with security-focused hardening",
    },
    {
        "name": "netbsd-libc",
        "full_name": "NetBSD libc",
        "vendor": "NetBSD Project",
        "url": "https://www.netbsd.org/",
        "platforms": ["netbsd"],
        "description": "NetBSD's standard C library, highly portable across architectures",
    },
    {
        "name": "picolibc",
        "full_name": "picolibc",
        "vendor": "Keith Packard",
        "url": "https://github.com/picolibc/picolibc",
        "platforms": ["none"],
        "description": "C library for embedded systems, combining newlib and AVR libc code",
    },
    {
        "name": "wasi-libc",
        "full_name": "WASI libc",
        "vendor": "WebAssembly",
        "url": "https://github.com/WebAssembly/wasi-libc",
        "platforms": ["wasi"],
        "description": "C library for WebAssembly System Interface, built on musl and cloudlibc",
    },
]


def fetch_runtimes():
    """Fetch libc versions using merge strategy — never overwrite with empty data."""
    print("Fetching libc versions...")
    libc_path = DATA_DIR / "runtimes" / "libc.toml"

    FETCHABLE_LIBC = [
        ("glibc", fetch_glibc_versions, {
            "full_name": "GNU C Library", "vendor": "GNU Project",
            "url": "https://www.gnu.org/software/libc/", "platforms": ["linux"],
            "description": "The standard C library for GNU/Linux systems",
        }),
        ("musl", fetch_musl_versions, {
            "full_name": "musl libc", "vendor": "musl project",
            "url": "https://musl.libc.org/", "platforms": ["linux"],
            "description": "Lightweight, standards-conforming C library for Linux",
        }),
        ("newlib", fetch_newlib_versions, {
            "full_name": "Newlib", "vendor": "Red Hat / open source",
            "url": "https://sourceware.org/newlib/", "platforms": ["none", "rtems", "elf"],
            "description": "C library for embedded systems and bare-metal targets",
        }),
    ]

    fetched = []
    for name, fetcher, meta in FETCHABLE_LIBC:
        print(f"  {name}...")
        releases = fetcher()
        if not releases:
            print(f"    Skipping {name} (no data fetched, preserving existing)")
            continue
        fetched.append({"name": name, **meta, "releases": releases})

    # GitHub-released libc implementations
    for name, repo, meta_entry in [
        ("uclibc-ng", "wbx-github/uclibc-ng", {
            "full_name": "uClibc-ng", "vendor": "uClibc-ng project",
            "url": "https://uclibc-ng.org/", "platforms": ["linux"],
            "description": "Small C library for embedded Linux systems, actively maintained fork of uClibc",
        }),
        ("mingw-w64", "mingw-w64/mingw-w64", {
            "full_name": "mingw-w64", "vendor": "mingw-w64 project",
            "url": "https://www.mingw-w64.org/", "platforms": ["windows"],
            "description": "Windows C runtime headers and import libraries for GCC, targeting msvcrt or ucrt",
        }),
        ("picolibc", "picolibc/picolibc",
         next(s for s in STATIC_LIBC if s["name"] == "picolibc")),
        ("wasi-libc", "WebAssembly/wasi-libc",
         next(s for s in STATIC_LIBC if s["name"] == "wasi-libc")),
    ]:
        print(f"  {name}...")
        releases = gh_releases(repo)
        if not releases:
            print(f"    Skipping {name} (no data fetched, preserving existing)")
            continue
        for r in releases:
            r.pop("assets", None)
        fetched.append({"name": name, **meta_entry, "releases": releases})

    # Merge: update releases for fetched items, preserve everything else
    merge_toml_list(libc_path, "implementations", fetched)


# ---------------------------------------------------------------------------
# Fetchers: Targets (mostly static, but refresh from clang)
# ---------------------------------------------------------------------------

def fetch_targets():
    print("Fetching target info from clang...")
    try:
        result = subprocess.run(
            ["clang", "--print-targets"],
            capture_output=True, text=True, timeout=10
        )
        targets = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("Registered") or line.startswith("="):
                continue
            parts = line.split(" - ", 1)
            name = parts[0].strip()
            desc = parts[1].strip() if len(parts) > 1 else ""
            targets.append({"name": name, "description": desc})
        if targets:
            write_toml(DATA_DIR / "targets" / "llvm-targets.toml", {
                "meta": {
                    "name": "LLVM Registered Targets",
                    "description": "Targets registered in the local LLVM/Clang installation",
                    "source": "clang --print-targets",
                },
                "targets": targets,
            })
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("  clang not found, skipping target detection")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

FETCH_GROUPS = {
    "compilers": [fetch_llvm_clang, fetch_gcc, fetch_amd_rocm, fetch_zig],
    "build-systems": [fetch_build_systems],
    "tools": [fetch_tools],
    "runtimes": [fetch_runtimes],
    "targets": [fetch_targets],
}


def main():
    groups = sys.argv[1:] if len(sys.argv) > 1 else list(FETCH_GROUPS.keys())

    for group in groups:
        if group not in FETCH_GROUPS:
            print(f"Unknown group: {group}")
            print(f"Available: {', '.join(FETCH_GROUPS.keys())}")
            sys.exit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for group in groups:
        print(f"\n=== {group} ===")
        for fetcher in FETCH_GROUPS[group]:
            fetcher()

    print("\nDone!")


if __name__ == "__main__":
    main()
