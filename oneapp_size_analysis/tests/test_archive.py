import plistlib
import sys
import tempfile
from pathlib import Path

import pytest

from oneapp_size_analysis.archive import (
    ComponentDescriptor,
    ArchiveError,
    discover_components,
)


def _make_bundle(parent: Path, bundle_name: str, executable: str) -> Path:
    """Create a minimal .app/.framework/.appex bundle with Info.plist and binary."""
    bundle = parent / bundle_name
    bundle.mkdir(parents=True, exist_ok=True)
    with open(bundle / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleExecutable": executable}, f)
    (bundle / executable).touch()
    return bundle


def _make_xcarchive(tmp: Path) -> Path:
    """Build a minimal XCArchive structure and return its path."""
    apps_dir = tmp / "MyApp.xcarchive" / "Products" / "Applications"
    apps_dir.mkdir(parents=True)
    return tmp / "MyApp.xcarchive"


def test_discover_main_executable(tmp_path):
    archive = _make_xcarchive(tmp_path)
    apps = archive / "Products" / "Applications"
    _make_bundle(apps, "MyApp.app", "MyApp")

    components = discover_components(archive)

    main = next(c for c in components if c.component_type == "main_executable")
    assert main.relative_path == "MyApp.app/MyApp"
    assert main.absolute_path == apps / "MyApp.app" / "MyApp"


def test_discover_framework(tmp_path):
    archive = _make_xcarchive(tmp_path)
    apps = archive / "Products" / "Applications"
    app = _make_bundle(apps, "MyApp.app", "MyApp")
    fw_dir = app / "Frameworks"
    fw_dir.mkdir()
    _make_bundle(fw_dir, "Foo.framework", "Foo")

    components = discover_components(archive)

    fw = next(c for c in components if c.component_type == "framework")
    assert fw.relative_path == "MyApp.app/Frameworks/Foo.framework/Foo"
    assert fw.absolute_path == fw_dir / "Foo.framework" / "Foo"


def test_discover_extension(tmp_path):
    archive = _make_xcarchive(tmp_path)
    apps = archive / "Products" / "Applications"
    app = _make_bundle(apps, "MyApp.app", "MyApp")
    ext_dir = app / "PlugIns"
    ext_dir.mkdir()
    _make_bundle(ext_dir, "MyExt.appex", "MyExt")

    components = discover_components(archive)

    ext = next(c for c in components if c.component_type == "extension")
    assert ext.relative_path == "MyApp.app/PlugIns/MyExt.appex/MyExt"


def test_discover_framework_inside_extension(tmp_path):
    archive = _make_xcarchive(tmp_path)
    apps = archive / "Products" / "Applications"
    app = _make_bundle(apps, "MyApp.app", "MyApp")
    ext_dir = app / "PlugIns"
    ext_dir.mkdir()
    ext = _make_bundle(ext_dir, "MyExt.appex", "MyExt")
    nested_fw_dir = ext / "Frameworks"
    nested_fw_dir.mkdir()
    _make_bundle(nested_fw_dir, "Bar.framework", "Bar")

    components = discover_components(archive)

    nested_fw = next(
        c for c in components
        if c.component_type == "framework" and "PlugIns" in c.relative_path
    )
    assert nested_fw.relative_path == "MyApp.app/PlugIns/MyExt.appex/Frameworks/Bar.framework/Bar"


def test_discover_watch_app(tmp_path):
    archive = _make_xcarchive(tmp_path)
    apps = archive / "Products" / "Applications"
    app = _make_bundle(apps, "MyApp.app", "MyApp")
    watch_dir = app / "Watch"
    watch_dir.mkdir()
    _make_bundle(watch_dir, "MyWatch.app", "MyWatch")

    components = discover_components(archive)

    watch = next(c for c in components if c.component_type == "watch_app")
    assert watch.relative_path == "MyApp.app/Watch/MyWatch.app/MyWatch"


def test_discover_framework_inside_watch(tmp_path):
    archive = _make_xcarchive(tmp_path)
    apps = archive / "Products" / "Applications"
    app = _make_bundle(apps, "MyApp.app", "MyApp")
    watch_dir = app / "Watch"
    watch_dir.mkdir()
    watch = _make_bundle(watch_dir, "MyWatch.app", "MyWatch")
    fw_dir = watch / "Frameworks"
    fw_dir.mkdir()
    _make_bundle(fw_dir, "Baz.framework", "Baz")

    components = discover_components(archive)

    fw = next(
        c for c in components
        if c.component_type == "framework" and "Watch" in c.relative_path
    )
    assert fw.relative_path == "MyApp.app/Watch/MyWatch.app/Frameworks/Baz.framework/Baz"


def test_error_no_app(tmp_path):
    archive = _make_xcarchive(tmp_path)
    with pytest.raises(ArchiveError, match="No .app bundle"):
        discover_components(archive)


def test_error_multiple_apps(tmp_path):
    archive = _make_xcarchive(tmp_path)
    apps = archive / "Products" / "Applications"
    _make_bundle(apps, "First.app", "First")
    _make_bundle(apps, "Second.app", "Second")
    with pytest.raises(ArchiveError, match="More than one"):
        discover_components(archive)


def test_missing_cfbundleexecutable_skipped(tmp_path, capsys):
    archive = _make_xcarchive(tmp_path)
    apps = archive / "Products" / "Applications"
    app = apps / "MyApp.app"
    app.mkdir()
    # Info.plist without CFBundleExecutable
    with open(app / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleName": "MyApp"}, f)

    warnings = []
    components = discover_components(archive, warnings=warnings)

    assert not any(c.component_type == "main_executable" for c in components)
    assert any("CFBundleExecutable" in w for w in warnings)
