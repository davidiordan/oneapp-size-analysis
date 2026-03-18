# oneapp-size-analysis — Design Spec

**Date:** 2026-03-18
**Status:** Approved

---

## Overview

`oneapp-size-analysis` is a Python CLI tool that compares the binary size of two XCArchive builds of the same iOS application. It leverages `cmpcodesize` (imported as a library) to analyze every Mach-O binary component within each archive — main executable, embedded frameworks, app extensions, and Watch app components — then produces a single, fully-verbose JSON report with Swift symbols demangled via `swift-demangle`.

---

## Goals

- Accept two `.xcarchive` paths as positional CLI arguments
- Discover and analyze every Mach-O binary component in both archives
- Produce a single JSON report capturing size differences at four levels of granularity: segments, sections, function categories, and individual functions
- Demangle all Swift-mangled symbol names before writing output
- Default output to `./analysis-reports/{AppName}-size-diff-{timestamp}.json`; allow override via `--output`/`-o`
- Be maximally verbose: no flags suppress detail

---

## Non-Goals

- No filtering or threshold options
- No support for non-macOS platforms (depends on `otool` and `xcrun swift-demangle`)
- Does not modify or extend the existing `cmpcodesize` package

---

## Package Structure

New sibling package alongside the existing `cmpcodesize/` directory:

```
OneAppSizeAnalysis/
├── cmpcodesize/                          (existing, untouched)
└── oneapp_size_analysis/
    ├── setup.py
    ├── oneapp_size_analysis/
    │   ├── __init__.py
    │   ├── main.py         (CLI entry point and argument parsing)
    │   ├── archive.py      (XCArchive traversal — discovers all Mach-O binaries)
    │   ├── analysis.py     (calls cmpcodesize.compare.read_sizes, builds raw data dicts)
    │   ├── demangle.py     (batches all symbols through xcrun swift-demangle)
    │   └── report.py       (assembles and writes the JSON report)
    └── tests/
        └── __init__.py
```

Installed via `pip install -e ./oneapp_size_analysis`, exposing the `oneapp-size-analysis` console script.

---

## CLI Interface

```
oneapp-size-analysis <old.xcarchive> <new.xcarchive> [--output <path>]
```

**Arguments:**

| Argument | Description |
|---|---|
| `old.xcarchive` | Path to the baseline XCArchive (positional) |
| `new.xcarchive` | Path to the new XCArchive to compare against (positional) |
| `--output` / `-o` | Optional path for the JSON report file |

**Default output path:** `./analysis-reports/{AppName}-size-diff-{YYYYMMDD-HHMMSS}.json`
The `analysis-reports/` directory is created if it does not exist.

---

## Module Responsibilities

### `archive.py`

Traverses an XCArchive directory and returns a list of component descriptors, each with:
- A **relative path** (used as the stable key for matching components between archives)
- An **absolute path** to the Mach-O binary on disk
- A **component type** (`main_executable`, `framework`, `extension`, `watch_app`)

Discovery rules:
- **Main executable:** Read `CFBundleExecutable` from `Products/Applications/{App}.app/Info.plist`
- **Embedded frameworks:** `Products/Applications/{App}.app/Frameworks/*.framework/{Name}`
- **App extensions:** `Products/Applications/{App}.app/PlugIns/*.appex/{Name}` (and their nested frameworks)
- **Watch app:** `Products/Applications/{App}.app/Watch/*.app/{Name}` (and their nested frameworks and extensions)

Components that exist in one archive but not the other are recorded in `components_only_in_old` and `components_only_in_new` at the report level.

### `analysis.py`

For each matched component pair, calls `cmpcodesize.compare.read_sizes()` directly (bypassing all print functions) to obtain raw `defaultdict` size data. Performs two passes per component:

1. `group_by_prefix=True` — captures section sizes and function-category sizes
2. `group_by_prefix=False` — captures per-function sizes by mangled name

Computes diffs in Python and returns structured dicts ready for JSON serialization. Prefers `arm64` architecture (matching `cmpcodesize` behavior).

### `demangle.py`

Collects every unique mangled symbol name across all components, pipes them all in one subprocess call to `xcrun swift-demangle` via stdin (one symbol per line), and returns a `{mangled_name: demangled_name}` lookup dict. Non-Swift symbols (ObjC, C++) pass through unchanged.

### `report.py`

Assembles the final JSON structure, applies demangled names from the lookup dict, creates the output directory if needed, and writes the report file with `indent=2` for human readability.

---

## JSON Report Structure

```json
{
  "metadata": {
    "generated_at": "<ISO 8601 timestamp>",
    "old_archive": "<absolute path>",
    "new_archive": "<absolute path>",
    "app_name": "<CFBundleExecutable value>",
    "architecture": "arm64"
  },
  "components": {
    "<component relative path>": {
      "type": "main_executable | framework | extension | watch_app",
      "relative_path": "<path within archive>",
      "segments": {
        "__TEXT":     { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "0.0%" },
        "__DATA":     { ... },
        "__LLVM_COV": { ... },
        "__LINKEDIT": { ... }
      },
      "sections": {
        "__text":          { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "0.0%" },
        "__stubs":         { ... },
        "__const":         { ... },
        "__cstring":       { ... },
        "__objc_methname": { ... },
        "__objc_const":    { ... },
        "__data":          { ... },
        "__swift5_proto":  { ... },
        "__common":        { ... },
        "__bss":           { ... }
      },
      "categories": {
        "Swift Function": {
          "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "0.0%",
          "old_percent_of_text": "0.0%", "new_percent_of_text": "0.0%"
        },
        "ObjC":            { ... },
        "CPP":             { ... },
        "Partial Apply":   { ... },
        "Protocol Witness":{ ... },
        "Value Witness":   { ... },
        "Type Metadata":   { ... },
        "FuncSigGen Spec": { ... },
        "Generic Spec":    { ... },
        "Partial Spec":    { ... },
        "FuncSig Spec":    { ... },
        "Generic Function":{ ... },
        "Static Func":     { ... },
        "Swift @objc Func":{ ... },
        "Accessor":        { ... },
        "Getter/Setter":   { ... },
        "Unknown":         { ... }
      },
      "functions": {
        "added": [
          { "mangled_name": "...", "demangled_name": "...", "new_bytes": 0 }
        ],
        "removed": [
          { "mangled_name": "...", "demangled_name": "...", "old_bytes": 0 }
        ],
        "increased": [
          { "mangled_name": "...", "demangled_name": "...", "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%" }
        ],
        "decreased": [
          { "mangled_name": "...", "demangled_name": "...", "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "-0.0%" }
        ],
        "unchanged": [
          { "mangled_name": "...", "demangled_name": "...", "bytes": 0 }
        ],
        "totals": {
          "added_bytes": 0,
          "removed_bytes": 0,
          "increased_bytes": 0,
          "decreased_bytes": 0,
          "net_change_bytes": 0
        }
      }
    }
  },
  "components_only_in_old": ["<relative path>"],
  "components_only_in_new": ["<relative path>"]
}
```

---

## Dependencies

| Dependency | Source |
|---|---|
| `cmpcodesize` | Sibling package — imported as a library (`cmpcodesize.compare.read_sizes`) |
| `otool` | macOS system tool (bundled with Xcode Command Line Tools) |
| `xcrun swift-demangle` | Xcode toolchain |
| Python standard library only | `argparse`, `collections`, `datetime`, `json`, `os`, `pathlib`, `plistlib`, `subprocess` |

No third-party PyPI packages required.

---

## Error Handling

- Missing or invalid XCArchive paths → clear error message, non-zero exit
- XCArchive with no `.app` bundle found → error
- `otool` or `swift-demangle` not found → error with install hint
- Component present in one archive but absent in the other → recorded in `components_only_in_old` / `components_only_in_new`, not a fatal error
