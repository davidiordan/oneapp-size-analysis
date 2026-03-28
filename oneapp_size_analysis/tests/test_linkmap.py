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
    assert sym.in_text is True

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
    assert result["_duplicateSym"].library == "PluginA"
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
    assert plugin_a.text_bytes == 0xAC + 0x50
    assert plugin_a.symbol_count == 2

def test_parse_link_map_total_text_bytes(tmp_path):
    p = tmp_path / "linkmap.txt"
    p.write_text(_MINIMAL_LINK_MAP)
    warnings = []
    data = parse_link_map(str(p), warnings)
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
    assert data is not None
    assert len(data.symbols) == 0
    assert len(data.libraries) == 0
