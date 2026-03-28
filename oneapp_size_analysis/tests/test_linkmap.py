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
