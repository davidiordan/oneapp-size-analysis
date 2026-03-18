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


