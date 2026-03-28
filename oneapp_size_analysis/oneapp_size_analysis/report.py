# oneapp_size_analysis/oneapp_size_analysis/report.py
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from oneapp_size_analysis.linkmap import LinkMapData
from oneapp_size_analysis.analysis import fmt_percent, fmt_percent_of_text


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


def build_single_archive_report(
    metadata: Dict[str, Any],
    component_results: Dict[str, Tuple[str, Optional[Dict]]],
    analysis_warnings: List[str],
    demangle_lookup: Dict[str, str],
) -> Dict[str, Any]:
    """Assemble the JSON report dict for single-archive (list) mode.

    component_results maps relative_path_key → (component_type, analysis_dict_or_None).
    Components where the analysis dict is None (failed) are excluded from 'components'.
    """
    components_out: Dict[str, Any] = {}

    for rel_path, (comp_type, analysis) in component_results.items():
        if analysis is None:
            continue
        # Apply demangled names to the flat functions list in-place
        for entry in analysis.get("functions", []):
            mangled = entry["mangled_name"]
            entry["demangled_name"] = demangle_lookup.get(mangled, mangled)
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


def write_report(report: Dict[str, Any], output_path: Path) -> None:
    """Write the report dict to output_path as pretty-printed JSON.
    Creates parent directories if they do not exist.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
