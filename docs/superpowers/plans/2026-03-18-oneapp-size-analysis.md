# oneapp-size-analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a CLI tool that takes two XCArchive paths, discovers every Mach-O binary in each, diffs their sizes using cmpcodesize, and writes a fully-verbose demangled JSON report.

**Architecture:** New Python package `oneapp_size_analysis` alongside the existing `cmpcodesize` package. `archive.py` discovers components, `analysis.py` drives `cmpcodesize.compare.read_sizes` for diffs, `demangle.py` batches all symbols through `xcrun swift-demangle`, and `report.py` assembles and writes the final JSON. `main.py` orchestrates the pipeline.

**Tech Stack:** Python 3.12, `cmpcodesize` sibling package (library import), `otool` (macOS system), `xcrun swift-demangle` (Xcode toolchain), Python standard library only (`argparse`, `collections`, `datetime`, `json`, `pathlib`, `plistlib`, `subprocess`, `unittest.mock` for tests).

---

## File Map

| File | Purpose |
|---|---|
| `oneapp_size_analysis/setup.py` | Package config; declares `oneapp-size-analysis` console script |
| `oneapp_size_analysis/oneapp_size_analysis/__init__.py` | Version string |
| `oneapp_size_analysis/oneapp_size_analysis/main.py` | CLI entry point; orchestrates archive → analysis → demangle → report |
| `oneapp_size_analysis/oneapp_size_analysis/archive.py` | `ComponentDescriptor` dataclass + recursive XCArchive traversal |
| `oneapp_size_analysis/oneapp_size_analysis/analysis.py` | `analyze_component()` — drives `read_sizes`, computes diffs, detects arch |
| `oneapp_size_analysis/oneapp_size_analysis/demangle.py` | `demangle_symbols()` — one batch call to `xcrun swift-demangle` |
| `oneapp_size_analysis/oneapp_size_analysis/report.py` | `build_report()` + `write_report()` — assembles and writes JSON |
| `oneapp_size_analysis/tests/__init__.py` | Empty |
| `oneapp_size_analysis/tests/test_archive.py` | Unit tests for XCArchive traversal |
| `oneapp_size_analysis/tests/test_analysis.py` | Unit tests for diff computation and function classification |
| `oneapp_size_analysis/tests/test_demangle.py` | Unit tests for batch demangling |
| `oneapp_size_analysis/tests/test_report.py` | Unit tests for JSON assembly and file writing |

---

## Task 1: Package Scaffolding

**Files:**
- Create: `oneapp_size_analysis/setup.py`
- Create: `oneapp_size_analysis/oneapp_size_analysis/__init__.py`
- Create: `oneapp_size_analysis/tests/__init__.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p oneapp_size_analysis/oneapp_size_analysis
mkdir -p oneapp_size_analysis/tests
```

- [ ] **Step 2: Write `setup.py`**

```python
# oneapp_size_analysis/setup.py
from setuptools import setup, find_packages

setup(
    name="oneapp-size-analysis",
    version="0.1.0",
    packages=find_packages(),
    install_requires=["cmpcodesize"],
    entry_points={
        "console_scripts": [
            "oneapp-size-analysis=oneapp_size_analysis.main:main",
        ],
    },
    python_requires=">=3.9",
)
```

- [ ] **Step 3: Write `__init__.py`**

```python
# oneapp_size_analysis/oneapp_size_analysis/__init__.py
__version__ = "0.1.0"
```

- [ ] **Step 4: Create empty test `__init__.py`**

```python
# oneapp_size_analysis/tests/__init__.py
```

- [ ] **Step 5: Install both packages in editable mode**

```bash
cd /path/to/OneAppSizeAnalysis
pip install -e ./cmpcodesize
pip install -e ./oneapp_size_analysis
```

Expected: both packages install without error. `oneapp-size-analysis --help` should show "error: the following arguments are required" (since `main.py` doesn't exist yet).

- [ ] **Step 6: Commit**

```bash
git add oneapp_size_analysis/
git commit -m "feat: scaffold oneapp-size-analysis package"
```

---

## Task 2: `archive.py` — XCArchive Traversal

**Files:**
- Create: `oneapp_size_analysis/oneapp_size_analysis/archive.py`
- Create: `oneapp_size_analysis/tests/test_archive.py`

The XCArchive layout this module must traverse:
```
MyApp.xcarchive/
└── Products/
    └── Applications/
        └── MyApp.app/                    ← main_executable
            ├── Info.plist                (CFBundleExecutable = "MyApp")
            ├── MyApp                     ← the binary
            ├── Frameworks/
            │   └── Foo.framework/        ← framework
            │       ├── Info.plist
            │       └── Foo
            ├── PlugIns/
            │   └── MyExt.appex/          ← extension
            │       ├── Info.plist
            │       ├── MyExt
            │       └── Frameworks/       ← framework inside extension
            │           └── Bar.framework/
            │               ├── Info.plist
            │               └── Bar
            └── Watch/
                └── MyWatch.app/          ← watch_app
                    ├── Info.plist
                    ├── MyWatch
                    ├── Frameworks/       ← framework inside watch
                    │   └── Baz.framework/
                    │       ├── Info.plist
                    │       └── Baz
                    └── PlugIns/          ← extension inside watch
                        └── MyComp.appex/
                            ├── Info.plist
                            ├── MyComp
                            └── Frameworks/
                                └── Qux.framework/
                                    ├── Info.plist
                                    └── Qux
```

- [ ] **Step 1: Write the failing tests**

```python
# oneapp_size_analysis/tests/test_archive.py
import plistlib
import sys
import tempfile
from pathlib import Path

import pytest

from oneapp_size_analysis.archive import (
    ComponentDescriptor,
    ArchiveError,
    discover_components,
)


def _make_bundle(parent: Path, bundle_name: str, executable: str) -> Path:
    """Create a minimal .app/.framework/.appex bundle with Info.plist and binary."""
    bundle = parent / bundle_name
    bundle.mkdir(parents=True, exist_ok=True)
    with open(bundle / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleExecutable": executable}, f)
    (bundle / executable).touch()
    return bundle


def _make_xcarchive(tmp: Path) -> Path:
    """Build a minimal XCArchive structure and return its path."""
    apps_dir = tmp / "MyApp.xcarchive" / "Products" / "Applications"
    apps_dir.mkdir(parents=True)
    return tmp / "MyApp.xcarchive"


def test_discover_main_executable(tmp_path):
    archive = _make_xcarchive(tmp_path)
    apps = archive / "Products" / "Applications"
    _make_bundle(apps, "MyApp.app", "MyApp")

    components = discover_components(archive)

    main = next(c for c in components if c.component_type == "main_executable")
    assert main.relative_path == "MyApp.app/MyApp"
    assert main.absolute_path == apps / "MyApp.app" / "MyApp"


def test_discover_framework(tmp_path):
    archive = _make_xcarchive(tmp_path)
    apps = archive / "Products" / "Applications"
    app = _make_bundle(apps, "MyApp.app", "MyApp")
    fw_dir = app / "Frameworks"
    fw_dir.mkdir()
    _make_bundle(fw_dir, "Foo.framework", "Foo")

    components = discover_components(archive)

    fw = next(c for c in components if c.component_type == "framework")
    assert fw.relative_path == "MyApp.app/Frameworks/Foo.framework/Foo"
    assert fw.absolute_path == fw_dir / "Foo.framework" / "Foo"


def test_discover_extension(tmp_path):
    archive = _make_xcarchive(tmp_path)
    apps = archive / "Products" / "Applications"
    app = _make_bundle(apps, "MyApp.app", "MyApp")
    ext_dir = app / "PlugIns"
    ext_dir.mkdir()
    _make_bundle(ext_dir, "MyExt.appex", "MyExt")

    components = discover_components(archive)

    ext = next(c for c in components if c.component_type == "extension")
    assert ext.relative_path == "MyApp.app/PlugIns/MyExt.appex/MyExt"


def test_discover_framework_inside_extension(tmp_path):
    archive = _make_xcarchive(tmp_path)
    apps = archive / "Products" / "Applications"
    app = _make_bundle(apps, "MyApp.app", "MyApp")
    ext_dir = app / "PlugIns"
    ext_dir.mkdir()
    ext = _make_bundle(ext_dir, "MyExt.appex", "MyExt")
    nested_fw_dir = ext / "Frameworks"
    nested_fw_dir.mkdir()
    _make_bundle(nested_fw_dir, "Bar.framework", "Bar")

    components = discover_components(archive)

    nested_fw = next(
        c for c in components
        if c.component_type == "framework" and "PlugIns" in c.relative_path
    )
    assert nested_fw.relative_path == "MyApp.app/PlugIns/MyExt.appex/Frameworks/Bar.framework/Bar"


def test_discover_watch_app(tmp_path):
    archive = _make_xcarchive(tmp_path)
    apps = archive / "Products" / "Applications"
    app = _make_bundle(apps, "MyApp.app", "MyApp")
    watch_dir = app / "Watch"
    watch_dir.mkdir()
    _make_bundle(watch_dir, "MyWatch.app", "MyWatch")

    components = discover_components(archive)

    watch = next(c for c in components if c.component_type == "watch_app")
    assert watch.relative_path == "MyApp.app/Watch/MyWatch.app/MyWatch"


def test_discover_framework_inside_watch(tmp_path):
    archive = _make_xcarchive(tmp_path)
    apps = archive / "Products" / "Applications"
    app = _make_bundle(apps, "MyApp.app", "MyApp")
    watch_dir = app / "Watch"
    watch_dir.mkdir()
    watch = _make_bundle(watch_dir, "MyWatch.app", "MyWatch")
    fw_dir = watch / "Frameworks"
    fw_dir.mkdir()
    _make_bundle(fw_dir, "Baz.framework", "Baz")

    components = discover_components(archive)

    fw = next(
        c for c in components
        if c.component_type == "framework" and "Watch" in c.relative_path
    )
    assert fw.relative_path == "MyApp.app/Watch/MyWatch.app/Frameworks/Baz.framework/Baz"


def test_error_no_app(tmp_path):
    archive = _make_xcarchive(tmp_path)
    with pytest.raises(ArchiveError, match="No .app bundle"):
        discover_components(archive)


def test_error_multiple_apps(tmp_path):
    archive = _make_xcarchive(tmp_path)
    apps = archive / "Products" / "Applications"
    _make_bundle(apps, "First.app", "First")
    _make_bundle(apps, "Second.app", "Second")
    with pytest.raises(ArchiveError, match="More than one"):
        discover_components(archive)


def test_missing_cfbundleexecutable_skipped(tmp_path, capsys):
    archive = _make_xcarchive(tmp_path)
    apps = archive / "Products" / "Applications"
    app = apps / "MyApp.app"
    app.mkdir()
    # Info.plist without CFBundleExecutable
    with open(app / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleName": "MyApp"}, f)

    warnings = []
    components = discover_components(archive, warnings=warnings)

    assert not any(c.component_type == "main_executable" for c in components)
    assert any("CFBundleExecutable" in w for w in warnings)
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd oneapp_size_analysis
python -m pytest tests/test_archive.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'oneapp_size_analysis.archive'`

- [ ] **Step 3: Implement `archive.py`**

```python
# oneapp_size_analysis/oneapp_size_analysis/archive.py
import plistlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


class ArchiveError(Exception):
    pass


@dataclass
class ComponentDescriptor:
    relative_path: str
    absolute_path: Path
    component_type: str  # "main_executable" | "framework" | "extension" | "watch_app"


def _read_executable(bundle_path: Path, warnings: List[str]) -> Optional[str]:
    """Read CFBundleExecutable from a bundle's Info.plist. Returns None and appends a
    warning if the key is absent or the plist is unreadable."""
    plist_path = bundle_path / "Info.plist"
    try:
        with open(plist_path, "rb") as f:
            info = plistlib.load(f)
        name = info.get("CFBundleExecutable")
        if name is None:
            warnings.append(
                f"Warning: CFBundleExecutable missing in {plist_path} — skipping bundle"
            )
        return name
    except Exception as exc:
        warnings.append(f"Warning: Could not read {plist_path}: {exc} — skipping bundle")
        return None


def _collect_frameworks(
    bundle_path: Path,
    parent_rel: str,
    warnings: List[str],
) -> List[ComponentDescriptor]:
    """Collect all framework binaries directly inside {bundle_path}/Frameworks/."""
    results = []
    fw_dir = bundle_path / "Frameworks"
    if not fw_dir.is_dir():
        return results
    for fw in sorted(fw_dir.glob("*.framework")):
        name = _read_executable(fw, warnings)
        if name is None:
            continue
        binary = fw / name
        rel = f"{parent_rel}/Frameworks/{fw.name}/{name}"
        results.append(ComponentDescriptor(rel, binary, "framework"))
    return results


def _collect_extensions(
    bundle_path: Path,
    parent_rel: str,
    warnings: List[str],
) -> List[ComponentDescriptor]:
    """Collect all extension binaries directly inside {bundle_path}/PlugIns/,
    plus their nested frameworks."""
    results = []
    plugins_dir = bundle_path / "PlugIns"
    if not plugins_dir.is_dir():
        return results
    for ext in sorted(plugins_dir.glob("*.appex")):
        name = _read_executable(ext, warnings)
        if name is None:
            continue
        binary = ext / name
        rel = f"{parent_rel}/PlugIns/{ext.name}/{name}"
        results.append(ComponentDescriptor(rel, binary, "extension"))
        # Frameworks nested inside this extension
        results.extend(_collect_frameworks(ext, f"{parent_rel}/PlugIns/{ext.name}", warnings))
    return results


def discover_components(
    archive_path: Path,
    warnings: Optional[List[str]] = None,
) -> List[ComponentDescriptor]:
    """Traverse an XCArchive and return all Mach-O component descriptors.

    Raises ArchiveError if the archive has no .app or more than one .app.
    Malformed bundles (missing CFBundleExecutable) emit warnings and are skipped.
    """
    if warnings is None:
        warnings = []

    apps_dir = archive_path / "Products" / "Applications"
    app_bundles = sorted(apps_dir.glob("*.app"))

    if len(app_bundles) == 0:
        raise ArchiveError(f"No .app bundle found in {apps_dir}")
    if len(app_bundles) > 1:
        raise ArchiveError(
            f"More than one .app bundle found in {apps_dir}: "
            + ", ".join(b.name for b in app_bundles)
        )

    app = app_bundles[0]
    results: List[ComponentDescriptor] = []

    # 1. Main executable
    main_name = _read_executable(app, warnings)
    if main_name is not None:
        app_rel = f"{app.name}/{main_name}"
        results.append(ComponentDescriptor(app_rel, app / main_name, "main_executable"))

    # 2. Main app frameworks
    app_parent_rel = app.name.removesuffix("/" + main_name) if main_name else app.name
    # Relative root for the app bundle itself (just the .app name)
    app_bundle_rel = app.name
    results.extend(_collect_frameworks(app, app_bundle_rel, warnings))

    # 3. Main app extensions (+ their frameworks)
    results.extend(_collect_extensions(app, app_bundle_rel, warnings))

    # 4. Watch apps (+ their frameworks and extensions)
    watch_dir = app / "Watch"
    if watch_dir.is_dir():
        for watch in sorted(watch_dir.glob("*.app")):
            wname = _read_executable(watch, warnings)
            if wname is None:
                continue
            watch_rel = f"{app_bundle_rel}/Watch/{watch.name}/{wname}"
            results.append(ComponentDescriptor(watch_rel, watch / wname, "watch_app"))
            # Watch frameworks
            watch_bundle_rel = f"{app_bundle_rel}/Watch/{watch.name}"
            results.extend(_collect_frameworks(watch, watch_bundle_rel, warnings))
            # Watch extensions (+ their frameworks)
            results.extend(_collect_extensions(watch, watch_bundle_rel, warnings))

    return results


def validate_app_names(
    old_archive: Path,
    new_archive: Path,
    warnings: List[str],
) -> dict:
    """Compare CFBundleExecutable from both archives' main app.
    Returns a metadata dict with either 'app_name' or 'old_app_name'/'new_app_name'."""
    def _get_name(archive: Path) -> Optional[str]:
        apps_dir = archive / "Products" / "Applications"
        bundles = list(apps_dir.glob("*.app"))
        if not bundles:
            return None
        w: List[str] = []
        return _read_executable(bundles[0], w)

    old_name = _get_name(old_archive)
    new_name = _get_name(new_archive)

    if old_name and new_name and old_name == new_name:
        return {"app_name": old_name}
    else:
        if old_name != new_name:
            warnings.append(
                f"Warning: app names differ between archives: '{old_name}' vs '{new_name}'"
            )
        return {"old_app_name": old_name or "unknown", "new_app_name": new_name or "unknown"}
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_archive.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add oneapp_size_analysis/oneapp_size_analysis/archive.py oneapp_size_analysis/tests/test_archive.py
git commit -m "feat: add archive.py — XCArchive traversal and component discovery"
```

---

## Task 3: `analysis.py` — Size Diff Computation

**Files:**
- Create: `oneapp_size_analysis/oneapp_size_analysis/analysis.py`
- Create: `oneapp_size_analysis/tests/test_analysis.py`

This module drives `cmpcodesize.compare.read_sizes` and computes all diffs. It is called per matched component pair and returns a dict ready for `report.py` (function entries have `mangled_name` only — `report.py` adds `demangled_name`).

- [ ] **Step 1: Write the failing tests**

```python
# oneapp_size_analysis/tests/test_analysis.py
import collections
from unittest.mock import patch, call

import pytest

from oneapp_size_analysis.analysis import (
    detect_arch,
    fmt_percent,
    fmt_percent_of_text,
    diff_entry,
    classify_functions,
    analyze_component,
    CATEGORY_NAMES,
)


# ── fmt_percent ──────────────────────────────────────────────────────────────

def test_fmt_percent_increase():
    assert fmt_percent(new_bytes=110, old_bytes=100) == "+10.0%"

def test_fmt_percent_decrease():
    assert fmt_percent(new_bytes=90, old_bytes=100) == "-10.0%"

def test_fmt_percent_no_change():
    assert fmt_percent(new_bytes=100, old_bytes=100) == "+0.0%"

def test_fmt_percent_old_zero():
    assert fmt_percent(new_bytes=100, old_bytes=0) == "N/A"


# ── fmt_percent_of_text ──────────────────────────────────────────────────────

def test_fmt_percent_of_text_normal():
    assert fmt_percent_of_text(bytes_val=50, text_bytes=100) == "50.0%"

def test_fmt_percent_of_text_zero_denominator():
    assert fmt_percent_of_text(bytes_val=50, text_bytes=0) == "N/A"


# ── diff_entry ────────────────────────────────────────────────────────────────

def test_diff_entry():
    result = diff_entry(old_bytes=100, new_bytes=150)
    assert result == {
        "old_bytes": 100,
        "new_bytes": 150,
        "diff_bytes": 50,
        "diff_percent": "+50.0%",
    }


# ── CATEGORY_NAMES ────────────────────────────────────────────────────────────

def test_category_names_contains_expected():
    assert "Swift Function" in CATEGORY_NAMES
    assert "ObjC" in CATEGORY_NAMES
    assert "CPP" in CATEGORY_NAMES
    assert "Unknown" in CATEGORY_NAMES
    # Section names must NOT be in the set
    assert "__text" not in CATEGORY_NAMES


# ── classify_functions ───────────────────────────────────────────────────────

def _dd(d: dict) -> collections.defaultdict:
    result = collections.defaultdict(int)
    result.update(d)
    return result

def test_classify_added():
    old = _dd({})
    new = _dd({"_newFunc": 100})
    result = classify_functions(old, new)
    assert len(result["added"]) == 1
    assert result["added"][0] == {"mangled_name": "_newFunc", "new_bytes": 100}
    assert result["totals"]["added_bytes"] == 100

def test_classify_removed():
    old = _dd({"_oldFunc": 80})
    new = _dd({})
    result = classify_functions(old, new)
    assert len(result["removed"]) == 1
    assert result["removed"][0] == {"mangled_name": "_oldFunc", "old_bytes": 80}
    assert result["totals"]["removed_bytes"] == 80

def test_classify_increased():
    old = _dd({"_func": 100})
    new = _dd({"_func": 160})
    result = classify_functions(old, new)
    assert len(result["increased"]) == 1
    entry = result["increased"][0]
    assert entry["old_bytes"] == 100
    assert entry["new_bytes"] == 160
    assert entry["diff_bytes"] == 60
    assert entry["diff_percent"] == "+60.0%"
    assert result["totals"]["increased_bytes"] == 60

def test_classify_decreased():
    old = _dd({"_func": 200})
    new = _dd({"_func": 150})
    result = classify_functions(old, new)
    assert len(result["decreased"]) == 1
    entry = result["decreased"][0]
    assert entry["diff_bytes"] == -50
    assert entry["diff_percent"] == "-25.0%"
    assert result["totals"]["decreased_bytes"] == 50

def test_classify_unchanged():
    old = _dd({"_func": 64})
    new = _dd({"_func": 64})
    result = classify_functions(old, new)
    assert len(result["unchanged"]) == 1
    assert result["unchanged"][0] == {"mangled_name": "_func", "bytes": 64}

def test_classify_net_change():
    old = _dd({"_removed": 100, "_shrunk": 200})
    new = _dd({"_added": 50, "_shrunk": 150})
    result = classify_functions(old, new)
    # added_bytes=50, removed_bytes=100, increased_bytes=0, decreased_bytes=50
    # net = 0 - 50 + 50 - 100 = -100
    assert result["totals"]["net_change_bytes"] == -100

def test_classify_sort_order_added():
    old = _dd({})
    new = _dd({"_small": 10, "_big": 500, "_medium": 100})
    result = classify_functions(old, new)
    sizes = [e["new_bytes"] for e in result["added"]]
    assert sizes == sorted(sizes, reverse=True)

def test_classify_sort_order_increased():
    old = _dd({"_a": 100, "_b": 100, "_c": 100})
    new = _dd({"_a": 150, "_b": 200, "_c": 110})
    result = classify_functions(old, new)
    diffs = [e["diff_bytes"] for e in result["increased"]]
    assert diffs == sorted(diffs, reverse=True)


# ── detect_arch ───────────────────────────────────────────────────────────────

def test_detect_arch_arm64():
    fake_output = b"Fat headers\nfat_magic 0xcafebabe\nnfat_arch 2\narchitecture x86_64\n    cputype 16777223\narchitecture arm64\n    cputype 16777228\n"
    with patch("subprocess.check_output", return_value=fake_output):
        # First arch found is x86_64 (first-match wins, matching cmpcodesize behavior)
        arch = detect_arch("/fake/binary")
    assert arch == "x86_64"

def test_detect_arch_arm64_only():
    fake_output = b"architecture arm64\n    cputype 16777228\n"
    with patch("subprocess.check_output", return_value=fake_output):
        arch = detect_arch("/fake/binary")
    assert arch == "arm64"

def test_detect_arch_unknown():
    fake_output = b"Non-fat file: /fake/binary is architecture: arm64\n"
    with patch("subprocess.check_output", return_value=fake_output):
        arch = detect_arch("/fake/binary")
    # No "architecture X" line from -V -f → unknown
    assert arch == "unknown"


# ── analyze_component (integration with mocked read_sizes) ───────────────────

def _make_mock_pass1(sect_data: dict, seg_data: dict):
    """Return a side_effect function that populates the defaultdicts passed to read_sizes."""
    def _side_effect(sect_sizes, seg_sizes, path, function_details, group_by_prefix):
        for k, v in sect_data.items():
            sect_sizes[k] += v
        if isinstance(seg_sizes, collections.defaultdict):
            for k, v in seg_data.items():
                seg_sizes[k] += v
    return _side_effect

def test_section_category_separation():
    """Keys from cmpcodesize categories are routed to 'categories', rest to 'sections'."""
    from oneapp_size_analysis.analysis import CATEGORY_NAMES
    # All known cmpcodesize category names
    assert "Swift Function" in CATEGORY_NAMES
    assert "ObjC" in CATEGORY_NAMES
    # Section names are NOT category names
    assert "__text" not in CATEGORY_NAMES
    assert "__stubs" not in CATEGORY_NAMES
    assert "__const" not in CATEGORY_NAMES
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_analysis.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'oneapp_size_analysis.analysis'`

- [ ] **Step 3: Implement `analysis.py`**

```python
# oneapp_size_analysis/oneapp_size_analysis/analysis.py
import collections
import re
import subprocess
from typing import Any, Dict, List, Tuple

from cmpcodesize.compare import read_sizes
from cmpcodesize.compare import categories as _CATEGORIES

# Canonical set of category names from cmpcodesize — used to separate
# section-name keys from category-name keys in the shared sect_sizes dict.
CATEGORY_NAMES = {cat[0] for cat in _CATEGORIES}

# Segments reported in the JSON (only those tracked by cmpcodesize).
_SEGMENTS = ["__TEXT", "__DATA", "__LLVM_COV", "__LINKEDIT"]

# Sections reported in the JSON. Any additional keys from sect_sizes that are
# not category names are also included (forward-compatibility).
_KNOWN_SECTIONS = [
    "__text", "__stubs", "__const", "__cstring",
    "__objc_methname", "__objc_const", "__data",
    "__swift5_proto", "__common", "__bss",
]


def fmt_percent(new_bytes: int, old_bytes: int) -> str:
    """Format a size change as a signed percentage string, or 'N/A' if old is zero."""
    if old_bytes == 0:
        return "N/A"
    pct = (new_bytes / old_bytes - 1.0) * 100.0
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def fmt_percent_of_text(bytes_val: int, text_bytes: int) -> str:
    """Format bytes as a percentage of __text size, or 'N/A' if __text is zero."""
    if text_bytes == 0:
        return "N/A"
    return f"{bytes_val * 100.0 / text_bytes:.1f}%"


def diff_entry(old_bytes: int, new_bytes: int) -> Dict[str, Any]:
    """Build a size-diff dict for sections, segments, and categories."""
    return {
        "old_bytes": old_bytes,
        "new_bytes": new_bytes,
        "diff_bytes": new_bytes - old_bytes,
        "diff_percent": fmt_percent(new_bytes, old_bytes),
    }


def detect_arch(binary_path: str) -> str:
    """Detect the architecture that read_sizes would select for this binary.

    Replicates the exact loop from cmpcodesize.compare.read_sizes:
    - Records the first architecture name seen (first-match wins).
    - The second condition `if 'arm64' == arch` only fires when the first arch
      was already 'arm64' — it is effectively a no-op, kept verbatim so this
      function and read_sizes behave identically.
    - Returns 'unknown' if no architecture lines are found.

    Note: This is a best-effort, separate subprocess call and may theoretically
    diverge from what read_sizes used internally.
    """
    try:
        output = subprocess.check_output(
            ["otool", "-V", "-f", binary_path],
            stderr=subprocess.DEVNULL,
        ).decode("utf-8")
    except subprocess.CalledProcessError:
        return "unknown"

    arch = None
    arch_pattern = re.compile(r"architecture ([\S]+)")
    for line in output.split("\n"):
        m = arch_pattern.match(line)
        if m:
            if arch is None:
                arch = m.group(1)
            if "arm64" == arch:
                arch = "arm64"  # matches cmpcodesize behavior exactly
    return arch if arch is not None else "unknown"


def classify_functions(
    old_func_sizes: collections.defaultdict,
    new_func_sizes: collections.defaultdict,
) -> Dict[str, Any]:
    """Classify symbol size changes into added/removed/increased/decreased/unchanged.

    Uses zero-equality as the absent sentinel (mirrors cmpcodesize behavior).
    Functions with a true computed size of 0 are indistinguishable from absent
    and are treated as absent — this is a known limitation of cmpcodesize.

    Sort order per list:
      added/removed  → descending by size
      increased      → descending by diff_bytes
      decreased      → descending by abs(diff_bytes)
      unchanged      → descending by bytes
    Within equal sizes, secondary sort is mangled_name ascending.
    """
    all_names = set(old_func_sizes.keys()) | set(new_func_sizes.keys())

    added: List[Dict] = []
    removed: List[Dict] = []
    increased: List[Dict] = []
    decreased: List[Dict] = []
    unchanged: List[Dict] = []

    for name in all_names:
        old_sz = old_func_sizes[name]
        new_sz = new_func_sizes[name]

        if old_sz == 0 and new_sz > 0:
            added.append({"mangled_name": name, "new_bytes": new_sz})
        elif old_sz > 0 and new_sz == 0:
            removed.append({"mangled_name": name, "old_bytes": old_sz})
        elif old_sz > 0 and new_sz > 0:
            diff = new_sz - old_sz
            if diff > 0:
                increased.append({
                    "mangled_name": name,
                    "old_bytes": old_sz,
                    "new_bytes": new_sz,
                    "diff_bytes": diff,
                    "diff_percent": fmt_percent(new_sz, old_sz),
                })
            elif diff < 0:
                decreased.append({
                    "mangled_name": name,
                    "old_bytes": old_sz,
                    "new_bytes": new_sz,
                    "diff_bytes": diff,
                    "diff_percent": fmt_percent(new_sz, old_sz),
                })
            else:
                unchanged.append({"mangled_name": name, "bytes": new_sz})

    added.sort(key=lambda e: (-e["new_bytes"], e["mangled_name"]))
    removed.sort(key=lambda e: (-e["old_bytes"], e["mangled_name"]))
    increased.sort(key=lambda e: (-e["diff_bytes"], e["mangled_name"]))
    decreased.sort(key=lambda e: (e["diff_bytes"], e["mangled_name"]))  # diff is negative
    unchanged.sort(key=lambda e: (-e["bytes"], e["mangled_name"]))

    added_bytes = sum(e["new_bytes"] for e in added)
    removed_bytes = sum(e["old_bytes"] for e in removed)
    increased_bytes = sum(e["diff_bytes"] for e in increased)
    decreased_bytes = sum(abs(e["diff_bytes"]) for e in decreased)
    net = increased_bytes - decreased_bytes + added_bytes - removed_bytes

    return {
        "added": added,
        "removed": removed,
        "increased": increased,
        "decreased": decreased,
        "unchanged": unchanged,
        "totals": {
            "added_bytes": added_bytes,
            "removed_bytes": removed_bytes,
            "increased_bytes": increased_bytes,
            "decreased_bytes": decreased_bytes,
            "net_change_bytes": net,
        },
    }


def analyze_component(
    old_path: str,
    new_path: str,
    warnings: List[str],
) -> Dict[str, Any]:
    """Run both read_sizes passes for an old/new binary pair and return structured diffs.

    Returns None if otool fails or the binary is missing; appends a warning in that case.
    Function entries contain 'mangled_name' only — report.py adds 'demangled_name'.
    """
    import os

    for label, path in [("old", old_path), ("new", new_path)]:
        if not os.path.isfile(path):
            warnings.append(f"Warning: binary not found ({label}): {path} — skipping component")
            return None

    arch = detect_arch(old_path)  # use old binary for arch reporting

    # Pass 1: sections, segments, categories
    old_sect = collections.defaultdict(int)
    old_seg = collections.defaultdict(int)
    new_sect = collections.defaultdict(int)
    new_seg = collections.defaultdict(int)

    try:
        read_sizes(old_sect, old_seg, old_path, function_details=True, group_by_prefix=True)
        read_sizes(new_sect, new_seg, new_path, function_details=True, group_by_prefix=True)
    except subprocess.CalledProcessError as exc:
        warnings.append(f"Warning: otool failed for {old_path} or {new_path}: {exc} — skipping")
        return None

    # Pass 2: per-function symbol sizes
    old_func = collections.defaultdict(int)
    new_func = collections.defaultdict(int)

    try:
        read_sizes(old_func, [], old_path, function_details=True, group_by_prefix=False)
        read_sizes(new_func, [], new_path, function_details=True, group_by_prefix=False)
    except subprocess.CalledProcessError as exc:
        warnings.append(f"Warning: otool failed (pass 2) for {old_path} or {new_path}: {exc} — skipping")
        return None

    # Separate section keys from category keys in sect_sizes
    def _split_sect(sect_sizes):
        sections = {}
        categories = {}
        for key, val in sect_sizes.items():
            if key in CATEGORY_NAMES:
                categories[key] = val
            else:
                sections[key] = val
        return sections, categories

    old_sections, old_cats = _split_sect(old_sect)
    new_sections, new_cats = _split_sect(new_sect)

    # Build segments dict
    segments = {}
    all_seg_keys = set(old_seg.keys()) | set(new_seg.keys()) | set(_SEGMENTS)
    for seg in sorted(all_seg_keys):
        segments[seg] = diff_entry(old_seg[seg], new_seg[seg])

    # Build sections dict (known + any unexpected keys)
    sections = {}
    all_sect_keys = set(old_sections.keys()) | set(new_sections.keys())
    ordered = [s for s in _KNOWN_SECTIONS if s in all_sect_keys]
    ordered += sorted(k for k in all_sect_keys if k not in _KNOWN_SECTIONS)
    for sect in ordered:
        sections[sect] = diff_entry(old_sections.get(sect, 0), new_sections.get(sect, 0))

    # Build categories dict (all 17 from cmpcodesize, in canonical order)
    old_text = old_sections.get("__text", 0)
    new_text = new_sections.get("__text", 0)
    categories_out = {}
    for cat_name, _ in _CATEGORIES:
        old_b = old_cats.get(cat_name, 0)
        new_b = new_cats.get(cat_name, 0)
        entry = diff_entry(old_b, new_b)
        entry["old_percent_of_text"] = fmt_percent_of_text(old_b, old_text)
        entry["new_percent_of_text"] = fmt_percent_of_text(new_b, new_text)
        categories_out[cat_name] = entry

    # Build functions dict
    functions = classify_functions(old_func, new_func)

    return {
        "architecture": arch,
        "segments": segments,
        "sections": sections,
        "categories": categories_out,
        "functions": functions,
    }
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_analysis.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add oneapp_size_analysis/oneapp_size_analysis/analysis.py oneapp_size_analysis/tests/test_analysis.py
git commit -m "feat: add analysis.py — size diff computation and function classification"
```

---

## Task 4: `demangle.py` — Batch Swift Demangling

**Files:**
- Create: `oneapp_size_analysis/oneapp_size_analysis/demangle.py`
- Create: `oneapp_size_analysis/tests/test_demangle.py`

`swift-demangle` accepts one symbol per line on stdin and writes one demangled form per line on stdout, in the same order. Non-Swift symbols are echoed back unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# oneapp_size_analysis/tests/test_demangle.py
from unittest.mock import patch, MagicMock
import subprocess

from oneapp_size_analysis.demangle import demangle_symbols


def _mock_run(input_text: str, demangled_lines: list):
    """Return a mock subprocess.run result whose stdout matches demangled_lines."""
    mock = MagicMock()
    mock.stdout = "\n".join(demangled_lines) + "\n"
    return mock


def test_demangle_empty():
    result = demangle_symbols([])
    assert result == {}


def test_demangle_swift_symbols():
    symbols = ["_$s3FooBarV", "_$s3BazQuxC"]
    demangled = ["Foo.Bar", "Baz.Qux"]
    mock_result = MagicMock()
    mock_result.stdout = "\n".join(demangled) + "\n"

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        result = demangle_symbols(symbols)

    # subprocess.run called once with all symbols via stdin
    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args
    assert call_kwargs.kwargs["input"] == "\n".join(symbols)

    assert result["_$s3FooBarV"] == "Foo.Bar"
    assert result["_$s3BazQuxC"] == "Baz.Qux"


def test_demangle_non_swift_passthrough():
    symbols = ["-[NSObject init]", "__Z3foov"]
    mock_result = MagicMock()
    # swift-demangle echoes non-Swift symbols unchanged
    mock_result.stdout = "-[NSObject init]\n__Z3foov\n"

    with patch("subprocess.run", return_value=mock_result):
        result = demangle_symbols(symbols)

    assert result["-[NSObject init]"] == "-[NSObject init]"
    assert result["__Z3foov"] == "__Z3foov"


def test_demangle_deduplication():
    """Duplicate input symbols should be sent only once to swift-demangle."""
    symbols = ["_$s3FooBarV", "_$s3FooBarV", "_$s3FooBarV"]
    mock_result = MagicMock()
    mock_result.stdout = "Foo.Bar\n"

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        result = demangle_symbols(symbols)

    call_kwargs = mock_run.call_args
    # Only one unique symbol sent
    assert call_kwargs.kwargs["input"].strip().count("\n") == 0  # one line, no newlines within
    assert result["_$s3FooBarV"] == "Foo.Bar"


def test_demangle_preserves_all_original_keys():
    """Every input symbol appears as a key in the result, even after dedup."""
    symbols = ["_$s3FooBarV", "_$s3FooBarV"]
    mock_result = MagicMock()
    mock_result.stdout = "Foo.Bar\n"

    with patch("subprocess.run", return_value=mock_result):
        result = demangle_symbols(symbols)

    assert "_$s3FooBarV" in result
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_demangle.py -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'oneapp_size_analysis.demangle'`

- [ ] **Step 3: Implement `demangle.py`**

```python
# oneapp_size_analysis/oneapp_size_analysis/demangle.py
import subprocess
from typing import Dict, Iterable


def demangle_symbols(names: Iterable[str]) -> Dict[str, str]:
    """Batch-demangle Swift mangled symbol names using xcrun swift-demangle.

    Accepts any symbol names (Swift mangled, ObjC, C++). Non-Swift symbols are
    returned unchanged. Deduplicates before sending to swift-demangle; all
    original names (including duplicates) are present as keys in the result.

    Returns a dict mapping each input name to its demangled form.
    """
    name_list = list(names)
    if not name_list:
        return {}

    # Deduplicate while preserving a stable order for the subprocess call.
    seen = {}
    unique_ordered = []
    for name in name_list:
        if name not in seen:
            seen[name] = None
            unique_ordered.append(name)

    result = subprocess.run(
        ["xcrun", "swift-demangle"],
        input="\n".join(unique_ordered),
        capture_output=True,
        text=True,
        check=True,
    )

    output_lines = result.stdout.splitlines()
    lookup: Dict[str, str] = {}
    for original, demangled in zip(unique_ordered, output_lines):
        lookup[original] = demangled.strip() if demangled.strip() else original

    return lookup
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_demangle.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add oneapp_size_analysis/oneapp_size_analysis/demangle.py oneapp_size_analysis/tests/test_demangle.py
git commit -m "feat: add demangle.py — batch swift-demangle integration"
```

---

## Task 5: `report.py` — JSON Assembly and File Writing

**Files:**
- Create: `oneapp_size_analysis/oneapp_size_analysis/report.py`
- Create: `oneapp_size_analysis/tests/test_report.py`

`report.py` takes the raw output from `analysis.analyze_component` (one dict per matched component), applies demangled names to all function entries, wraps each with its `type` and `relative_path`, and writes the final JSON.

- [ ] **Step 1: Write the failing tests**

```python
# oneapp_size_analysis/tests/test_report.py
import json
import tempfile
from pathlib import Path

import pytest

from oneapp_size_analysis.report import apply_demangled_names, build_report, write_report


def _make_component_analysis(arch="arm64") -> dict:
    """Minimal valid analyze_component output."""
    return {
        "architecture": arch,
        "segments": {"__TEXT": {"old_bytes": 1000, "new_bytes": 1100, "diff_bytes": 100, "diff_percent": "+10.0%"}},
        "sections": {"__text": {"old_bytes": 800, "new_bytes": 900, "diff_bytes": 100, "diff_percent": "+12.5%"}},
        "categories": {
            "Swift Function": {
                "old_bytes": 500, "new_bytes": 600, "diff_bytes": 100, "diff_percent": "+20.0%",
                "old_percent_of_text": "62.5%", "new_percent_of_text": "66.7%",
            }
        },
        "functions": {
            "added":     [{"mangled_name": "_$sAddedFunc", "new_bytes": 64}],
            "removed":   [{"mangled_name": "_$sRemovedFunc", "old_bytes": 32}],
            "increased": [{"mangled_name": "_$sGrownFunc", "old_bytes": 100, "new_bytes": 150, "diff_bytes": 50, "diff_percent": "+50.0%"}],
            "decreased": [],
            "unchanged": [{"mangled_name": "_$sStableFunc", "bytes": 80}],
            "totals": {"added_bytes": 64, "removed_bytes": 32, "increased_bytes": 50, "decreased_bytes": 0, "net_change_bytes": 82},
        },
    }


def test_apply_demangled_names_added():
    analysis = _make_component_analysis()
    lookup = {"_$sAddedFunc": "MyApp.addedFunc()"}
    apply_demangled_names(analysis, lookup)
    assert analysis["functions"]["added"][0]["demangled_name"] == "MyApp.addedFunc()"


def test_apply_demangled_names_removed():
    analysis = _make_component_analysis()
    lookup = {"_$sRemovedFunc": "MyApp.removedFunc()"}
    apply_demangled_names(analysis, lookup)
    assert analysis["functions"]["removed"][0]["demangled_name"] == "MyApp.removedFunc()"


def test_apply_demangled_names_increased():
    analysis = _make_component_analysis()
    lookup = {"_$sGrownFunc": "MyApp.grownFunc()"}
    apply_demangled_names(analysis, lookup)
    assert analysis["functions"]["increased"][0]["demangled_name"] == "MyApp.grownFunc()"


def test_apply_demangled_names_unchanged():
    analysis = _make_component_analysis()
    lookup = {"_$sStableFunc": "MyApp.stableFunc()"}
    apply_demangled_names(analysis, lookup)
    assert analysis["functions"]["unchanged"][0]["demangled_name"] == "MyApp.stableFunc()"


def test_apply_demangled_names_missing_key_uses_mangled():
    """If a symbol is absent from the lookup, demangled_name falls back to mangled_name."""
    analysis = _make_component_analysis()
    apply_demangled_names(analysis, {})  # empty lookup
    assert analysis["functions"]["added"][0]["demangled_name"] == "_$sAddedFunc"


def test_build_report_top_level_keys():
    component_results = {
        "MyApp.app/MyApp": ("main_executable", _make_component_analysis()),
    }
    metadata = {"app_name": "MyApp", "old_archive": "/old", "new_archive": "/new", "generated_at": "2026-01-01T00:00:00"}
    report = build_report(
        metadata=metadata,
        component_results=component_results,
        components_only_in_old=["OldOnly.framework"],
        components_only_in_new=["NewOnly.framework"],
        analysis_warnings=["some warning"],
        demangle_lookup={},
    )
    assert "metadata" in report
    assert "components" in report
    assert "components_only_in_old" in report
    assert "components_only_in_new" in report
    assert "analysis_warnings" in report


def test_build_report_component_structure():
    component_results = {
        "MyApp.app/MyApp": ("main_executable", _make_component_analysis()),
    }
    report = build_report(
        metadata={"app_name": "MyApp", "old_archive": "/old", "new_archive": "/new", "generated_at": "t"},
        component_results=component_results,
        components_only_in_old=[],
        components_only_in_new=[],
        analysis_warnings=[],
        demangle_lookup={},
    )
    comp = report["components"]["MyApp.app/MyApp"]
    assert comp["type"] == "main_executable"
    assert comp["relative_path"] == "MyApp.app/MyApp"
    assert comp["architecture"] == "arm64"
    assert "segments" in comp
    assert "sections" in comp
    assert "categories" in comp
    assert "functions" in comp


def test_build_report_skips_failed_components():
    """Components where analyze_component returned None are excluded from output."""
    component_results = {
        "MyApp.app/MyApp": ("main_executable", _make_component_analysis()),
        "Frameworks/Bad.framework/Bad": ("framework", None),
    }
    report = build_report(
        metadata={"app_name": "MyApp", "old_archive": "/old", "new_archive": "/new", "generated_at": "t"},
        component_results=component_results,
        components_only_in_old=[],
        components_only_in_new=[],
        analysis_warnings=[],
        demangle_lookup={},
    )
    assert "MyApp.app/MyApp" in report["components"]
    assert "Frameworks/Bad.framework/Bad" not in report["components"]


def test_write_report_creates_directory(tmp_path):
    output_path = tmp_path / "nested" / "dir" / "report.json"
    report = {"metadata": {}, "components": {}}
    write_report(report, output_path)
    assert output_path.exists()


def test_write_report_valid_json(tmp_path):
    output_path = tmp_path / "report.json"
    report = {"metadata": {"app_name": "MyApp"}, "components": {}}
    write_report(report, output_path)
    with open(output_path) as f:
        parsed = json.load(f)
    assert parsed["metadata"]["app_name"] == "MyApp"


def test_write_report_indented(tmp_path):
    output_path = tmp_path / "report.json"
    write_report({"a": 1}, output_path)
    raw = output_path.read_text()
    # indent=2 means newlines present
    assert "\n" in raw
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_report.py -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'oneapp_size_analysis.report'`

- [ ] **Step 3: Implement `report.py`**

```python
# oneapp_size_analysis/oneapp_size_analysis/report.py
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def apply_demangled_names(
    component_analysis: Dict[str, Any],
    lookup: Dict[str, str],
) -> None:
    """Mutate component_analysis in-place: add 'demangled_name' to every function entry.

    Falls back to the mangled name if the symbol is absent from the lookup dict.
    """
    funcs = component_analysis.get("functions", {})
    for bucket in ("added", "removed", "increased", "decreased", "unchanged"):
        for entry in funcs.get(bucket, []):
            mangled = entry["mangled_name"]
            entry["demangled_name"] = lookup.get(mangled, mangled)


def build_report(
    metadata: Dict[str, Any],
    component_results: Dict[str, Tuple[str, Optional[Dict]]],
    components_only_in_old: List[str],
    components_only_in_new: List[str],
    analysis_warnings: List[str],
    demangle_lookup: Dict[str, str],
) -> Dict[str, Any]:
    """Assemble the final JSON report dict.

    component_results maps relative_path_key → (component_type, analysis_dict_or_None).
    Components where the analysis dict is None (failed) are excluded from 'components'.
    """
    components_out: Dict[str, Any] = {}

    for rel_path, (comp_type, analysis) in component_results.items():
        if analysis is None:
            continue
        # Apply demangled names in-place (mutates the analysis dict)
        apply_demangled_names(analysis, demangle_lookup)
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


def write_report(report: Dict[str, Any], output_path: Path) -> None:
    """Write the report dict to output_path as pretty-printed JSON.
    Creates parent directories if they do not exist.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_report.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add oneapp_size_analysis/oneapp_size_analysis/report.py oneapp_size_analysis/tests/test_report.py
git commit -m "feat: add report.py — JSON assembly and file writing"
```

---

## Task 6: `main.py` — CLI Orchestration

**Files:**
- Create: `oneapp_size_analysis/oneapp_size_analysis/main.py`

`main.py` wires the pipeline: validate inputs → discover components → match → analyze → demangle → report → write.

- [ ] **Step 1: Implement `main.py`**

```python
# oneapp_size_analysis/oneapp_size_analysis/main.py
import argparse
import datetime
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from oneapp_size_analysis.archive import (
    ArchiveError,
    ComponentDescriptor,
    discover_components,
    validate_app_names,
)
from oneapp_size_analysis.analysis import analyze_component
from oneapp_size_analysis.demangle import demangle_symbols
from oneapp_size_analysis.report import build_report, write_report


def _check_tool(tool: str, install_hint: str) -> None:
    """Exit with a clear error if a required system tool is not found."""
    if shutil.which(tool) is None:
        # For xcrun tools, also try xcrun --find
        if tool == "xcrun":
            pass  # xcrun itself missing is checked below
        sys.exit(f"Error: '{tool}' not found. {install_hint}")


def _check_dependencies() -> None:
    if shutil.which("otool") is None:
        sys.exit(
            "Error: 'otool' not found.\n"
            "Install Xcode Command Line Tools: xcode-select --install"
        )
    try:
        subprocess.run(
            ["xcrun", "--find", "swift-demangle"],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit(
            "Error: 'swift-demangle' not found via xcrun.\n"
            "Install Xcode from the Mac App Store."
        )


def _match_components(
    old_components: List[ComponentDescriptor],
    new_components: List[ComponentDescriptor],
) -> Tuple[
    List[Tuple[ComponentDescriptor, ComponentDescriptor]],
    List[str],
    List[str],
]:
    """Match components by relative_path key. Return matched pairs and unmatched keys."""
    old_by_key = {c.relative_path: c for c in old_components}
    new_by_key = {c.relative_path: c for c in new_components}

    matched = []
    for key in sorted(old_by_key.keys() & new_by_key.keys()):
        matched.append((old_by_key[key], new_by_key[key]))

    only_in_old = sorted(old_by_key.keys() - new_by_key.keys())
    only_in_new = sorted(new_by_key.keys() - old_by_key.keys())
    return matched, only_in_old, only_in_new


def _collect_all_mangled_names(
    component_results: Dict[str, Tuple[str, Optional[dict]]],
) -> List[str]:
    """Extract every mangled symbol name from all component analysis results."""
    names = []
    for _, (_, analysis) in component_results.items():
        if analysis is None:
            continue
        funcs = analysis.get("functions", {})
        for bucket in ("added", "removed", "increased", "decreased", "unchanged"):
            for entry in funcs.get(bucket, []):
                names.append(entry["mangled_name"])
    return names


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="oneapp-size-analysis",
        description=(
            "Compare binary size between two XCArchive builds of an iOS application. "
            "Analyzes every Mach-O component (main executable, frameworks, extensions, "
            "Watch apps) and writes a detailed JSON report."
        ),
    )
    parser.add_argument(
        "old_archive",
        metavar="OLD.xcarchive",
        help="Path to the baseline XCArchive.",
    )
    parser.add_argument(
        "new_archive",
        metavar="NEW.xcarchive",
        help="Path to the new XCArchive to compare against.",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="PATH",
        help=(
            "Path for the JSON report. "
            "Defaults to ./analysis-reports/{AppName}-size-diff-{timestamp}.json"
        ),
    )
    args = parser.parse_args()

    _check_dependencies()

    old_path = Path(args.old_archive).resolve()
    new_path = Path(args.new_archive).resolve()

    for p in (old_path, new_path):
        if not p.is_dir():
            sys.exit(f"Error: XCArchive not found or is not a directory: {p}")

    warnings: List[str] = []

    # Discover all components
    try:
        old_components = discover_components(old_path, warnings=warnings)
    except ArchiveError as e:
        sys.exit(f"Error in {old_path.name}: {e}")

    try:
        new_components = discover_components(new_path, warnings=warnings)
    except ArchiveError as e:
        sys.exit(f"Error in {new_path.name}: {e}")

    # Build app name metadata
    app_name_meta = validate_app_names(old_path, new_path, warnings)
    app_name = app_name_meta.get("app_name") or app_name_meta.get("old_app_name", "app")

    # Match components
    matched, only_in_old, only_in_new = _match_components(old_components, new_components)

    if only_in_old:
        print(f"Components only in old archive: {', '.join(only_in_old)}", file=sys.stderr)
    if only_in_new:
        print(f"Components only in new archive: {', '.join(only_in_new)}", file=sys.stderr)

    # Analyze each matched pair
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

    # Collect and demangle all symbol names in one batch
    print("Demangling symbols ...", file=sys.stderr)
    all_names = _collect_all_mangled_names(component_results)
    demangle_lookup = demangle_symbols(all_names)

    # Build metadata
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    metadata = {
        "generated_at": datetime.datetime.now().isoformat(),
        "old_archive": str(old_path),
        "new_archive": str(new_path),
        **app_name_meta,
    }

    # Assemble report
    report = build_report(
        metadata=metadata,
        component_results=component_results,
        components_only_in_old=only_in_old,
        components_only_in_new=only_in_new,
        analysis_warnings=warnings,
        demangle_lookup=demangle_lookup,
    )

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_dir = Path("analysis-reports")
        output_path = output_dir / f"{app_name}-size-diff-{timestamp}.json"

    write_report(report, output_path)

    analyzed = sum(1 for _, (_, a) in component_results.items() if a is not None)
    print(f"Report written to: {output_path}", file=sys.stderr)
    print(f"Components analyzed: {analyzed}/{total}", file=sys.stderr)
    if warnings:
        print(f"Warnings: {len(warnings)} (see 'analysis_warnings' in report)", file=sys.stderr)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify CLI is accessible**

```bash
oneapp-size-analysis --help
```

Expected output:
```
usage: oneapp-size-analysis [-h] [--output PATH] OLD.xcarchive NEW.xcarchive

Compare binary size between two XCArchive builds ...
```

- [ ] **Step 3: Commit**

```bash
git add oneapp_size_analysis/oneapp_size_analysis/main.py
git commit -m "feat: add main.py — CLI entry point and pipeline orchestration"
```

---

## Task 7: Full Test Suite Pass and Smoke Test

**Goal:** Run all unit tests together, then do a quick sanity-check that the tool produces valid JSON output on a real binary.

- [ ] **Step 1: Run the full test suite**

```bash
cd oneapp_size_analysis
python -m pytest tests/ -v
```

Expected: all tests PASS, no errors.

- [ ] **Step 2: Smoke test with a real macOS binary**

We don't need a real XCArchive for a smoke test — we can call `analyze_component` directly on any Mach-O binary present on macOS (`/bin/ls` works):

```bash
python3 - <<'EOF'
import json
import collections
from oneapp_size_analysis.analysis import analyze_component

result = analyze_component("/bin/ls", "/bin/ls", warnings=[])
if result:
    print("Architecture:", result["architecture"])
    print("Sections:", list(result["sections"].keys())[:3])
    print("Categories with data:", [k for k, v in result["categories"].items() if v["old_bytes"] > 0])
    print("Function counts:", {k: len(v) for k, v in result["functions"].items() if isinstance(v, list)})
else:
    print("analyze_component returned None — check warnings")
EOF
```

Expected: prints architecture (`arm64` on Apple Silicon), some section names, some categories, and function counts.

- [ ] **Step 3: Smoke test demangling with a real Swift binary (if available)**

```bash
python3 - <<'EOF'
from oneapp_size_analysis.demangle import demangle_symbols
# Test with a known Swift mangled name
result = demangle_symbols(["_$sSS1poiyS2S_SStFZ"])
print(result)
# Expected: {'_$sSS1poiyS2S_SStFZ': 'static Swift.String.+ infix(Swift.String, Swift.String) -> Swift.String'}
EOF
```

Expected: prints a dict with a human-readable demangled name.

- [ ] **Step 4: Commit**

```bash
git add oneapp_size_analysis/
git commit -m "test: confirm full test suite passes and smoke tests succeed"
```

---

## Quick Reference

**Install:**
```bash
pip install -e ./cmpcodesize && pip install -e ./oneapp_size_analysis
```

**Run:**
```bash
oneapp-size-analysis OldApp.xcarchive NewApp.xcarchive
oneapp-size-analysis OldApp.xcarchive NewApp.xcarchive --output my-report.json
```

**Test:**
```bash
cd oneapp_size_analysis && python -m pytest tests/ -v
```
