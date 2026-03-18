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
- A **relative path key** (used as the stable key for matching components between archives)
- An **absolute path** to the Mach-O binary on disk
- A **component type** (`main_executable`, `framework`, `extension`, `watch_app`)

**Discovery rules** — fully recursive. The traversal applies the following rules at each nesting level, following standard XCArchive bundle conventions:

1. **Main executable:**
   - Glob `Products/Applications/*.app` — if more than one `.app` is found, raise an error (invalid archive). If none found, raise an error.
   - Read `CFBundleExecutable` from `Products/Applications/{App}.app/Info.plist`.
   - Binary path: `Products/Applications/{App}.app/{CFBundleExecutable}`
   - Relative path key: `{App}.app/{CFBundleExecutable}`
   - Type: `main_executable`

2. **Embedded frameworks** (applies at any nesting level — inside `.app`, `.appex`, or Watch `.app`):
   - Glob `{bundle}/Frameworks/*.framework`
   - For each, read `CFBundleExecutable` from `{framework}/Info.plist`
   - Relative path key: `<parent relative path>/{Framework}.framework/{CFBundleExecutable}`
   - Type: `framework`

3. **App extensions** (applies inside `.app` bundles including Watch apps):
   - Glob `{bundle}/PlugIns/*.appex`
   - For each, read `CFBundleExecutable` from `{extension}/Info.plist`
   - Relative path key: `<parent relative path>/PlugIns/{Extension}.appex/{CFBundleExecutable}`
   - Type: `extension`
   - Recursively apply rule 2 (frameworks) inside each `.appex`

4. **Watch app:**
   - Glob `Products/Applications/{App}.app/Watch/*.app`
   - For each, read `CFBundleExecutable` from its `Info.plist`
   - Relative path key: `{App}.app/Watch/{Watch}.app/{CFBundleExecutable}`
   - Type: `watch_app`
   - Recursively apply rules 2 (frameworks) and 3 (extensions, including their nested frameworks) inside each Watch `.app`

**Matching between archives:** Components are matched by relative path key. Components present in one archive but absent in the other are excluded from analysis and recorded in `components_only_in_old` / `components_only_in_new`. They have **no entry** under `"components"` in the JSON.

**App name validation:** `archive.py` reads `CFBundleExecutable` from both the old and new archives' main app `Info.plist`. If they differ, a warning is printed to stderr (non-fatal) and both values are recorded in metadata as `old_app_name` / `new_app_name`. If they match, a single `app_name` field is used.

**Missing `CFBundleExecutable`:** If `Info.plist` for any bundle does not contain the `CFBundleExecutable` key (malformed bundle), that component is skipped with a warning to stderr. Its path key is added to `"analysis_warnings"` in the JSON. This is non-fatal.

---

### `analysis.py`

For each matched component pair, calls `cmpcodesize.compare.read_sizes()` directly (bypassing all print functions) to obtain raw `defaultdict(int)` size data.

**Pass 1 — sections, segments, and categories:**
```python
sect_sizes = collections.defaultdict(int)
seg_sizes  = collections.defaultdict(int)
read_sizes(sect_sizes, seg_sizes, path, function_details=True, group_by_prefix=True)
```

After this call, `sect_sizes` contains two kinds of keys in the same `defaultdict`, from two different data sources:
- **Section-name keys** (e.g. `"__text"`, `"__stubs"`, ...) — populated from `otool -l` load-command section header sizes (the `size` field)
- **Category-name keys** (e.g. `"Swift Function"`, `"ObjC"`, ...) — populated from disassembly label byte ranges (address arithmetic on `otool -t` output)

These are separate measurements. Section-header sizes reflect the linker's view; category sizes are the sum of disassembled instruction ranges within `__text`. They are written to the same dict under disjoint key names.

`report.py` separates them by checking each key against the canonical category-name set: `{cat[0] for cat in cmpcodesize.compare.categories}`. Keys in that set belong to `categories`; all others belong to `sections`. Any unrecognized key (e.g. a new SDK section name) is treated as a section entry for forward-compatibility.

`function_details=True` is **required** — it invokes `otool` with `-l -v -t` (load commands + disassembly), enabling both section-header parsing and per-label parsing. Without it, `otool` is called with only `-l` and category data will be empty.

`seg_sizes` must be `defaultdict(int)` because `read_sizes` writes to it when `group_by_prefix=True`.

**Pass 2 — per-function symbol sizes:**
```python
func_sizes = collections.defaultdict(int)
read_sizes(func_sizes, [], path, function_details=True, group_by_prefix=False)
```

`func_sizes` maps each mangled symbol name to its byte count. `function_details=True` is **required** — the disassembly output is what provides per-symbol labels. Without it, no symbols are collected.

Passing `[]` for `seg_sizes` is safe: the segment-accumulation branch in `read_sizes` is gated by `group_by_prefix`, so `seg_sizes` is never written when `group_by_prefix=False`. Similarly, `sect_size_match` writes are also gated by `group_by_prefix`, so Pass 2's `func_sizes` dict contains **only** label-derived symbol sizes — no section-header entries.

**`otool` failure on a specific binary:** If `otool` returns a non-zero exit code for a specific binary (e.g. invalid Mach-O, encrypted binary), `read_sizes` raises `subprocess.CalledProcessError`. `analysis.py` catches this per-component: the component is skipped, a warning is printed to stderr, and the component path key is added to a top-level `"analysis_warnings"` list in the JSON output. This is non-fatal; the rest of the run continues.

**Missing binary file:** If a discovered component's binary path (derived from `CFBundleExecutable`) does not exist on disk, `analysis.py` applies the same treatment as an `otool` failure: skip with warning to stderr, add to `"analysis_warnings"`.

**Function classification:** Because `defaultdict(int)` returns `0` for absent keys, a symbol absent from one binary and a symbol with a true computed size of `0` are indistinguishable. The implementation mirrors `cmpcodesize.compare.compare_function_sizes` exactly: a symbol is considered **absent** when its size equals `0`. Functions with a true computed size of `0` are treated as absent. This is a known limitation of `cmpcodesize`'s approach.

Classification rules:
- `old_size > 0` and `new_size == 0` → **removed**
- `old_size == 0` and `new_size > 0` → **added**
- `old_size > 0` and `new_size > 0` and `new_size > old_size` → **increased**
- `old_size > 0` and `new_size > 0` and `new_size < old_size` → **decreased**
- `old_size > 0` and `new_size > 0` and `new_size == old_size` → **unchanged**

**Sort order for function lists:** All five function lists (`added`, `removed`, `increased`, `decreased`, `unchanged`) are sorted by absolute size change descending (largest first), then by `mangled_name` ascending for stability. For `added`/`removed`, sort by `new_bytes`/`old_bytes` descending. For `increased`/`decreased`, sort by `abs(diff_bytes)` descending. For `unchanged`, sort by `bytes` descending.

**diff_percent format:** Used consistently across sections, segments, categories, and functions:
- Formula: `(new_bytes / old_bytes - 1.0) * 100.0`, rounded to one decimal place
- Positive values: `"+9.1%"`. Negative values: `"-5.2%"`.
- When `old_bytes == 0`: `"N/A"`

**totals field definitions:**
- `added_bytes` — sum of `new_bytes` for all added functions
- `removed_bytes` — sum of `old_bytes` for all removed functions
- `increased_bytes` — sum of `diff_bytes` (`new_bytes - old_bytes`) for all increased functions
- `decreased_bytes` — sum of `abs(diff_bytes)` (`old_bytes - new_bytes`) for all decreased functions (a positive number)
- `net_change_bytes` — `increased_bytes - decreased_bytes + added_bytes - removed_bytes`

**old_percent_of_text / new_percent_of_text (categories only):** The denominator is `sect_sizes["__text"]` from Pass 1 (load-command header size, post-normalization of `__textcoal_nt` → `__text`). Formatted as `"X.X%"` (no leading sign). When `__text == 0`: `"N/A"`.

**Architecture reporting:** `analysis.py` runs `otool -V -f` on each binary before calling `read_sizes` to capture the selected architecture for the JSON. The selection logic replicates `read_sizes` exactly: iterate architecture lines, record the last-seen architecture name, overwrite with `"arm64"` if `arm64` is seen (but do not break — last match wins). This is a best-effort query; because it is a separate subprocess call, it is theoretically possible for the result to differ from what `read_sizes` used (e.g. file system race). This is documented in the JSON via the `"architecture"` field. If `otool -V -f` produces no architecture lines, the field is set to `"unknown"`. Reported per component in the JSON (not globally in metadata).

---

### `demangle.py`

Collects every unique mangled symbol name across all components from both archives (from Pass 2 `func_sizes` dicts), pipes them all in one subprocess call to `xcrun swift-demangle` via stdin (one symbol per line), and returns a `{mangled_name: demangled_name}` lookup dict. Non-Swift symbols (ObjC, C++) are returned unchanged by `swift-demangle`.

---

### `report.py`

Assembles the final JSON structure using the diffs from `analysis.py` and the demangled name lookup from `demangle.py`. Creates the output directory if it does not exist. Writes the file with `indent=2` for human readability.

The `relative_path` field is included inside each component object (duplicating the dict key) for the benefit of consumers that deserialize individual component objects without the surrounding key context.

---

## JSON Report Structure

```json
{
  "metadata": {
    "generated_at": "<ISO 8601 timestamp>",
    "old_archive": "<absolute path>",
    "new_archive": "<absolute path>",
    "app_name": "<CFBundleExecutable — present only when both archives match>",
    "old_app_name": "<present only when archives differ>",
    "new_app_name": "<present only when archives differ>"
  },
  "components": {
    "<component relative path key>": {
      "type": "main_executable | framework | extension | watch_app",
      "relative_path": "<relative path key — duplicated from the dict key for standalone consumers>",
      "architecture": "<architecture selected by otool for this binary, e.g. 'arm64', or 'unknown' if not detectable>",
      "segments": {
        "__TEXT":     { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%" },
        "__DATA":     { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%" },
        "__LLVM_COV": { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%" },
        "__LINKEDIT": { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%" }
      },
      "sections": {
        "__text":          { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%" },
        "__stubs":         { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%" },
        "__const":         { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%" },
        "__cstring":       { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%" },
        "__objc_methname": { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%" },
        "__objc_const":    { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%" },
        "__data":          { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%" },
        "__swift5_proto":  { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%" },
        "__common":        { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%" },
        "__bss":           { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%" }
      },
      "categories": {
        "CPP":             { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%", "old_percent_of_text": "0.0%", "new_percent_of_text": "0.0%" },
        "ObjC":            { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%", "old_percent_of_text": "0.0%", "new_percent_of_text": "0.0%" },
        "Partial Apply":   { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%", "old_percent_of_text": "0.0%", "new_percent_of_text": "0.0%" },
        "Protocol Witness":{ "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%", "old_percent_of_text": "0.0%", "new_percent_of_text": "0.0%" },
        "Value Witness":   { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%", "old_percent_of_text": "0.0%", "new_percent_of_text": "0.0%" },
        "Type Metadata":   { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%", "old_percent_of_text": "0.0%", "new_percent_of_text": "0.0%" },
        "FuncSigGen Spec": { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%", "old_percent_of_text": "0.0%", "new_percent_of_text": "0.0%" },
        "Generic Spec":    { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%", "old_percent_of_text": "0.0%", "new_percent_of_text": "0.0%" },
        "Partial Spec":    { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%", "old_percent_of_text": "0.0%", "new_percent_of_text": "0.0%" },
        "FuncSig Spec":    { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%", "old_percent_of_text": "0.0%", "new_percent_of_text": "0.0%" },
        "Generic Function":{ "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%", "old_percent_of_text": "0.0%", "new_percent_of_text": "0.0%" },
        "Static Func":     { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%", "old_percent_of_text": "0.0%", "new_percent_of_text": "0.0%" },
        "Swift @objc Func":{ "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%", "old_percent_of_text": "0.0%", "new_percent_of_text": "0.0%" },
        "Accessor":        { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%", "old_percent_of_text": "0.0%", "new_percent_of_text": "0.0%" },
        "Getter/Setter":   { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%", "old_percent_of_text": "0.0%", "new_percent_of_text": "0.0%" },
        "Swift Function":  { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%", "old_percent_of_text": "0.0%", "new_percent_of_text": "0.0%" },
        "Unknown":         { "old_bytes": 0, "new_bytes": 0, "diff_bytes": 0, "diff_percent": "+0.0%", "old_percent_of_text": "0.0%", "new_percent_of_text": "0.0%" }
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
  "components_only_in_old": ["<relative path key>"],
  "components_only_in_new": ["<relative path key>"],
  "analysis_warnings": [
    "<human-readable message for each skipped component or otool failure>"
  ]
}
```

**Notes:**
- `__textcoal_nt` is normalized to `__text` inside `read_sizes` and will never appear as a separate section key.
- Sections with zero bytes in both old and new are still emitted with zero values (not omitted).
- Components in `components_only_in_old` / `components_only_in_new` have **no entry** under `"components"`.
- `diff_percent` is `"N/A"` when `old_bytes == 0`. `old/new_percent_of_text` is `"N/A"` when `__text == 0`.

---

## Dependencies

| Dependency | Source |
|---|---|
| `cmpcodesize` | Sibling package — `cmpcodesize.compare.read_sizes`, `cmpcodesize.compare.categories` |
| `otool` | macOS system tool (bundled with Xcode Command Line Tools) |
| `xcrun swift-demangle` | Xcode toolchain |
| Python standard library only | `argparse`, `collections`, `datetime`, `json`, `os`, `pathlib`, `plistlib`, `subprocess` |

No third-party PyPI packages required.

---

## Error Handling

- Missing or invalid XCArchive paths → error, non-zero exit
- More than one `.app` bundle found in `Products/Applications/` → error (invalid archive)
- No `.app` bundle found in `Products/Applications/` → error
- `otool` not found → error: "Install Xcode Command Line Tools: xcode-select --install"
- `xcrun swift-demangle` not found → error: "Install Xcode from the Mac App Store"
- `CFBundleExecutable` differs between old and new archives → warning to stderr; both values recorded in metadata as `old_app_name` / `new_app_name`
- `CFBundleExecutable` key absent from any bundle's `Info.plist` → skip that component; warning to stderr; entry in `"analysis_warnings"`
- Binary file not found at expected path (i.e. `CFBundleExecutable` name doesn't match actual file) → skip that component; warning to stderr; entry in `"analysis_warnings"`
- `otool` returns non-zero exit for a specific binary (invalid Mach-O, encrypted binary) → skip that component; warning to stderr; entry in `"analysis_warnings"`
- Component present in one archive but absent in the other → recorded in `components_only_in_old` / `components_only_in_new`; not a fatal error; no entry in `"components"`
