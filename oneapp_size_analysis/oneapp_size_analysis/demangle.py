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
    seen: set[str] = set()
    unique_ordered = []
    for name in name_list:
        if name not in seen:
            seen.add(name)
            unique_ordered.append(name)

    result = subprocess.run(
        ["xcrun", "swift-demangle"],
        input="\n".join(unique_ordered),
        capture_output=True,
        text=True,
        check=True,
    )

    output_lines = result.stdout.splitlines()
    if len(output_lines) != len(unique_ordered):
        raise ValueError(
            f"swift-demangle returned {len(output_lines)} lines for "
            f"{len(unique_ordered)} input symbols; output may be corrupted"
        )
    lookup: Dict[str, str] = {}
    for original, demangled in zip(unique_ordered, output_lines):
        stripped = demangled.strip()
        lookup[original] = stripped if stripped else original

    return lookup
