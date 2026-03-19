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
from oneapp_size_analysis.analysis import analyze_component, list_component
from oneapp_size_analysis.demangle import demangle_symbols
from oneapp_size_analysis.report import build_report, build_single_archive_report, write_report


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


def _collect_all_mangled_names_diff(
    component_results: Dict[str, Tuple[str, Optional[dict]]],
) -> List[str]:
    """Extract every mangled symbol name from diff-mode component results."""
    names = []
    for _, (_, analysis) in component_results.items():
        if analysis is None:
            continue
        funcs = analysis.get("functions", {})
        for bucket in ("added", "removed", "increased", "decreased", "unchanged"):
            for entry in funcs.get(bucket, []):
                names.append(entry["mangled_name"])
    return names


def _collect_all_mangled_names_list(
    component_results: Dict[str, Tuple[str, Optional[dict]]],
) -> List[str]:
    """Extract every mangled symbol name from list-mode component results."""
    names = []
    for _, (_, analysis) in component_results.items():
        if analysis is None:
            continue
        for entry in analysis.get("functions", []):
            names.append(entry["mangled_name"])
    return names


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="oneapp-size-analysis",
        description=(
            "Analyze binary size of iOS XCArchive builds. "
            "Pass two archives to diff them; pass one to list all function sizes."
        ),
    )
    parser.add_argument(
        "old_archive",
        metavar="OLD.xcarchive",
        help="Path to the baseline XCArchive, or the only archive when listing sizes.",
    )
    parser.add_argument(
        "new_archive",
        metavar="NEW.xcarchive",
        nargs="?",
        default=None,
        help="Path to the new XCArchive to compare against. Omit to list sizes for OLD.xcarchive.",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="PATH",
        help=(
            "Path for the JSON report. "
            "Defaults to ./analysis-reports/{AppName}-size-diff-{timestamp}.json (diff mode) "
            "or ./analysis-reports/{AppName}-size-list-{timestamp}.json (list mode)."
        ),
    )
    args = parser.parse_args()

    _check_dependencies()

    old_path = Path(args.old_archive).resolve()
    if not old_path.is_dir():
        sys.exit(f"Error: XCArchive not found or is not a directory: {old_path}")

    if args.new_archive is None:
        _run_list_mode(old_path, args)
    else:
        new_path = Path(args.new_archive).resolve()
        if not new_path.is_dir():
            sys.exit(f"Error: XCArchive not found or is not a directory: {new_path}")
        _run_diff_mode(old_path, new_path, args)


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


if __name__ == "__main__":
    main()
