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
