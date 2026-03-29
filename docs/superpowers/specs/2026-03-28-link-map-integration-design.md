# Link Map Integration Design

**Date:** 2026-03-28
**Status:** Approved

## Problem

`oneapp-size-analysis` currently analyzes every Mach-O binary component in an XCArchive and reports segment, section, category, and per-function sizes. However, it has no concept of *where a function came from*. Static libraries are linked into the main binary at archive time — their boundaries disappear from the binary itself. This means the tool can tell you "there is a 4KB function named `PluginA.SomeFeature.doThing()`" but cannot tell you "that function came from the PluginA static library, which in total contributes 524KB to this binary."

The primary use case is tracking whether individual feature plugins (internal Xcode targets, built as static libraries) are growing between builds at a rate that warrants investigation.

## Solution

Ingest an optional linker link map alongside each XCArchive. The link map is the linker's authoritative record of every symbol, its size, and the object file it originated from. By parsing it, the tool can:

1. Attribute each function entry in the report to a `library` and `source_file`
2. Produce a `libraries` aggregation block per component showing total bytes and symbol count per library, sorted by size

The link map is **optional and additive** — all existing behavior is unchanged when it is not provided.

## Size Accuracy Note

`cmpcodesize` (the underlying analysis library) computes per-symbol sizes using address arithmetic: it subtracts the address of one symbol from the address of the next. This is an approximation. The linker link map contains the exact sizes as determined by the linker.

The `libraries` aggregation block uses link map sizes (authoritative). The `functions` list uses cmpcodesize sizes (approximate). These will differ slightly for individual symbols. This difference is documented in the README and is expected — do not attempt to reconcile them in the report output.

## Generating a Link Map

Add `LD_GENERATE_MAP_FILE=YES` (and optionally `LD_MAP_FILE_PATH`) to the `xcodebuild archive` command to write the link map alongside the archive:

```bash
xcodebuild archive \
  -workspace YourApp.xcworkspace \
  -scheme YourScheme \
  -configuration Release \
  -archivePath ~/Archives/YourApp.xcarchive \
  LD_GENERATE_MAP_FILE=YES \
  LD_MAP_FILE_PATH=~/Archives/YourApp-linkmap.txt \
  STRIP_INSTALLED_PRODUCT=NO \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGN_IDENTITY="" \
  CODE_SIGNING_REQUIRED=NO
```

The link map is not written into the `.xcarchive` bundle — it must be captured separately at build time. Passing it to the tool is the developer's or pipeline's responsibility.

## Architecture

### New Module: `linkmap.py`

Responsible solely for parsing a link map file and returning structured data. No knowledge of archives, reports, or the existing analysis pipeline.

**Public API:**

```python
@dataclass
class SymbolEntry:
    library: str        # extracted library name (e.g. "PluginA")
    source_file: str    # object filename (e.g. "SomeFeature.o")
    linker_bytes: int   # authoritative size from linker
    in_text: bool       # True if this symbol is in the __TEXT __text section

@dataclass
class LibraryEntry:
    total_bytes: int    # all symbols across all sections
    text_bytes: int     # symbols in __TEXT __text only
    symbol_count: int
    # object_files is internal only — not emitted in report output

@dataclass
class LinkMapData:
    symbols: Dict[str, SymbolEntry]    # normalized_symbol_name → entry
    libraries: Dict[str, LibraryEntry] # library_name → entry
    total_text_bytes: int              # total __text bytes from link map (for percent_of_text)

def parse_link_map(path: str, warnings: List[str]) -> Optional[LinkMapData]:
    """Parse a linker link map file. Returns None on failure, appending a warning."""
```

**Symbol name normalization:**

Link map symbol names carry a leading underscore for Mach-O C-linkage symbols (e.g. `_$sSomeSwiftSymbol`, `_some_c_function`). `cmpcodesize` extracts symbol names from `otool -v -t` function labels, which also carry this leading underscore. The `symbols` dict is keyed on the raw name **as it appears in the link map**, with no normalization. Lookup in `_enrich_functions_with_linkmap` uses the `mangled_name` field from the analysis output exactly as-is — since both sources preserve the underscore prefix, no stripping is needed.

**Symbol name collision:**

If the same symbol name appears more than once in `# Symbols:` (possible when multiple translation units define a symbol with the same mangled name, e.g. after generic specialization), the **first occurrence wins** and a warning is appended:
`"Warning: duplicate symbol in link map, keeping first: <name>"`.

**Link map format parsed:**

```
# Path: /path/to/MyApp
# Arch: arm64

# Object files:
[  0] linker synthesized
[  1] /path/to/DerivedData/.../PluginA.build/Objects-normal/arm64/SomeFeature.o

# Sections:
# Address    Size        Segment Section
0x100003C64  0x000037E4  __TEXT  __text
0x100003B44  0x00000200  __TEXT  __stubs

# Symbols:
# Address    Size        File  Name
0x100003C64  0x000000AC  [  1] _$sSomeSymbol...
```

**Parsing strategy for `# Sections:` block:**

Parse this block to build an `address_range → (segment, section)` mapping. This is used to classify each symbol as `in_text = True` (i.e. it falls within a `__TEXT __text` address range) or `False`. A symbol's address is tested against each range: `section_start <= symbol_address < section_start + section_size`.

**Parsing strategy for `# Symbols:` block:**

Each non-comment line has the format:
```
0xADDRESS  0xSIZE  [FILE_INDEX]  NAME
```
Where `NAME` is everything after `[FILE_INDEX]` and may contain spaces (e.g. ObjC method names like `+[ClassName methodName:withParam:]`). Parse using a regex that captures address, size, file index, and the remainder of the line as the name.

**Library name extraction (priority order):**

1. File index `0` (linker synthesized) → `"Linker Synthesized"`
2. Path contains a `*.build` path component → strip `.build` suffix → e.g. `"PluginA"`
3. Path contains a `*.framework` path component → strip `.framework` suffix
4. Fallback → stem of the object filename (strip `.o`)

`source_file` is always the filename component of the object file path (e.g. `SomeFeature.o`).

### Modified: `main.py`

**New CLI flags** (added to the existing `argparse` parser in `main()`):

```python
parser.add_argument(
    "--link-map",
    metavar="PATH",
    help=(
        "Path to the linker link map for the archive (list mode). "
        "Enables per-function library attribution and library size breakdown."
    ),
)
parser.add_argument(
    "--old-link-map",
    metavar="PATH",
    help="Path to the linker link map for OLD.xcarchive (diff mode).",
)
parser.add_argument(
    "--new-link-map",
    metavar="PATH",
    help="Path to the linker link map for NEW.xcarchive (diff mode).",
)
```

All three flags are optional. In diff mode, `--old-link-map` and `--new-link-map` are independent — providing one without the other is valid.

**Missing link map path is fatal.** If the user passes `--link-map /path/that/does/not/exist`, the tool calls `sys.exit()` with a clear error message before doing any analysis — consistent with the existing behavior for missing archive paths. This is distinct from a parse failure inside a valid file (which is non-fatal and emits a warning).

Both `_run_list_mode` and `_run_diff_mode` validate the provided paths exist (fatal if not), then call `parse_link_map` for each (non-fatal on parse failure), and pass the resulting `Optional[LinkMapData]` objects to the report builders via the updated call sites shown below.

**Updated call site in `_run_list_mode`:**

```python
link_map: Optional[LinkMapData] = None
if args.link_map:
    if not Path(args.link_map).is_file():
        sys.exit(f"Error: link map not found: {args.link_map}")
    link_map = parse_link_map(args.link_map, warnings)

report = build_single_archive_report(
    metadata=metadata,
    component_results=component_results,
    analysis_warnings=warnings,
    demangle_lookup=demangle_lookup,
    link_map=link_map,
)
```

**Updated call site in `_run_diff_mode`:**

```python
link_map_old: Optional[LinkMapData] = None
link_map_new: Optional[LinkMapData] = None
if args.old_link_map:
    if not Path(args.old_link_map).is_file():
        sys.exit(f"Error: link map not found: {args.old_link_map}")
    link_map_old = parse_link_map(args.old_link_map, warnings)
if args.new_link_map:
    if not Path(args.new_link_map).is_file():
        sys.exit(f"Error: link map not found: {args.new_link_map}")
    link_map_new = parse_link_map(args.new_link_map, warnings)

report = build_report(
    metadata=metadata,
    component_results=component_results,
    components_only_in_old=only_in_old,
    components_only_in_new=only_in_new,
    analysis_warnings=warnings,
    demangle_lookup=demangle_lookup,
    link_map_old=link_map_old,
    link_map_new=link_map_new,
)
```

### Modified: `report.py`

**Updated function signatures:**

```python
def build_single_archive_report(
    metadata: dict,
    component_results: dict,
    analysis_warnings: list,
    demangle_lookup: dict,
    link_map: Optional[LinkMapData] = None,   # new
) -> dict:

def build_report(
    metadata: dict,
    component_results: dict,
    components_only_in_old: list,
    components_only_in_new: list,
    analysis_warnings: list,
    demangle_lookup: dict,
    link_map_old: Optional[LinkMapData] = None,   # new
    link_map_new: Optional[LinkMapData] = None,   # new
) -> dict:
```

Both new parameters default to `None` so all existing call sites (including tests) continue to work without modification.

Two new private functions:

**`_enrich_functions_with_linkmap(functions, link_map_primary: LinkMapData, link_map_fallback: Optional[LinkMapData] = None) -> None`**

Mutates function entries in-place (same pattern as the existing demangling mutation). Works on both flat lists (list mode) and bucketed dicts (diff mode):
- List mode: iterates `functions` as a `List[dict]` directly
- Diff mode: iterates each bucket in `functions` dict (`added`, `removed`, `increased`, `decreased`, `unchanged`)

For each entry, looks up `entry["mangled_name"]` in `link_map_primary.symbols` first. If not found and `link_map_fallback` is provided, tries `link_map_fallback.symbols`. If found in either, sets `entry["library"]` and `entry["source_file"]`. If not found in either, entry is unchanged — no null fields added.

This two-source lookup handles the diff-mode asymmetry: `removed` bucket entries exist only in the old binary. In diff mode, `link_map_new` is passed as primary and `link_map_old` as fallback, so removed symbols are still attributed via the old link map.

**Integration point in `build_single_archive_report`:**

```python
# existing: apply demangled names in-place
for entry in analysis["functions"]:
    entry["demangled_name"] = demangle_lookup.get(entry["mangled_name"], entry["mangled_name"])

# new: apply link map attribution in-place (after demangling)
if link_map is not None:
    _enrich_functions_with_linkmap(analysis["functions"], link_map)
    analysis["libraries"] = _build_libraries_block_list(link_map)
```

**Integration point in `build_report`** (inside the `component_results` loop, after the existing demangling step):

```python
# new: apply link map attribution per bucket (after demangling)
if link_map_old is not None or link_map_new is not None:
    primary = link_map_new if link_map_new is not None else link_map_old
    fallback = link_map_old if link_map_new is not None else None
    _enrich_functions_with_linkmap(analysis["functions"], primary, fallback)
    if link_map_old is not None and link_map_new is not None:
        analysis["libraries"] = _build_libraries_block_diff(link_map_old, link_map_new)
```

**`_build_libraries_block_list(link_map: LinkMapData) -> dict`**

Produces a dict of library entries sorted by `total_bytes` descending. `percent_of_text` is computed as `library.text_bytes / link_map.total_text_bytes`. Imports and uses `fmt_percent_of_text` from `analysis.py`.

**`_build_libraries_block_diff(old: LinkMapData, new: LinkMapData) -> dict`**

Produces a dict of library entries over the union of all library names in both link maps. Sorted by `abs(diff_bytes)` descending. Uses the existing `fmt_percent` helper (imported from `analysis.py`) for `diff_percent`. When `old_bytes == 0` (library is new), `diff_percent` is `"N/A"` — consistent with how `fmt_percent` handles a zero denominator in the existing codebase.

### Unchanged: `analysis.py`

No modifications. Link map attribution is entirely a report-layer concern.

## Report Output Shape

`libraries` appears inside each component entry in `components`, alongside the existing `segments`, `sections`, `categories`, and `functions` fields. It is per-component, not top-level.

### List mode — full component shape (abbreviated):

```json
"components": {
  "MyApp.app/MyApp": {
    "type": "main_executable",
    "relative_path": "MyApp.app/MyApp",
    "architecture": "arm64",
    "segments": { ... },
    "sections": { ... },
    "categories": { ... },
    "functions": [
      {
        "mangled_name": "_$s7PluginA11SomeFeatureC6doThingyyF",
        "demangled_name": "PluginA.SomeFeature.doThing()",
        "bytes": 4096,
        "library": "PluginA",
        "source_file": "SomeFeature.o"
      }
    ],
    "totals": { ... },
    "libraries": { ... }
  }
}
```

`library` and `source_file` are omitted (not null) from individual function entries when the symbol is not found in the link map.

### List mode — `libraries` block (inside a component):

```json
"libraries": {
  "PluginA": {
    "bytes": 610000,
    "text_bytes": 524288,
    "symbol_count": 142,
    "percent_of_text": "5.3%"
  },
  "PluginB": {
    "bytes": 270000,
    "text_bytes": 262144,
    "symbol_count": 87,
    "percent_of_text": "2.6%"
  },
  "Linker Synthesized": {
    "bytes": 8192,
    "text_bytes": 8192,
    "symbol_count": 12,
    "percent_of_text": "0.1%"
  }
}
```

`bytes` = all symbols attributed to this library across all sections (authoritative total).
`text_bytes` = symbols in `__TEXT __text` only.
`percent_of_text` = `text_bytes / total_text_bytes` from the link map.
Sorted by `bytes` descending. `object_files` is internal to `LinkMapData` and is not emitted.

### Diff mode — `libraries` block:

```json
"libraries": {
  "PluginA": {
    "old_bytes": 524288,
    "new_bytes": 573440,
    "diff_bytes": 49152,
    "diff_percent": "+9.4%",
    "old_symbol_count": 142,
    "new_symbol_count": 156
  },
  "PluginB": {
    "old_bytes": 262144,
    "new_bytes": 258048,
    "diff_bytes": -4096,
    "diff_percent": "-1.6%",
    "old_symbol_count": 87,
    "new_symbol_count": 84
  },
  "NewLibrary": {
    "old_bytes": 0,
    "new_bytes": 131072,
    "diff_bytes": 131072,
    "diff_percent": "N/A",
    "old_symbol_count": 0,
    "new_symbol_count": 45
  }
}
```

`diff_percent` format: `"+X.X%"` (explicit `+` prefix for positive values), `"-X.X%"` for negative, `"N/A"` when `old_bytes == 0`. Decimal precision: one decimal place. This matches the `fmt_percent` convention already used throughout the codebase.
Sorted by `abs(diff_bytes)` descending.

## Testing

All tests use in-memory fixtures — no real link map files or binaries required.

### `test_linkmap.py` (new)

- Parse a minimal in-memory link map string covering all three sections
- Verify `symbols` and `libraries` lookups are built correctly
- Verify `in_text` classification: symbol in a `__TEXT __text` address range → `True`; symbol outside → `False`
- Test each name extraction rule: linker synthesized, `.build` path, `.framework` path, fallback
- Verify symbol name collision: second occurrence ignored, warning appended
- Verify symbol name with spaces (ObjC method): parsed correctly
- Malformed / missing sections: warnings emitted, partial results returned, no crash
- Missing file path: `parse_link_map` returns `None`, warning appended

### `test_report.py` (additions)

- With `LinkMapData`: function entries gain `library`/`source_file` fields
- Symbol not in link map → no `library`/`source_file` field on that entry (no null pollution)
- `libraries` block appears in list mode output, sorted by `bytes` descending
- `percent_of_text` uses `text_bytes`, not `bytes`
- `object_files` does not appear in report output
- Diff mode with both link maps → correct per-library diffs, sorted by `abs(diff_bytes)`
- Diff mode `diff_percent` is `"N/A"` for libraries where `old_bytes == 0`
- Diff mode with only one link map → `_enrich_functions_with_linkmap` runs, no `libraries` block, no crash

### `test_main.py` (additions)

- `--link-map` wires through to list mode correctly
- `--old-link-map` / `--new-link-map` wire through to diff mode correctly
- `--link-map /nonexistent/path` → `sys.exit()` with error message before analysis runs
- `--old-link-map` / `--new-link-map` with nonexistent path → same fatal behavior

## README Updates

- Add `LD_MAP_FILE` to the xcodebuild archive command in "Building an XCArchive for Analysis"
- Add a section explaining link map support: what it enables, how to provide it, what the new output fields mean
- Document the cmpcodesize approximation vs link map authoritative sizes — clarify which fields use which source and that minor differences between the two are expected and normal
