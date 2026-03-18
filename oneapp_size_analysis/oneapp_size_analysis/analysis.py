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
