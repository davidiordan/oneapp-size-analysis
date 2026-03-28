# oneapp_size_analysis/tests/test_report.py
import json
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
