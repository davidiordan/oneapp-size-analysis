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
