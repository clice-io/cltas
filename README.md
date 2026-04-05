# cltas

**C/C++ Language Toolchain And System** — a unified, open database of C/C++ toolchain metadata: system header paths, implicit flags, platform quirks, and cross-compilation info.

## The Problem

In the C++ ecosystem, compiler drivers, build systems, and language servers all need to resolve system headers and implicit search paths. Today, every tool re-implements its own detection logic (e.g., probing MSVC installations, sniffing GCC's internal paths). This duplicated effort is fragile, tedious, and ugly.

## Goals

### 1. Toolchain Database (core, standalone value)

Catalog how each C++ toolchain resolves system libraries — indexed by **(toolchain, version, target-triple, platform)**. Beyond detection rules, record known quirks and workarounds (e.g., version-specific bugs, non-standard search orders).

### 2. Automated Cross-Compilation

Collect platform libc metadata (glibc, musl, etc.) and sysroot structures to enable automated cross-compilation workflows (similar in spirit to `zig cc`). Planned in phases: information catalog first, automation later. Kept loosely coupled with Goal 1.

### 3. Ecosystem Collaboration (long-term)

Provide a high-quality reference dataset that other projects (language servers, build systems, compiler frontends) can consume. Rather than pushing adoption top-down, the strategy is to let coverage and quality speak for themselves.

## Design Principles

- **Data and code are separate.** The database is pure data (JSON/TOML), consumable by any language or tool. Detection libraries built on top live elsewhere.
- **Neutral naming and governance.** `cltas` is not tied to any single tool. External contributions should feel natural, not like contributing to someone else's internal module.
- **Dogfooding first.** The initial dataset will be bootstrapped by extracting [clice](https://github.com/clice-io/clice)'s existing toolchain detection logic — a real use case driving the schema design from day one.

## Status

Early stage. Schema design and initial data collection are in progress.

## License

Apache-2.0
