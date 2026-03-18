import plistlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


class ArchiveError(Exception):
    pass


@dataclass
class ComponentDescriptor:
    relative_path: str
    absolute_path: Path
    component_type: str  # "main_executable" | "framework" | "extension" | "watch_app"


def _read_executable(bundle_path: Path, warnings: List[str]) -> Optional[str]:
    """Read CFBundleExecutable from a bundle's Info.plist. Returns None and appends a
    warning if the key is absent or the plist is unreadable."""
    plist_path = bundle_path / "Info.plist"
    try:
        with open(plist_path, "rb") as f:
            info = plistlib.load(f)
        name = info.get("CFBundleExecutable")
        if name is None:
            warnings.append(
                f"Warning: CFBundleExecutable missing in {plist_path} — skipping bundle"
            )
        return name
    except Exception as exc:
        warnings.append(f"Warning: Could not read {plist_path}: {exc} — skipping bundle")
        return None


def _collect_frameworks(
    bundle_path: Path,
    parent_rel: str,
    warnings: List[str],
) -> List[ComponentDescriptor]:
    """Collect all framework binaries directly inside {bundle_path}/Frameworks/."""
    results = []
    fw_dir = bundle_path / "Frameworks"
    if not fw_dir.is_dir():
        return results
    for fw in sorted(fw_dir.glob("*.framework")):
        name = _read_executable(fw, warnings)
        if name is None:
            continue
        binary = fw / name
        rel = f"{parent_rel}/Frameworks/{fw.name}/{name}"
        results.append(ComponentDescriptor(rel, binary, "framework"))
    return results


def _collect_extensions(
    bundle_path: Path,
    parent_rel: str,
    warnings: List[str],
) -> List[ComponentDescriptor]:
    """Collect all extension binaries directly inside {bundle_path}/PlugIns/,
    plus their nested frameworks."""
    results = []
    plugins_dir = bundle_path / "PlugIns"
    if not plugins_dir.is_dir():
        return results
    for ext in sorted(plugins_dir.glob("*.appex")):
        name = _read_executable(ext, warnings)
        if name is None:
            continue
        binary = ext / name
        rel = f"{parent_rel}/PlugIns/{ext.name}/{name}"
        results.append(ComponentDescriptor(rel, binary, "extension"))
        # Frameworks nested inside this extension
        results.extend(_collect_frameworks(ext, f"{parent_rel}/PlugIns/{ext.name}", warnings))
    return results


def discover_components(
    archive_path: Path,
    warnings: Optional[List[str]] = None,
) -> List[ComponentDescriptor]:
    """Traverse an XCArchive and return all Mach-O component descriptors.

    Raises ArchiveError if the archive has no .app or more than one .app.
    Malformed bundles (missing CFBundleExecutable) emit warnings and are skipped.
    """
    if warnings is None:
        warnings = []

    apps_dir = archive_path / "Products" / "Applications"
    app_bundles = sorted(apps_dir.glob("*.app"))

    if len(app_bundles) == 0:
        raise ArchiveError(f"No .app bundle found in {apps_dir}")
    if len(app_bundles) > 1:
        raise ArchiveError(
            f"More than one .app bundle found in {apps_dir}: "
            + ", ".join(b.name for b in app_bundles)
        )

    app = app_bundles[0]
    results: List[ComponentDescriptor] = []

    # 1. Main executable
    main_name = _read_executable(app, warnings)
    if main_name is not None:
        app_rel = f"{app.name}/{main_name}"
        results.append(ComponentDescriptor(app_rel, app / main_name, "main_executable"))

    # 2. Main app frameworks
    app_bundle_rel = app.name
    results.extend(_collect_frameworks(app, app_bundle_rel, warnings))

    # 3. Main app extensions (+ their frameworks)
    results.extend(_collect_extensions(app, app_bundle_rel, warnings))

    # 4. Watch apps (+ their frameworks and extensions)
    watch_dir = app / "Watch"
    if watch_dir.is_dir():
        for watch in sorted(watch_dir.glob("*.app")):
            wname = _read_executable(watch, warnings)
            if wname is None:
                continue
            watch_bundle_rel = f"{app_bundle_rel}/Watch/{watch.name}"
            watch_rel = f"{watch_bundle_rel}/{wname}"
            results.append(ComponentDescriptor(watch_rel, watch / wname, "watch_app"))
            # Watch frameworks
            results.extend(_collect_frameworks(watch, watch_bundle_rel, warnings))
            # Watch extensions (+ their frameworks)
            results.extend(_collect_extensions(watch, watch_bundle_rel, warnings))

    return results


def validate_app_names(
    old_archive: Path,
    new_archive: Path,
    warnings: List[str],
) -> dict:
    """Compare CFBundleExecutable from both archives' main app.
    Returns a metadata dict with either 'app_name' or 'old_app_name'/'new_app_name'."""
    def _get_name(archive: Path) -> Optional[str]:
        apps_dir = archive / "Products" / "Applications"
        bundles = list(apps_dir.glob("*.app"))
        if not bundles:
            return None
        w: List[str] = []
        return _read_executable(bundles[0], w)

    old_name = _get_name(old_archive)
    new_name = _get_name(new_archive)

    if old_name and new_name and old_name == new_name:
        return {"app_name": old_name}
    else:
        if old_name != new_name:
            warnings.append(
                f"Warning: app names differ between archives: '{old_name}' vs '{new_name}'"
            )
        return {"old_app_name": old_name or "unknown", "new_app_name": new_name or "unknown"}
