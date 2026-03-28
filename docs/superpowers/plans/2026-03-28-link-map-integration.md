# Link Map Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional link map ingestion to `oneapp-size-analysis` so each function entry is attributed to its originating library, and a per-component `libraries` aggregation block is added to the report.

**Architecture:** A new `linkmap.py` module parses the linker link map into `LinkMapData` (symbol→library lookup + library aggregations). `report.py` gains two new enrichment helpers that mutate analysis dicts in-place after the existing demangling pass. `main.py` adds three optional CLI flags and threads `LinkMapData` into the report builders. All existing behavior is unchanged when no link map is provided.

**Tech Stack:** Python 3.9+, `dataclasses`, `re`, `pytest`. No new dependencies.

---

## File Map

| Action | Path |
|---|---|
| **Create** | `oneapp_size_analysis/oneapp_size_analysis/linkmap.py` |
| **Modify** | `oneapp_size_analysis/oneapp_size_analysis/report.py` |
| **Modify** | `oneapp_size_analysis/oneapp_size_analysis/main.py` |
| **Create** | `oneapp_size_analysis/tests/test_linkmap.py` |
| **Modify** | `oneapp_size_analysis/tests/test_report.py` |
| **Modify** | `README.md` |

All test commands run from `oneapp_size_analysis/` directory.

---

## Task 1: `linkmap.py` — Dataclasses, library name extraction, object files parsing

**Files:**
- Create: `oneapp_size_analysis/oneapp_size_analysis/linkmap.py`
- Create: `oneapp_size_analysis/tests/test_linkmap.py`

### Step 1.1 — Write the failing tests

Create `oneapp_size_analysis/tests/test_linkmap.py`:

```python
# oneapp_size_analysis/tests/test_linkmap.py
import pytest
from oneapp_size_analysis.linkmap import (
    _extract_library_name,
    _parse_object_files,
    LinkMapData,
    LibraryEntry,
    SymbolEntry,
    parse_link_map,
)


# ── _extract_library_name ─────────────────────────────────────────────────────

def test_extract_library_linker_synthesized():
    assert _extract_library_name(0, "linker synthesized") == "Linker Synthesized"

def test_extract_library_build_dir():
    path = "/DerivedData/App/Build/.../PluginA.build/Objects-normal/arm64/Feature.o"
    assert _extract_library_name(1, path) == "PluginA"

def test_extract_library_framework():
    path = "/DerivedData/App/Build/.../MySDK.framework/MySDK"
    assert _extract_library_name(1, path) == "MySDK"

def test_extract_library_fallback_stem():
    path = "/some/path/SomeFile.o"
    assert _extract_library_name(1, path) == "SomeFile"

def test_extract_library_build_takes_priority_over_framework():
    # If a path somehow has both, .build wins (it comes first in priority)
    path = "/path/PluginA.build/SomeSDK.framework/file.o"
    assert _extract_library_name(1, path) == "PluginA"


# ── _parse_object_files ───────────────────────────────────────────────────────

def test_parse_object_files_basic():
    lines = [
        "[  0] linker synthesized",
        "[  1] /path/to/PluginA.build/Objects-normal/arm64/Feature.o",
        "[  2] /path/to/PluginB.build/Objects-normal/arm64/Other.o",
    ]
    result = _parse_object_files(lines)
    assert result[0][1] == "Linker Synthesized"
    assert result[0][2] == "linker synthesized"
    assert result[1][1] == "PluginA"
    assert result[1][2] == "Feature.o"
    assert result[2][1] == "PluginB"
    assert result[2][2] == "Other.o"

def test_parse_object_files_skips_comment_lines():
    lines = [
        "# Object files:",
        "[  0] linker synthesized",
    ]
    result = _parse_object_files(lines)
    assert 0 in result
    assert len(result) == 1

def test_parse_object_files_large_index():
    lines = ["[ 42] /path/to/PluginC.build/arm64/C.o"]
    result = _parse_object_files(lines)
    assert 42 in result
    assert result[42][1] == "PluginC"
```

- [ ] **Step 1.2 — Run to confirm failure**

```bash
cd oneapp_size_analysis && python -m pytest tests/test_linkmap.py -v 2>&1 | head -20
```

Expected: `ImportError` or `ModuleNotFoundError` for `linkmap`.

- [ ] **Step 1.3 — Create `linkmap.py` with dataclasses and object file parsing**

Create `oneapp_size_analysis/oneapp_size_analysis/linkmap.py`:

```python
# oneapp_size_analysis/oneapp_size_analysis/linkmap.py
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class SymbolEntry:
    library: str
    source_file: str
    linker_bytes: int
    in_text: bool


@dataclass
class LibraryEntry:
    total_bytes: int
    text_bytes: int
    symbol_count: int


@dataclass
class LinkMapData:
    symbols: Dict[str, SymbolEntry]
    libraries: Dict[str, LibraryEntry]
    total_text_bytes: int


def _extract_library_name(file_index: int, path: str) -> str:
    """Extract a human-readable library name from an object file path."""
    if file_index == 0:
        return "Linker Synthesized"
    parts = path.replace("\\", "/").split("/")
    # Rule 2: *.build component
    for part in parts:
        if part.endswith(".build"):
            return part[: -len(".build")]
    # Rule 3: *.framework component
    for part in parts:
        if part.endswith(".framework"):
            return part[: -len(".framework")]
    # Rule 4: filename stem
    filename = parts[-1]
    if filename.endswith(".o"):
        return filename[:-2]
    return filename


def _parse_object_files(lines: List[str]) -> Dict[int, Tuple[str, str, str]]:
    """Parse # Object files: lines. Returns {index: (full_path, library_name, source_file)}."""
    result: Dict[int, Tuple[str, str, str]] = {}
    pattern = re.compile(r"^\[\s*(\d+)\]\s+(.+)$")
    for line in lines:
        m = pattern.match(line.strip())
        if not m:
            continue
        idx = int(m.group(1))
        path = m.group(2).strip()
        library = _extract_library_name(idx, path)
        source_file = os.path.basename(path)
        result[idx] = (path, library, source_file)
    return result


def _parse_sections(lines: List[str]) -> List[Tuple[int, int, str, str]]:
    """Parse # Sections: lines. Returns [(start_addr, size, segment, section_name)]."""
    result: List[Tuple[int, int, str, str]] = []
    pattern = re.compile(r"^0x([0-9A-Fa-f]+)\s+0x([0-9A-Fa-f]+)\s+(\S+)\s+(\S+)$")
    for line in lines:
        m = pattern.match(line.strip())
        if not m:
            continue
        addr = int(m.group(1), 16)
        size = int(m.group(2), 16)
        segment = m.group(3)
        section = m.group(4)
        result.append((addr, size, segment, section))
    return result


def _in_text_section(addr: int, sections: List[Tuple[int, int, str, str]]) -> bool:
    """Return True if addr falls within a __TEXT __text section range."""
    for start, size, segment, section in sections:
        if segment == "__TEXT" and section == "__text":
            if start <= addr < start + size:
                return True
    return False


def _parse_symbols(
    lines: List[str],
    object_files: Dict[int, Tuple[str, str, str]],
    sections: List[Tuple[int, int, str, str]],
    warnings: List[str],
) -> Dict[str, SymbolEntry]:
    """Parse # Symbols: lines into a symbol_name → SymbolEntry dict."""
    result: Dict[str, SymbolEntry] = {}
    pattern = re.compile(r"^0x([0-9A-Fa-f]+)\s+0x([0-9A-Fa-f]+)\s+\[\s*(\d+)\]\s+(.+)$")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        m = pattern.match(stripped)
        if not m:
            continue
        addr = int(m.group(1), 16)
        size = int(m.group(2), 16)
        file_idx = int(m.group(3))
        name = m.group(4).strip()

        if name in result:
            warnings.append(
                f"Warning: duplicate symbol in link map, keeping first: {name}"
            )
            continue

        file_info = object_files.get(
            file_idx, (f"[{file_idx}]", "Unknown", f"[{file_idx}]")
        )
        _, library, source_file = file_info
        result[name] = SymbolEntry(
            library=library,
            source_file=source_file,
            linker_bytes=size,
            in_text=_in_text_section(addr, sections),
        )
    return result


def _build_libraries(
    symbols: Dict[str, SymbolEntry],
) -> Tuple[Dict[str, LibraryEntry], int]:
    """Aggregate symbol entries into per-library totals. Returns (libraries, total_text_bytes)."""
    libs: Dict[str, LibraryEntry] = {}
    total_text = 0
    for sym in symbols.values():
        if sym.library not in libs:
            libs[sym.library] = LibraryEntry(
                total_bytes=0, text_bytes=0, symbol_count=0
            )
        entry = libs[sym.library]
        entry.total_bytes += sym.linker_bytes
        entry.symbol_count += 1
        if sym.in_text:
            entry.text_bytes += sym.linker_bytes
            total_text += sym.linker_bytes
    return libs, total_text


def parse_link_map(path: str, warnings: List[str]) -> Optional[LinkMapData]:
    """Parse a linker link map file. Returns None on failure, appending a warning."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as exc:
        warnings.append(f"Warning: could not read link map {path}: {exc}")
        return None

    obj_lines: List[str] = []
    sect_lines: List[str] = []
    sym_lines: List[str] = []
    current = None

    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "# Object files:":
            current = "obj"
            continue
        elif stripped == "# Sections:":
            current = "sect"
            continue
        elif stripped == "# Symbols:":
            current = "sym"
            continue
        if current == "obj":
            obj_lines.append(line)
        elif current == "sect":
            sect_lines.append(line)
        elif current == "sym":
            sym_lines.append(line)

    object_files = _parse_object_files(obj_lines)
    sections = _parse_sections(sect_lines)
    symbols = _parse_symbols(sym_lines, object_files, sections, warnings)
    libraries, total_text_bytes = _build_libraries(symbols)

    return LinkMapData(
        symbols=symbols,
        libraries=libraries,
        total_text_bytes=total_text_bytes,
    )
```

- [ ] **Step 1.4 — Run tests to confirm pass**

```bash
cd oneapp_size_analysis && python -m pytest tests/test_linkmap.py -v
```

Expected: all `test_extract_library_*` and `test_parse_object_files_*` tests pass.

- [ ] **Step 1.5 — Confirm all existing tests still pass**

```bash
cd oneapp_size_analysis && python -m pytest tests/ -q
```

Expected: 46 passed (no regressions).

- [ ] **Step 1.6 — Commit**

```bash
git add oneapp_size_analysis/oneapp_size_analysis/linkmap.py oneapp_size_analysis/tests/test_linkmap.py
git commit -m "feat: add linkmap.py with dataclasses and object file parsing"
```

---

## Task 2: `linkmap.py` — Sections parsing and in_text classification

**Files:**
- Modify: `oneapp_size_analysis/tests/test_linkmap.py` (add tests)

- [ ] **Step 2.1 — Add failing tests**

Append to `oneapp_size_analysis/tests/test_linkmap.py`:

```python
# ── _parse_sections + _in_text_section ────────────────────────────────────────

from oneapp_size_analysis.linkmap import _parse_sections, _in_text_section

def test_parse_sections_basic():
    lines = [
        "# Address    Size        Segment Section",
        "0x100003C64  0x000037E4  __TEXT  __text",
        "0x100007448  0x00000200  __TEXT  __stubs",
        "0x100010000  0x00001000  __DATA  __data",
    ]
    result = _parse_sections(lines)
    assert len(result) == 3
    assert result[0] == (0x100003C64, 0x000037E4, "__TEXT", "__text")
    assert result[1] == (0x100007448, 0x00000200, "__TEXT", "__stubs")
    assert result[2] == (0x100010000, 0x00001000, "__DATA", "__data")

def test_parse_sections_skips_comment_lines():
    lines = ["# Address    Size", "0x100003C64  0x00001000  __TEXT  __text"]
    result = _parse_sections(lines)
    assert len(result) == 1

def test_in_text_section_true():
    sections = [(0x100003C64, 0x1000, "__TEXT", "__text")]
    assert _in_text_section(0x100003C64, sections) is True
    assert _in_text_section(0x100003C64 + 0x500, sections) is True

def test_in_text_section_boundary_exclusive():
    sections = [(0x100003C64, 0x1000, "__TEXT", "__text")]
    assert _in_text_section(0x100003C64 + 0x1000, sections) is False

def test_in_text_section_false_wrong_section():
    sections = [(0x100003C64, 0x1000, "__TEXT", "__stubs")]
    assert _in_text_section(0x100003C64, sections) is False

def test_in_text_section_false_wrong_segment():
    sections = [(0x100003C64, 0x1000, "__DATA", "__text")]
    assert _in_text_section(0x100003C64, sections) is False

def test_in_text_section_empty():
    assert _in_text_section(0x100003C64, []) is False
```

- [ ] **Step 2.2 — Run to confirm they pass** (they should — the functions are already implemented in Task 1)

```bash
cd oneapp_size_analysis && python -m pytest tests/test_linkmap.py -v -k "sections or in_text"
```

Expected: all pass (implementation was included in Task 1).

- [ ] **Step 2.3 — Commit**

```bash
git add oneapp_size_analysis/tests/test_linkmap.py
git commit -m "test: add sections and in_text classification tests for linkmap.py"
```

---

## Task 3: `linkmap.py` — Symbols parsing and full `parse_link_map`

**Files:**
- Modify: `oneapp_size_analysis/tests/test_linkmap.py` (add tests)

- [ ] **Step 3.1 — Add failing tests**

Append to `oneapp_size_analysis/tests/test_linkmap.py`:

```python
# ── _parse_symbols ────────────────────────────────────────────────────────────

from oneapp_size_analysis.linkmap import _parse_symbols

_SAMPLE_OBJECT_FILES = {
    0: ("linker synthesized", "Linker Synthesized", "linker synthesized"),
    1: ("/path/PluginA.build/arm64/Feature.o", "PluginA", "Feature.o"),
    2: ("/path/PluginB.build/arm64/Other.o", "PluginB", "Other.o"),
}

_SAMPLE_SECTIONS = [
    (0x100003C64, 0x2000, "__TEXT", "__text"),
    (0x100010000, 0x1000, "__DATA", "__data"),
]

def test_parse_symbols_basic():
    lines = [
        "# Address    Size        File  Name",
        "0x100003C64  0x000000AC  [  1] _$sPluginAFeature",
        "0x100010000  0x00000020  [  2] _globalVar",
    ]
    warnings = []
    result = _parse_symbols(lines, _SAMPLE_OBJECT_FILES, _SAMPLE_SECTIONS, warnings)
    assert "_$sPluginAFeature" in result
    sym = result["_$sPluginAFeature"]
    assert sym.library == "PluginA"
    assert sym.source_file == "Feature.o"
    assert sym.linker_bytes == 0xAC
    assert sym.in_text is True  # address 0x100003C64 is in __text range

def test_parse_symbols_not_in_text():
    lines = ["0x100010000  0x00000020  [  2] _globalVar"]
    warnings = []
    result = _parse_symbols(lines, _SAMPLE_OBJECT_FILES, _SAMPLE_SECTIONS, warnings)
    assert result["_globalVar"].in_text is False

def test_parse_symbols_objc_method_name_with_spaces():
    lines = ["0x100003C64  0x00000040  [  1] +[MyClass doThing:withParam:]"]
    warnings = []
    result = _parse_symbols(lines, _SAMPLE_OBJECT_FILES, _SAMPLE_SECTIONS, warnings)
    assert "+[MyClass doThing:withParam:]" in result

def test_parse_symbols_duplicate_first_wins():
    lines = [
        "0x100003C64  0x00000040  [  1] _duplicateSym",
        "0x100003CA4  0x00000020  [  2] _duplicateSym",
    ]
    warnings = []
    result = _parse_symbols(lines, _SAMPLE_OBJECT_FILES, _SAMPLE_SECTIONS, warnings)
    assert result["_duplicateSym"].library == "PluginA"  # first wins
    assert len(warnings) == 1
    assert "duplicate symbol" in warnings[0]

def test_parse_symbols_skips_comment_lines():
    lines = [
        "# Address    Size        File  Name",
        "0x100003C64  0x00000040  [  1] _realSym",
    ]
    warnings = []
    result = _parse_symbols(lines, _SAMPLE_OBJECT_FILES, _SAMPLE_SECTIONS, warnings)
    assert len(result) == 1

def test_parse_symbols_unknown_file_index():
    lines = ["0x100003C64  0x00000040  [ 99] _unknownSym"]
    warnings = []
    result = _parse_symbols(lines, _SAMPLE_OBJECT_FILES, _SAMPLE_SECTIONS, warnings)
    assert "_unknownSym" in result
    assert result["_unknownSym"].library == "Unknown"


# ── parse_link_map (integration) ──────────────────────────────────────────────

import textwrap

_MINIMAL_LINK_MAP = textwrap.dedent("""\
    # Path: /path/to/MyApp
    # Arch: arm64

    # Object files:
    [  0] linker synthesized
    [  1] /path/PluginA.build/arm64/Feature.o
    [  2] /path/PluginB.build/arm64/Other.o

    # Sections:
    # Address    Size        Segment Section
    0x100003C64  0x00002000  __TEXT  __text
    0x100010000  0x00001000  __DATA  __data

    # Symbols:
    # Address    Size        File  Name
    0x100003C64  0x000000AC  [  1] _$sPluginALargeFunc
    0x100003D10  0x00000050  [  1] _$sPluginASmallFunc
    0x100003D60  0x00000030  [  2] _$sPluginBFunc
    0x100010000  0x00000020  [  0] _linkerSynthesizedSym
""")


def test_parse_link_map_symbols(tmp_path):
    p = tmp_path / "linkmap.txt"
    p.write_text(_MINIMAL_LINK_MAP)
    warnings = []
    data = parse_link_map(str(p), warnings)
    assert data is not None
    assert "_$sPluginALargeFunc" in data.symbols
    assert data.symbols["_$sPluginALargeFunc"].library == "PluginA"
    assert data.symbols["_$sPluginALargeFunc"].in_text is True

def test_parse_link_map_libraries_aggregation(tmp_path):
    p = tmp_path / "linkmap.txt"
    p.write_text(_MINIMAL_LINK_MAP)
    warnings = []
    data = parse_link_map(str(p), warnings)
    assert "PluginA" in data.libraries
    assert "PluginB" in data.libraries
    assert "Linker Synthesized" in data.libraries
    plugin_a = data.libraries["PluginA"]
    assert plugin_a.total_bytes == 0xAC + 0x50
    assert plugin_a.text_bytes == 0xAC + 0x50  # both in __text
    assert plugin_a.symbol_count == 2

def test_parse_link_map_total_text_bytes(tmp_path):
    p = tmp_path / "linkmap.txt"
    p.write_text(_MINIMAL_LINK_MAP)
    warnings = []
    data = parse_link_map(str(p), warnings)
    # All __text symbols: 0xAC + 0x50 + 0x30 = 0x12C
    assert data.total_text_bytes == 0xAC + 0x50 + 0x30

def test_parse_link_map_nonexistent_file():
    warnings = []
    result = parse_link_map("/does/not/exist.txt", warnings)
    assert result is None
    assert len(warnings) == 1
    assert "could not read link map" in warnings[0]

def test_parse_link_map_missing_symbols_section(tmp_path):
    content = "# Object files:\n[  0] linker synthesized\n"
    p = tmp_path / "linkmap.txt"
    p.write_text(content)
    warnings = []
    data = parse_link_map(str(p), warnings)
    assert data is not None  # no crash, empty results
    assert len(data.symbols) == 0
    assert len(data.libraries) == 0
```

- [ ] **Step 3.2 — Run to confirm tests pass**

```bash
cd oneapp_size_analysis && python -m pytest tests/test_linkmap.py -v
```

Expected: all tests pass (implementation was included in Task 1).

- [ ] **Step 3.3 — Run full test suite**

```bash
cd oneapp_size_analysis && python -m pytest tests/ -q
```

Expected: 46 + new linkmap tests, all pass.

- [ ] **Step 3.4 — Commit**

```bash
git add oneapp_size_analysis/tests/test_linkmap.py
git commit -m "test: complete test_linkmap.py coverage for symbols and parse_link_map"
```

---

## Task 4: `report.py` — `_enrich_functions_with_linkmap`

**Files:**
- Modify: `oneapp_size_analysis/oneapp_size_analysis/report.py`
- Modify: `oneapp_size_analysis/tests/test_report.py`

- [ ] **Step 4.1 — Add failing tests**

Append to `oneapp_size_analysis/tests/test_report.py`:

```python
# ── _enrich_functions_with_linkmap ────────────────────────────────────────────

from oneapp_size_analysis.linkmap import LinkMapData, LibraryEntry, SymbolEntry
from oneapp_size_analysis.report import _enrich_functions_with_linkmap


def _make_link_map_data(symbols: dict) -> LinkMapData:
    """Build a minimal LinkMapData for testing."""
    libraries = {}
    for sym in symbols.values():
        if sym.library not in libraries:
            libraries[sym.library] = LibraryEntry(0, 0, 0)
        libraries[sym.library].total_bytes += sym.linker_bytes
        libraries[sym.library].symbol_count += 1
        if sym.in_text:
            libraries[sym.library].text_bytes += sym.linker_bytes
    return LinkMapData(symbols=symbols, libraries=libraries, total_text_bytes=1000)


def test_enrich_list_mode_adds_library_and_source():
    functions = [
        {"mangled_name": "_$sPluginAFunc", "bytes": 100},
        {"mangled_name": "_unknown", "bytes": 50},
    ]
    lm = _make_link_map_data({
        "_$sPluginAFunc": SymbolEntry("PluginA", "Feature.o", 100, True),
    })
    _enrich_functions_with_linkmap(functions, lm)
    assert functions[0]["library"] == "PluginA"
    assert functions[0]["source_file"] == "Feature.o"

def test_enrich_list_mode_missing_symbol_no_null():
    functions = [{"mangled_name": "_unknown", "bytes": 50}]
    lm = _make_link_map_data({})
    _enrich_functions_with_linkmap(functions, lm)
    assert "library" not in functions[0]
    assert "source_file" not in functions[0]

def test_enrich_diff_mode_all_buckets():
    functions = {
        "added":     [{"mangled_name": "_$sAdded", "new_bytes": 64}],
        "removed":   [{"mangled_name": "_$sRemoved", "old_bytes": 32}],
        "increased": [{"mangled_name": "_$sGrown", "old_bytes": 100, "new_bytes": 150, "diff_bytes": 50, "diff_percent": "+50.0%"}],
        "decreased": [],
        "unchanged": [{"mangled_name": "_$sStable", "bytes": 80}],
        "totals": {},
    }
    lm = _make_link_map_data({
        "_$sAdded":   SymbolEntry("PluginA", "A.o", 64, True),
        "_$sRemoved": SymbolEntry("PluginB", "B.o", 32, True),
        "_$sGrown":   SymbolEntry("PluginA", "A.o", 150, True),
        "_$sStable":  SymbolEntry("PluginA", "A.o", 80, True),
    })
    _enrich_functions_with_linkmap(functions, lm)
    assert functions["added"][0]["library"] == "PluginA"
    assert functions["removed"][0]["library"] == "PluginB"
    assert functions["increased"][0]["library"] == "PluginA"
    assert functions["unchanged"][0]["library"] == "PluginA"

def test_enrich_diff_mode_fallback_for_removed():
    """Removed symbols exist only in old binary — found via fallback link map."""
    functions = {
        "added": [], "removed": [{"mangled_name": "_$sOldOnly", "old_bytes": 32}],
        "increased": [], "decreased": [], "unchanged": [], "totals": {},
    }
    lm_new = _make_link_map_data({})  # new map doesn't have the removed symbol
    lm_old = _make_link_map_data({
        "_$sOldOnly": SymbolEntry("PluginA", "A.o", 32, True),
    })
    _enrich_functions_with_linkmap(functions, lm_new, lm_old)
    assert functions["removed"][0]["library"] == "PluginA"
```

- [ ] **Step 4.2 — Run to confirm failure**

```bash
cd oneapp_size_analysis && python -m pytest tests/test_report.py -v -k "enrich"
```

Expected: `ImportError` — `_enrich_functions_with_linkmap` doesn't exist yet.

- [ ] **Step 4.3 — Implement `_enrich_functions_with_linkmap` in `report.py`**

Add these imports at the top of `report.py` (after existing imports):

```python
from typing import Union
from oneapp_size_analysis.linkmap import LinkMapData
```

Add this function before `build_report`:

```python
def _enrich_functions_with_linkmap(
    functions: Union[list, dict],
    link_map_primary: LinkMapData,
    link_map_fallback: Optional[LinkMapData] = None,
) -> None:
    """Mutate function entries in-place: add library and source_file from link map.

    Works on both flat lists (list mode) and bucketed dicts (diff mode).
    Symbols not found in either map are left unchanged — no null fields added.
    """
    if isinstance(functions, list):
        entries = functions
    else:
        entries = []
        for bucket in ("added", "removed", "increased", "decreased", "unchanged"):
            entries.extend(functions.get(bucket, []))

    for entry in entries:
        name = entry["mangled_name"]
        sym = link_map_primary.symbols.get(name)
        if sym is None and link_map_fallback is not None:
            sym = link_map_fallback.symbols.get(name)
        if sym is not None:
            entry["library"] = sym.library
            entry["source_file"] = sym.source_file
```

- [ ] **Step 4.4 — Run tests to confirm pass**

```bash
cd oneapp_size_analysis && python -m pytest tests/test_report.py -v -k "enrich"
```

Expected: all 4 new enrich tests pass.

- [ ] **Step 4.5 — Run full suite**

```bash
cd oneapp_size_analysis && python -m pytest tests/ -q
```

Expected: all pass, no regressions.

- [ ] **Step 4.6 — Commit**

```bash
git add oneapp_size_analysis/oneapp_size_analysis/report.py oneapp_size_analysis/tests/test_report.py
git commit -m "feat: add _enrich_functions_with_linkmap to report.py"
```

---

## Task 5: `report.py` — Library block builders

**Files:**
- Modify: `oneapp_size_analysis/oneapp_size_analysis/report.py`
- Modify: `oneapp_size_analysis/tests/test_report.py`

- [ ] **Step 5.1 — Add failing tests**

Append to `oneapp_size_analysis/tests/test_report.py`:

```python
# ── _build_libraries_block_list ───────────────────────────────────────────────

from oneapp_size_analysis.report import _build_libraries_block_list, _build_libraries_block_diff


def _make_link_map_with_libs() -> LinkMapData:
    symbols = {
        "_$sPluginALarge": SymbolEntry("PluginA", "A.o", 1000, True),
        "_$sPluginASmall": SymbolEntry("PluginA", "A.o", 200, True),
        "_$sPluginBFunc":  SymbolEntry("PluginB", "B.o", 500, True),
        "_globalVar":      SymbolEntry("PluginA", "A.o", 300, False),  # not in __text
    }
    libs = {
        "PluginA": LibraryEntry(total_bytes=1500, text_bytes=1200, symbol_count=3),
        "PluginB": LibraryEntry(total_bytes=500, text_bytes=500, symbol_count=1),
    }
    return LinkMapData(symbols=symbols, libraries=libs, total_text_bytes=1700)


def test_build_libraries_block_list_keys():
    lm = _make_link_map_with_libs()
    result = _build_libraries_block_list(lm)
    assert "PluginA" in result
    assert "PluginB" in result

def test_build_libraries_block_list_fields():
    lm = _make_link_map_with_libs()
    result = _build_libraries_block_list(lm)
    plugin_a = result["PluginA"]
    assert plugin_a["bytes"] == 1500
    assert plugin_a["text_bytes"] == 1200
    assert plugin_a["symbol_count"] == 3
    assert plugin_a["percent_of_text"] == "70.6%"  # 1200/1700

def test_build_libraries_block_list_sorted_by_bytes_desc():
    lm = _make_link_map_with_libs()
    result = _build_libraries_block_list(lm)
    keys = list(result.keys())
    assert keys[0] == "PluginA"  # 1500 bytes > 500
    assert keys[1] == "PluginB"

def test_build_libraries_block_list_no_object_files_key():
    lm = _make_link_map_with_libs()
    result = _build_libraries_block_list(lm)
    for entry in result.values():
        assert "object_files" not in entry


# ── _build_libraries_block_diff ───────────────────────────────────────────────

def _make_old_link_map() -> LinkMapData:
    return LinkMapData(
        symbols={},
        libraries={
            "PluginA": LibraryEntry(total_bytes=1000, text_bytes=1000, symbol_count=10),
            "PluginB": LibraryEntry(total_bytes=500, text_bytes=500, symbol_count=5),
        },
        total_text_bytes=1500,
    )

def _make_new_link_map() -> LinkMapData:
    return LinkMapData(
        symbols={},
        libraries={
            "PluginA": LibraryEntry(total_bytes=1200, text_bytes=1200, symbol_count=12),
            "NewPlugin": LibraryEntry(total_bytes=800, text_bytes=800, symbol_count=8),
        },
        total_text_bytes=2000,
    )

def test_build_libraries_block_diff_union_of_libs():
    result = _build_libraries_block_diff(_make_old_link_map(), _make_new_link_map())
    assert "PluginA" in result
    assert "PluginB" in result   # only in old
    assert "NewPlugin" in result  # only in new

def test_build_libraries_block_diff_fields_existing():
    result = _build_libraries_block_diff(_make_old_link_map(), _make_new_link_map())
    plugin_a = result["PluginA"]
    assert plugin_a["old_bytes"] == 1000
    assert plugin_a["new_bytes"] == 1200
    assert plugin_a["diff_bytes"] == 200
    assert plugin_a["diff_percent"] == "+20.0%"
    assert plugin_a["old_symbol_count"] == 10
    assert plugin_a["new_symbol_count"] == 12

def test_build_libraries_block_diff_new_library_na_percent():
    result = _build_libraries_block_diff(_make_old_link_map(), _make_new_link_map())
    new_plugin = result["NewPlugin"]
    assert new_plugin["old_bytes"] == 0
    assert new_plugin["new_bytes"] == 800
    assert new_plugin["diff_percent"] == "N/A"
    assert new_plugin["old_symbol_count"] == 0
    assert new_plugin["new_symbol_count"] == 8

def test_build_libraries_block_diff_sorted_by_abs_diff_desc():
    result = _build_libraries_block_diff(_make_old_link_map(), _make_new_link_map())
    keys = list(result.keys())
    # NewPlugin diff=800, PluginA diff=200, PluginB diff=-500 (abs=500)
    # Sorted: NewPlugin(800) > PluginB(500) > PluginA(200)
    assert keys[0] == "NewPlugin"
    assert keys[1] == "PluginB"
    assert keys[2] == "PluginA"
```

- [ ] **Step 5.2 — Run to confirm failure**

```bash
cd oneapp_size_analysis && python -m pytest tests/test_report.py -v -k "libraries_block"
```

Expected: `ImportError` — functions don't exist yet.

- [ ] **Step 5.3 — Implement `_build_libraries_block_list` and `_build_libraries_block_diff` in `report.py`**

Add this import at the top of `report.py` (alongside existing imports):

```python
from oneapp_size_analysis.analysis import fmt_percent, fmt_percent_of_text
```

Add these two functions after `_enrich_functions_with_linkmap`:

```python
def _build_libraries_block_list(link_map: LinkMapData) -> Dict[str, Any]:
    """Build a per-library size summary dict for list mode, sorted by bytes descending."""
    sorted_libs = sorted(
        link_map.libraries.items(),
        key=lambda item: -item[1].total_bytes,
    )
    return {
        name: {
            "bytes": entry.total_bytes,
            "text_bytes": entry.text_bytes,
            "symbol_count": entry.symbol_count,
            "percent_of_text": fmt_percent_of_text(
                entry.text_bytes, link_map.total_text_bytes
            ),
        }
        for name, entry in sorted_libs
    }


def _build_libraries_block_diff(
    old: LinkMapData, new: LinkMapData
) -> Dict[str, Any]:
    """Build a per-library size diff dict, sorted by abs(diff_bytes) descending."""
    all_libs = set(old.libraries.keys()) | set(new.libraries.keys())
    entries = []
    for name in all_libs:
        old_entry = old.libraries.get(name)
        new_entry = new.libraries.get(name)
        old_bytes = old_entry.total_bytes if old_entry else 0
        new_bytes = new_entry.total_bytes if new_entry else 0
        entries.append((name, {
            "old_bytes": old_bytes,
            "new_bytes": new_bytes,
            "diff_bytes": new_bytes - old_bytes,
            "diff_percent": fmt_percent(new_bytes, old_bytes),
            "old_symbol_count": old_entry.symbol_count if old_entry else 0,
            "new_symbol_count": new_entry.symbol_count if new_entry else 0,
        }))
    entries.sort(key=lambda item: -abs(item[1]["diff_bytes"]))
    return dict(entries)
```

- [ ] **Step 5.4 — Run tests to confirm pass**

```bash
cd oneapp_size_analysis && python -m pytest tests/test_report.py -v -k "libraries_block"
```

Expected: all library block tests pass.

- [ ] **Step 5.5 — Run full suite**

```bash
cd oneapp_size_analysis && python -m pytest tests/ -q
```

Expected: all pass.

- [ ] **Step 5.6 — Commit**

```bash
git add oneapp_size_analysis/oneapp_size_analysis/report.py oneapp_size_analysis/tests/test_report.py
git commit -m "feat: add _build_libraries_block_list and _build_libraries_block_diff to report.py"
```

---

## Task 6: `report.py` — Wire link map into `build_single_archive_report` and `build_report`

**Files:**
- Modify: `oneapp_size_analysis/oneapp_size_analysis/report.py`
- Modify: `oneapp_size_analysis/tests/test_report.py`

- [ ] **Step 6.1 — Add failing tests**

Append to `oneapp_size_analysis/tests/test_report.py`:

```python
# ── build_single_archive_report with link map ─────────────────────────────────

from oneapp_size_analysis.report import build_single_archive_report


def _make_list_component_analysis() -> dict:
    return {
        "architecture": "arm64",
        "segments": {"__TEXT": {"bytes": 10000}},
        "sections": {"__text": {"bytes": 8000}},
        "categories": {"Swift Function": {"bytes": 5000, "percent_of_text": "62.5%"}},
        "functions": [
            {"mangled_name": "_$sPluginAFunc", "bytes": 512},
            {"mangled_name": "_unknown", "bytes": 128},
        ],
        "totals": {"function_count": 2, "total_function_bytes": 640},
    }


def test_build_single_archive_report_with_link_map():
    lm = _make_link_map_data({
        "_$sPluginAFunc": SymbolEntry("PluginA", "A.o", 512, True),
    })
    lm.libraries["PluginA"] = LibraryEntry(total_bytes=512, text_bytes=512, symbol_count=1)
    lm.total_text_bytes = 512
    component_results = {"MyApp.app/MyApp": ("main_executable", _make_list_component_analysis())}
    report = build_single_archive_report(
        metadata={"app_name": "MyApp", "generated_at": "t", "archive": "/a"},
        component_results=component_results,
        analysis_warnings=[],
        demangle_lookup={},
        link_map=lm,
    )
    comp = report["components"]["MyApp.app/MyApp"]
    assert comp["functions"][0]["library"] == "PluginA"
    assert "library" not in comp["functions"][1]  # _unknown not in link map
    assert "libraries" in comp
    assert "PluginA" in comp["libraries"]

def test_build_single_archive_report_without_link_map():
    """Existing behavior unchanged when link_map=None."""
    component_results = {"MyApp.app/MyApp": ("main_executable", _make_list_component_analysis())}
    report = build_single_archive_report(
        metadata={"app_name": "MyApp", "generated_at": "t", "archive": "/a"},
        component_results=component_results,
        analysis_warnings=[],
        demangle_lookup={},
    )
    comp = report["components"]["MyApp.app/MyApp"]
    assert "library" not in comp["functions"][0]
    assert "libraries" not in comp


# ── build_report with link maps ───────────────────────────────────────────────

from oneapp_size_analysis.report import build_report as _build_report_fn


def test_build_report_with_both_link_maps():
    lm_old = _make_link_map_with_libs()
    lm_new = LinkMapData(
        symbols={"_$sAddedFunc": SymbolEntry("PluginA", "A.o", 64, True)},
        libraries={"PluginA": LibraryEntry(total_bytes=1600, text_bytes=1300, symbol_count=4)},
        total_text_bytes=1800,
    )
    analysis = _make_component_analysis()
    component_results = {"MyApp.app/MyApp": ("main_executable", analysis)}
    report = _build_report_fn(
        metadata={"app_name": "MyApp", "old_archive": "/old", "new_archive": "/new", "generated_at": "t"},
        component_results=component_results,
        components_only_in_old=[],
        components_only_in_new=[],
        analysis_warnings=[],
        demangle_lookup={},
        link_map_old=lm_old,
        link_map_new=lm_new,
    )
    comp = report["components"]["MyApp.app/MyApp"]
    assert "libraries" in comp
    assert "PluginA" in comp["libraries"]

def test_build_report_with_one_link_map_no_libraries_block():
    """With only new link map, enrichment runs but no libraries block emitted."""
    lm_new = _make_link_map_with_libs()
    analysis = _make_component_analysis()
    component_results = {"MyApp.app/MyApp": ("main_executable", analysis)}
    report = _build_report_fn(
        metadata={"app_name": "MyApp", "old_archive": "/old", "new_archive": "/new", "generated_at": "t"},
        component_results=component_results,
        components_only_in_old=[],
        components_only_in_new=[],
        analysis_warnings=[],
        demangle_lookup={},
        link_map_new=lm_new,
    )
    comp = report["components"]["MyApp.app/MyApp"]
    assert "libraries" not in comp

def test_build_report_without_link_maps():
    """Existing behavior: no link map args → no libraries block, no enrichment."""
    component_results = {"MyApp.app/MyApp": ("main_executable", _make_component_analysis())}
    report = _build_report_fn(
        metadata={"app_name": "MyApp", "old_archive": "/old", "new_archive": "/new", "generated_at": "t"},
        component_results=component_results,
        components_only_in_old=[],
        components_only_in_new=[],
        analysis_warnings=[],
        demangle_lookup={},
    )
    comp = report["components"]["MyApp.app/MyApp"]
    assert "libraries" not in comp
```

- [ ] **Step 6.2 — Run to confirm failure**

```bash
cd oneapp_size_analysis && python -m pytest tests/test_report.py -v -k "link_map or link_maps"
```

Expected: failures — `build_single_archive_report` doesn't accept `link_map` yet.

- [ ] **Step 6.3 — Update `build_single_archive_report` in `report.py`**

Replace the existing `build_single_archive_report` function:

```python
def build_single_archive_report(
    metadata: Dict[str, Any],
    component_results: Dict[str, Tuple[str, Optional[Dict]]],
    analysis_warnings: List[str],
    demangle_lookup: Dict[str, str],
    link_map: Optional[LinkMapData] = None,
) -> Dict[str, Any]:
    """Assemble the JSON report dict for single-archive (list) mode."""
    components_out: Dict[str, Any] = {}

    for rel_path, (comp_type, analysis) in component_results.items():
        if analysis is None:
            continue
        # Apply demangled names to the flat functions list in-place
        for entry in analysis.get("functions", []):
            mangled = entry["mangled_name"]
            entry["demangled_name"] = demangle_lookup.get(mangled, mangled)
        # Apply link map attribution in-place (after demangling)
        if link_map is not None:
            _enrich_functions_with_linkmap(analysis["functions"], link_map)
            analysis["libraries"] = _build_libraries_block_list(link_map)
        components_out[rel_path] = {
            "type": comp_type,
            "relative_path": rel_path,
            **analysis,
        }

    return {
        "metadata": metadata,
        "components": components_out,
        "analysis_warnings": analysis_warnings,
    }
```

- [ ] **Step 6.4 — Update `build_report` in `report.py`**

Replace the existing `build_report` function:

```python
def build_report(
    metadata: Dict[str, Any],
    component_results: Dict[str, Tuple[str, Optional[Dict]]],
    components_only_in_old: List[str],
    components_only_in_new: List[str],
    analysis_warnings: List[str],
    demangle_lookup: Dict[str, str],
    link_map_old: Optional[LinkMapData] = None,
    link_map_new: Optional[LinkMapData] = None,
) -> Dict[str, Any]:
    """Assemble the final JSON report dict."""
    components_out: Dict[str, Any] = {}

    for rel_path, (comp_type, analysis) in component_results.items():
        if analysis is None:
            continue
        # Apply demangled names in-place
        apply_demangled_names(analysis, demangle_lookup)
        # Apply link map attribution in-place (after demangling)
        if link_map_old is not None or link_map_new is not None:
            primary = link_map_new if link_map_new is not None else link_map_old
            fallback = link_map_old if link_map_new is not None else None
            _enrich_functions_with_linkmap(analysis["functions"], primary, fallback)
            if link_map_old is not None and link_map_new is not None:
                analysis["libraries"] = _build_libraries_block_diff(
                    link_map_old, link_map_new
                )
        components_out[rel_path] = {
            "type": comp_type,
            "relative_path": rel_path,
            **analysis,
        }

    return {
        "metadata": metadata,
        "components": components_out,
        "components_only_in_old": components_only_in_old,
        "components_only_in_new": components_only_in_new,
        "analysis_warnings": analysis_warnings,
    }
```

- [ ] **Step 6.5 — Run tests to confirm pass**

```bash
cd oneapp_size_analysis && python -m pytest tests/test_report.py -v
```

Expected: all tests pass.

- [ ] **Step 6.6 — Run full suite**

```bash
cd oneapp_size_analysis && python -m pytest tests/ -q
```

Expected: all pass, no regressions.

- [ ] **Step 6.7 — Commit**

```bash
git add oneapp_size_analysis/oneapp_size_analysis/report.py oneapp_size_analysis/tests/test_report.py
git commit -m "feat: wire link map into build_report and build_single_archive_report"
```

---

## Task 7: `main.py` — CLI flags and wiring

**Files:**
- Modify: `oneapp_size_analysis/oneapp_size_analysis/main.py`
- Create: `oneapp_size_analysis/tests/test_main.py`

- [ ] **Step 7.1 — Write failing tests**

Create `oneapp_size_analysis/tests/test_main.py`:

```python
# oneapp_size_analysis/tests/test_main.py
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from oneapp_size_analysis.main import main


def _make_minimal_archive(tmp_path: Path, app_name: str = "MyApp") -> Path:
    """Create a minimal xcarchive directory structure for testing."""
    archive = tmp_path / f"{app_name}.xcarchive"
    app = archive / "Products" / "Applications" / f"{app_name}.app"
    app.mkdir(parents=True)
    plist = app / "Info.plist"
    plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>{app_name}</string>
</dict></plist>
""")
    binary = app / app_name
    binary.write_bytes(b"\x00")
    return archive


def _make_minimal_link_map(tmp_path: Path, name: str = "linkmap.txt") -> Path:
    """Write a minimal but valid link map file."""
    content = (
        "# Object files:\n"
        "[  0] linker synthesized\n"
        "# Sections:\n"
        "# Symbols:\n"
    )
    p = tmp_path / name
    p.write_text(content)
    return p


@patch("oneapp_size_analysis.main._check_dependencies")
@patch("oneapp_size_analysis.main.discover_components", return_value=[])
@patch("oneapp_size_analysis.main.validate_app_names", return_value={"app_name": "MyApp"})
@patch("oneapp_size_analysis.main.demangle_symbols", return_value={})
@patch("oneapp_size_analysis.main.build_single_archive_report", return_value={"metadata": {}, "components": {}, "analysis_warnings": []})
@patch("oneapp_size_analysis.main.write_report")
def test_list_mode_link_map_flag_accepted(
    mock_write, mock_report, mock_demangle, mock_validate, mock_discover, mock_check, tmp_path
):
    archive = _make_minimal_archive(tmp_path)
    lm = _make_minimal_link_map(tmp_path)
    sys.argv = ["oneapp-size-analysis", str(archive), "--link-map", str(lm)]
    main()
    # Verify build_single_archive_report was called with a link_map kwarg
    call_kwargs = mock_report.call_args.kwargs
    assert "link_map" in call_kwargs


@patch("oneapp_size_analysis.main._check_dependencies")
def test_list_mode_link_map_nonexistent_exits(mock_check, tmp_path):
    archive = _make_minimal_archive(tmp_path)
    sys.argv = ["oneapp-size-analysis", str(archive), "--link-map", "/does/not/exist.txt"]
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code != 0


@patch("oneapp_size_analysis.main._check_dependencies")
@patch("oneapp_size_analysis.main.discover_components", return_value=[])
@patch("oneapp_size_analysis.main.validate_app_names", return_value={"app_name": "MyApp"})
@patch("oneapp_size_analysis.main.demangle_symbols", return_value={})
@patch("oneapp_size_analysis.main.build_report", return_value={"metadata": {}, "components": {}, "components_only_in_old": [], "components_only_in_new": [], "analysis_warnings": []})
@patch("oneapp_size_analysis.main.write_report")
def test_diff_mode_link_map_flags_accepted(
    mock_write, mock_report, mock_demangle, mock_validate, mock_discover, mock_check, tmp_path
):
    old_archive = _make_minimal_archive(tmp_path / "old", "OldApp")
    new_archive = _make_minimal_archive(tmp_path / "new", "NewApp")
    old_lm = _make_minimal_link_map(tmp_path, "old-linkmap.txt")
    new_lm = _make_minimal_link_map(tmp_path, "new-linkmap.txt")
    sys.argv = [
        "oneapp-size-analysis",
        str(old_archive), str(new_archive),
        "--old-link-map", str(old_lm),
        "--new-link-map", str(new_lm),
    ]
    main()
    call_kwargs = mock_report.call_args.kwargs
    assert "link_map_old" in call_kwargs
    assert "link_map_new" in call_kwargs


@patch("oneapp_size_analysis.main._check_dependencies")
def test_diff_mode_old_link_map_nonexistent_exits(mock_check, tmp_path):
    old_archive = _make_minimal_archive(tmp_path / "old", "OldApp")
    new_archive = _make_minimal_archive(tmp_path / "new", "NewApp")
    sys.argv = [
        "oneapp-size-analysis",
        str(old_archive), str(new_archive),
        "--old-link-map", "/does/not/exist.txt",
    ]
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code != 0


@patch("oneapp_size_analysis.main._check_dependencies")
def test_diff_mode_new_link_map_nonexistent_exits(mock_check, tmp_path):
    old_archive = _make_minimal_archive(tmp_path / "old", "OldApp")
    new_archive = _make_minimal_archive(tmp_path / "new", "NewApp")
    new_lm = _make_minimal_link_map(tmp_path, "new-linkmap.txt")
    sys.argv = [
        "oneapp-size-analysis",
        str(old_archive), str(new_archive),
        "--new-link-map", "/does/not/exist.txt",
    ]
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code != 0
```

- [ ] **Step 7.2 — Run to confirm failure**

```bash
cd oneapp_size_analysis && python -m pytest tests/test_main.py -v
```

Expected: failures — `--link-map` flag doesn't exist yet.

- [ ] **Step 7.3 — Update `main.py`**

Add new imports at the top (after existing imports):

```python
from oneapp_size_analysis.linkmap import parse_link_map, LinkMapData
```

Add these three `add_argument` calls inside `main()`, after the existing `--output` argument:

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

Replace the body of `_run_list_mode` to add link map handling **before** the report call. Locate the `report = build_single_archive_report(...)` call and replace the surrounding block:

```python
def _run_list_mode(archive_path: Path, args: argparse.Namespace) -> None:
    warnings: List[str] = []

    try:
        components = discover_components(archive_path, warnings=warnings)
    except ArchiveError as e:
        sys.exit(f"Error in {archive_path.name}: {e}")

    app_name_meta = validate_app_names(archive_path, archive_path, warnings)
    app_name = app_name_meta.get("app_name", "app")

    component_results: Dict[str, Tuple[str, Optional[dict]]] = {}
    total = len(components)
    for i, comp in enumerate(components, 1):
        print(f"  [{i}/{total}] Listing {comp.relative_path} ...", file=sys.stderr)
        analysis = list_component(str(comp.absolute_path), warnings)
        component_results[comp.relative_path] = (comp.component_type, analysis)

    print("Demangling symbols ...", file=sys.stderr)
    all_names = _collect_all_mangled_names_list(component_results)
    demangle_lookup = demangle_symbols(all_names)

    # Link map (optional)
    link_map: Optional[LinkMapData] = None
    if args.link_map:
        if not Path(args.link_map).is_file():
            sys.exit(f"Error: link map not found: {args.link_map}")
        link_map = parse_link_map(args.link_map, warnings)

    now = datetime.datetime.now()
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    metadata = {
        "generated_at": now.isoformat(),
        "archive": str(archive_path),
        "app_name": app_name,
    }

    report = build_single_archive_report(
        metadata=metadata,
        component_results=component_results,
        analysis_warnings=warnings,
        demangle_lookup=demangle_lookup,
        link_map=link_map,
    )

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path("analysis-reports") / f"{app_name}-size-list-{timestamp}.json"

    write_report(report, output_path)

    listed = sum(1 for _, (_, a) in component_results.items() if a is not None)
    print(f"Report written to: {output_path}", file=sys.stderr)
    print(f"Components listed: {listed}/{total}", file=sys.stderr)
    if warnings:
        print(f"Warnings: {len(warnings)} (see 'analysis_warnings' in report)", file=sys.stderr)
```

Replace the body of `_run_diff_mode` similarly:

```python
def _run_diff_mode(old_path: Path, new_path: Path, args: argparse.Namespace) -> None:
    warnings: List[str] = []

    try:
        old_components = discover_components(old_path, warnings=warnings)
    except ArchiveError as e:
        sys.exit(f"Error in {old_path.name}: {e}")

    try:
        new_components = discover_components(new_path, warnings=warnings)
    except ArchiveError as e:
        sys.exit(f"Error in {new_path.name}: {e}")

    app_name_meta = validate_app_names(old_path, new_path, warnings)
    app_name = app_name_meta.get("app_name") or app_name_meta.get("old_app_name", "app")

    matched, only_in_old, only_in_new = _match_components(old_components, new_components)

    if only_in_old:
        print(f"Components only in old archive: {', '.join(only_in_old)}", file=sys.stderr)
    if only_in_new:
        print(f"Components only in new archive: {', '.join(only_in_new)}", file=sys.stderr)

    component_results: Dict[str, Tuple[str, Optional[dict]]] = {}
    total = len(matched)
    for i, (old_comp, new_comp) in enumerate(matched, 1):
        print(f"  [{i}/{total}] Analyzing {old_comp.relative_path} ...", file=sys.stderr)
        analysis = analyze_component(
            str(old_comp.absolute_path),
            str(new_comp.absolute_path),
            warnings,
        )
        component_results[old_comp.relative_path] = (old_comp.component_type, analysis)

    print("Demangling symbols ...", file=sys.stderr)
    all_names = _collect_all_mangled_names_diff(component_results)
    demangle_lookup = demangle_symbols(all_names)

    # Link maps (optional)
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

    now = datetime.datetime.now()
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    metadata = {
        "generated_at": now.isoformat(),
        "old_archive": str(old_path),
        "new_archive": str(new_path),
        **app_name_meta,
    }

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

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path("analysis-reports") / f"{app_name}-size-diff-{timestamp}.json"

    write_report(report, output_path)

    analyzed = sum(1 for _, (_, a) in component_results.items() if a is not None)
    print(f"Report written to: {output_path}", file=sys.stderr)
    print(f"Components analyzed: {analyzed}/{total}", file=sys.stderr)
    if warnings:
        print(f"Warnings: {len(warnings)} (see 'analysis_warnings' in report)", file=sys.stderr)
```

- [ ] **Step 7.4 — Run tests to confirm pass**

```bash
cd oneapp_size_analysis && python -m pytest tests/test_main.py -v
```

Expected: all 5 new main tests pass.

- [ ] **Step 7.5 — Run full suite**

```bash
cd oneapp_size_analysis && python -m pytest tests/ -q
```

Expected: all pass (46 original + new tests).

- [ ] **Step 7.6 — Commit**

```bash
git add oneapp_size_analysis/oneapp_size_analysis/main.py oneapp_size_analysis/tests/test_main.py
git commit -m "feat: add --link-map CLI flags to main.py"
```

---

## Task 8: README updates

**Files:**
- Modify: `README.md`

- [ ] **Step 8.1 — Update xcodebuild command to include `LD_MAP_FILE`**

In the "Building an XCArchive for Analysis" section, update both the project-based and workspace-based commands to add `LD_MAP_FILE`:

```bash
xcodebuild archive \
  -project YourApp.xcodeproj \
  -scheme YourScheme \
  -configuration Release \
  -archivePath ~/Archives/YourApp.xcarchive \
  LD_MAP_FILE=~/Archives/YourApp-linkmap.txt \
  STRIP_INSTALLED_PRODUCT=NO \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGN_IDENTITY="" \
  CODE_SIGNING_REQUIRED=NO
```

Also add `LD_MAP_FILE` to the flags table:

| Flag | Purpose |
|---|---|
| `LD_MAP_FILE=~/Archives/YourApp-linkmap.txt` | Optional but recommended. Writes the linker link map to a known path so it can be passed to the tool for library attribution. |

- [ ] **Step 8.2 — Add "Link Map Support" section to README**

Add a new section after "Building an XCArchive for Analysis" and before "Prerequisites":

````markdown
## Link Map Support

When you provide a link map alongside an archive, the tool can attribute each function to the library that contributed it — showing you not just that a 4KB function exists, but that it came from `PluginA`.

This is the key feature for tracking whether individual feature plugins are growing between builds.

### Providing a link map

**List mode:**

```bash
oneapp-size-analysis ARCHIVE.xcarchive \
  --link-map ~/Archives/YourApp-linkmap.txt
```

**Diff mode:**

```bash
oneapp-size-analysis OLD.xcarchive NEW.xcarchive \
  --old-link-map ~/Archives/Old-linkmap.txt \
  --new-link-map ~/Archives/New-linkmap.txt
```

Both `--old-link-map` and `--new-link-map` are independent — you can provide one without the other.

### What the link map adds to the report

Each function entry gains two new fields when its symbol is found in the link map:

```json
{
  "mangled_name": "_$s7PluginA11SomeFeatureC6doThingyyF",
  "demangled_name": "PluginA.SomeFeature.doThing()",
  "bytes": 4096,
  "library": "PluginA",
  "source_file": "SomeFeature.o"
}
```

A `libraries` block is also added to each component, showing total bytes per library:

**List mode:**
```json
"libraries": {
  "PluginA": {
    "bytes": 610000,
    "text_bytes": 524288,
    "symbol_count": 142,
    "percent_of_text": "5.3%"
  }
}
```

`bytes` = all symbols from this library across all sections.
`text_bytes` = symbols in `__TEXT __text` only (executable code).
`percent_of_text` = `text_bytes` as a fraction of total `__text` bytes.

**Diff mode** (when both link maps provided):
```json
"libraries": {
  "PluginA": {
    "old_bytes": 524288,
    "new_bytes": 573440,
    "diff_bytes": 49152,
    "diff_percent": "+9.4%",
    "old_symbol_count": 142,
    "new_symbol_count": 156
  }
}
```

### Library name extraction

For internal Xcode targets, the tool extracts the library name from the `.build` directory in the object file path:

```
/DerivedData/.../PluginA.build/Objects-normal/arm64/SomeFeature.o  →  "PluginA"
```

### A note on size accuracy

The `functions` list uses sizes computed by `cmpcodesize` via address arithmetic — these are **approximations** (each symbol's size is estimated as the gap to the next symbol's address). The `libraries` block uses sizes from the link map, which are the **authoritative sizes** as determined by the linker. Minor differences between per-function `bytes` values and library `bytes` totals are expected and normal.
````

- [ ] **Step 8.3 — Commit**

```bash
git add README.md
git commit -m "docs: update README with link map support and size accuracy note"
```

---

## Final Verification

- [ ] **Run the complete test suite one last time**

```bash
cd oneapp_size_analysis && python -m pytest tests/ -v
```

Expected: all tests pass with 0 failures.

- [ ] **Verify `--help` output includes new flags**

```bash
oneapp-size-analysis --help
```

Expected output includes `--link-map`, `--old-link-map`, `--new-link-map`.
