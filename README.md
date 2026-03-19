# oneapp-size-analysis

A CLI tool for analyzing binary size in iOS XCArchive builds. Pass one archive to see a full breakdown of what's in it; pass two to diff them and see exactly what changed between builds.

## What It Does

The tool has two modes depending on how many archives you provide.

### Two archives — diff mode

When you build two versions of an iOS app and archive them, the compiled binaries change in ways that are hard to reason about from source alone. This mode answers the question: **where did the size go?**

It analyzes every Mach-O binary component in both archives side by side:

- **Main executable** — the primary app binary
- **Embedded frameworks** — `.framework` bundles inside the app
- **App extensions** — `.appex` bundles in `PlugIns/`, plus their embedded frameworks
- **Watch apps** — `.app` bundles in `Watch/`, plus their frameworks and extensions

For each matched component pair it produces:

- **Segments** — `__TEXT`, `__DATA`, `__LLVM_COV`, `__LINKEDIT` sizes with old/new/diff
- **Sections** — `__text`, `__stubs`, `__const`, `__cstring`, ObjC sections, Swift sections, etc.
- **Categories** — cmpcodesize's language-level groupings: Swift functions, ObjC methods, C++, generics, protocols, metadata, and more — each with its percentage of `__text`
- **Functions** — every symbol classified as added, removed, grown, shrunk, or unchanged, sorted by impact, with Swift symbols demangled into human-readable form

Components that appear in only one archive (e.g. a framework was added or removed) are listed separately. Non-fatal errors (missing `Info.plist`, `otool` failures on a single binary) are collected as warnings in the report rather than aborting the run.

### One archive — list mode

When you only have one build to look at, this mode answers: **what is taking up space right now?**

It runs the same deep analysis on every component — segments, sections, language-category breakdown — but instead of diffs it reports absolute sizes. Every symbol in every binary is listed with its byte count and demangled name, sorted largest first so the biggest contributors are immediately visible.

## Building an XCArchive for Analysis

To get meaningful results, the archived binary must **not** be stripped. By default, Xcode strips symbols from Release builds, which causes the functions list to be empty. Pass `STRIP_INSTALLED_PRODUCT=NO` to preserve them.

### Project-based (no workspace)

```bash
xcodebuild archive \
  -project YourApp.xcodeproj \
  -scheme YourScheme \
  -configuration Release \
  -archivePath ~/Archives/YourApp.xcarchive \
  STRIP_INSTALLED_PRODUCT=NO \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGN_IDENTITY="" \
  CODE_SIGNING_REQUIRED=NO
```

### Workspace-based (CocoaPods, SPM with generated workspace, etc.)

```bash
xcodebuild archive \
  -workspace YourApp.xcworkspace \
  -scheme YourScheme \
  -configuration Release \
  -archivePath ~/Archives/YourApp.xcarchive \
  STRIP_INSTALLED_PRODUCT=NO \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGN_IDENTITY="" \
  CODE_SIGNING_REQUIRED=NO
```

| Flag | Purpose |
|---|---|
| `STRIP_INSTALLED_PRODUCT=NO` | **Required.** Keeps function symbols in the binary. Without this, all function lists will be empty. |
| `CODE_SIGNING_ALLOWED=NO` | Skips code signing — not needed for analysis builds. |
| `CODE_SIGN_IDENTITY=""` | Clears any signing identity. |
| `CODE_SIGNING_REQUIRED=NO` | Prevents signing from being required by the target. |

The archive is written to the path you specify with `-archivePath`. Pass that path directly to `oneapp-size-analysis`.

> **Diagnosing a stripped binary:** If your report shows `"architecture": "unknown"`, `__text` has non-zero bytes, but all categories and the functions list are empty, the binary was stripped. Rebuild with `STRIP_INSTALLED_PRODUCT=NO`.

## Prerequisites

- **macOS** (this tool shells out to macOS-only tools)
- **Python 3.9+**
- **Xcode Command Line Tools** — provides `otool`

```bash
xcode-select --install
```

- **Xcode** (full install, from the Mac App Store) — provides `xcrun swift-demangle`

The tool checks for both at startup and exits with a clear error if either is missing.

## Installation

This repo contains two Python packages that must both be installed:

```bash
# From the repo root
pip install -e ./cmpcodesize
pip install -e ./oneapp_size_analysis
```

The `-e` flag installs in editable mode, so local source changes take effect immediately without reinstalling.

After installation the `oneapp-size-analysis` command is available in your shell:

```bash
oneapp-size-analysis --help
```

If you want to isolate dependencies, create a virtual environment first:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e ./cmpcodesize
pip install -e ./oneapp_size_analysis
```

## Usage

```bash
# Diff two archives
oneapp-size-analysis OLD.xcarchive NEW.xcarchive [--output PATH]

# List sizes for a single archive
oneapp-size-analysis ARCHIVE.xcarchive [--output PATH]
```

| Argument | Description |
|---|---|
| `OLD.xcarchive` | Baseline archive (the "before" build), or the only archive in list mode |
| `NEW.xcarchive` | New archive to compare against. Omit to use list mode. |
| `--output PATH`, `-o PATH` | Optional. Path for the JSON report. |

If `--output` is not provided, the report is written to:

```
./analysis-reports/{AppName}-size-diff-{YYYYMMDD-HHMMSS}.json   # diff mode
./analysis-reports/{AppName}-size-list-{YYYYMMDD-HHMMSS}.json   # list mode
```

The `analysis-reports/` directory is created automatically if it does not exist.

### Diff mode example

```bash
oneapp-size-analysis \
  ~/Archives/MyApp-1.0.xcarchive \
  ~/Archives/MyApp-1.1.xcarchive \
  --output ~/Desktop/size-report.json
```

Progress is printed to stderr:

```
  [1/4] Analyzing MyApp.app/MyApp ...
  [2/4] Analyzing MyApp.app/Frameworks/Foo.framework/Foo ...
  [3/4] Analyzing MyApp.app/Frameworks/Bar.framework/Bar ...
  [4/4] Analyzing MyApp.app/PlugIns/MyExt.appex/MyExt ...
Demangling symbols ...
Report written to: ~/Desktop/size-report.json
Components analyzed: 4/4
```

### List mode example

```bash
oneapp-size-analysis ~/Archives/MyApp-1.1.xcarchive
```

```
  [1/4] Listing MyApp.app/MyApp ...
  [2/4] Listing MyApp.app/Frameworks/Foo.framework/Foo ...
  [3/4] Listing MyApp.app/Frameworks/Bar.framework/Bar ...
  [4/4] Listing MyApp.app/PlugIns/MyExt.appex/MyExt ...
Demangling symbols ...
Report written to: ./analysis-reports/MyApp-size-list-20260318-143000.json
Components listed: 4/4
```

## Output Format

Both modes write a pretty-printed JSON file. The structure differs in the `functions` field and the top-level metadata.

### Diff mode

Top-level structure:

```json
{
  "metadata": {
    "generated_at": "2026-03-18T14:30:00.123456",
    "old_archive": "/path/to/Old.xcarchive",
    "new_archive": "/path/to/New.xcarchive",
    "app_name": "MyApp"
  },
  "components": { ... },
  "components_only_in_old": [],
  "components_only_in_new": [],
  "analysis_warnings": []
}
```

If the two archives have different app names (e.g. comparing a Debug build to a Release build of a differently named target), `app_name` is replaced with `old_app_name` and `new_app_name`.

Each key in `components` is the component's relative path within the app bundle:

```json
"MyApp.app/MyApp": {
  "type": "main_executable",
  "relative_path": "MyApp.app/MyApp",
  "architecture": "arm64",
  "segments": {
    "__TEXT": { "old_bytes": 12582912, "new_bytes": 13107200, "diff_bytes": 524288, "diff_percent": "+4.2%" },
    "__DATA": { ... }
  },
  "sections": {
    "__text": { "old_bytes": 9437184, "new_bytes": 9961472, "diff_bytes": 524288, "diff_percent": "+5.6%" },
    "__stubs": { ... }
  },
  "categories": {
    "Swift Function": {
      "old_bytes": 5242880, "new_bytes": 5767168, "diff_bytes": 524288, "diff_percent": "+10.0%",
      "old_percent_of_text": "55.6%", "new_percent_of_text": "57.9%"
    },
    "ObjC": { ... }
  },
  "functions": {
    "added": [
      { "mangled_name": "_$s...", "demangled_name": "MyModule.MyClass.newMethod()", "new_bytes": 512 }
    ],
    "removed": [ ... ],
    "increased": [
      {
        "mangled_name": "_$s...", "demangled_name": "MyModule.MyClass.existingMethod()",
        "old_bytes": 256, "new_bytes": 384, "diff_bytes": 128, "diff_percent": "+50.0%"
      }
    ],
    "decreased": [ ... ],
    "unchanged": [ ... ],
    "totals": {
      "added_bytes": 4096,
      "removed_bytes": 1024,
      "increased_bytes": 8192,
      "decreased_bytes": 512,
      "net_change_bytes": 10752
    }
  }
}
```

**Function sort order:**
- `added` / `removed` — largest first
- `increased` — largest growth first
- `decreased` — largest reduction first
- `unchanged` — largest first

### List mode

Top-level structure:

```json
{
  "metadata": {
    "generated_at": "2026-03-18T14:30:00.123456",
    "archive": "/path/to/MyApp.xcarchive",
    "app_name": "MyApp"
  },
  "components": { ... },
  "analysis_warnings": []
}
```

Each component has the same `segments`, `sections`, and `categories` fields, but with absolute sizes instead of diffs. The `functions` field is a flat list sorted largest first:

```json
"MyApp.app/MyApp": {
  "type": "main_executable",
  "relative_path": "MyApp.app/MyApp",
  "architecture": "arm64",
  "segments": {
    "__TEXT": { "bytes": 13107200 },
    "__DATA": { ... }
  },
  "sections": {
    "__text": { "bytes": 9961472 },
    "__stubs": { ... }
  },
  "categories": {
    "Swift Function": { "bytes": 5767168, "percent_of_text": "57.9%" },
    "ObjC": { ... }
  },
  "functions": [
    { "mangled_name": "_$s...", "demangled_name": "MyModule.MyClass.bigMethod()", "bytes": 4096 },
    { "mangled_name": "_$s...", "demangled_name": "MyModule.MyClass.mediumMethod()", "bytes": 2048 },
    ...
  ],
  "totals": {
    "function_count": 1482,
    "total_function_bytes": 9437184
  }
}
```

**Component types (both modes):** `main_executable`, `framework`, `extension`, `watch_app`

## How It Works

### Pipeline

**Diff mode (two archives):**

```
XCArchives (old + new)
    │
    ▼
archive.py          Recursively walks both XCArchive bundles,
                    reading Info.plist to find each binary.
                    Returns a list of ComponentDescriptors.
    │
    ▼
Component matching  Pairs old and new components by relative path.
                    Unmatched components are listed separately.
    │
    ▼
analysis.py         For each matched pair, calls cmpcodesize's
  analyze_component read_sizes() twice:
                    - Pass 1 (group_by_prefix=True): segments, sections, categories
                    - Pass 2 (group_by_prefix=False): per-symbol sizes
                    Classifies function changes, computes diffs.
    │
    ▼
demangle.py         Collects every mangled symbol name from all
                    components and sends them to xcrun swift-demangle
                    in a single batch subprocess call.
    │
    ▼
report.py           Applies demangled names, assembles the report
                    dict, writes JSON.
```

**List mode (one archive):**

```
XCArchive
    │
    ▼
archive.py          Recursively walks the XCArchive bundle,
                    reading Info.plist to find each binary.
    │
    ▼
analysis.py         For each component, calls read_sizes() twice:
  list_component    - Pass 1 (group_by_prefix=True): segments, sections, categories
                    - Pass 2 (group_by_prefix=False): per-symbol absolute sizes
                    Produces a flat function list sorted by size descending.
    │
    ▼
demangle.py         Same batch demangling pass as diff mode.
    │
    ▼
report.py           Applies demangled names, assembles the report
                    dict, writes JSON.
```

### XCArchive Layout

The tool expects the standard Xcode archive structure:

```
MyApp.xcarchive/
└── Products/
    └── Applications/
        └── MyApp.app/
            ├── Info.plist              (CFBundleExecutable = "MyApp")
            ├── MyApp                   ← main binary
            ├── Frameworks/
            │   └── Foo.framework/Foo   ← framework binary
            ├── PlugIns/
            │   └── MyExt.appex/        ← extension
            │       ├── MyExt
            │       └── Frameworks/Bar.framework/Bar
            └── Watch/
                └── MyWatch.app/        ← Watch app
                    ├── MyWatch
                    ├── Frameworks/Baz.framework/Baz
                    └── PlugIns/MyComp.appex/MyComp
```

### Size Analysis

Size data comes from `cmpcodesize`, an Apple-internal tool included in this repo. It uses `otool` to parse Mach-O sections and computes symbol sizes from the `__text` segment. For fat (multi-architecture) binaries it selects the first architecture slice — the same behavior as `cmpcodesize` itself.

Categories are `cmpcodesize`'s language-level breakdown of `__text` content (Swift functions, ObjC methods, C++ code, etc.). Each category also reports its size as a percentage of total `__text` so you can see the language composition shift between builds.

### Swift Demangling

Swift compiler output uses mangled symbol names like `_$s6MyApp0A5ClassC9newMethodyyF`. The tool passes all symbols through `xcrun swift-demangle` in a single batch call, producing human-readable names like `MyApp.MyClass.newMethod()`. Non-Swift symbols (ObjC, C, C++) pass through unchanged.

## Project Structure

```
oneapp_size_analysis/
├── setup.py
├── oneapp_size_analysis/
│   ├── __init__.py
│   ├── main.py        CLI entry point and pipeline orchestration
│   ├── archive.py     XCArchive traversal and component discovery
│   ├── analysis.py    Size diff computation via cmpcodesize
│   ├── demangle.py    Batch Swift symbol demangling
│   └── report.py      JSON report assembly and file writing
└── tests/
    ├── test_archive.py
    ├── test_analysis.py
    ├── test_demangle.py
    └── test_report.py
```

## Running Tests

```bash
cd oneapp_size_analysis
python -m pytest tests/ -v
```

All tests use mocks or `tmp_path` fixtures and do not require real XCArchives or system binaries.
