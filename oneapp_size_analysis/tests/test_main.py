# oneapp_size_analysis/tests/test_main.py
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from oneapp_size_analysis.main import main


def _make_minimal_archive(tmp_path: Path, app_name: str = "MyApp") -> Path:
    """Create a minimal xcarchive directory structure for testing."""
    archive = tmp_path / f"{app_name}.xcarchive"
    app = archive / "Products" / "Applications" / f"{app_name}.app"
    app.mkdir(parents=True)
    plist = app / "Info.plist"
    plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>{app_name}</string>
</dict></plist>
""")
    binary = app / app_name
    binary.write_bytes(b"\x00")
    return archive


def _make_minimal_link_map(tmp_path: Path, name: str = "linkmap.txt") -> Path:
    """Write a minimal but valid link map file."""
    content = (
        "# Object files:\n"
        "[  0] linker synthesized\n"
        "# Sections:\n"
        "# Symbols:\n"
    )
    p = tmp_path / name
    p.write_text(content)
    return p


@patch("oneapp_size_analysis.main._check_dependencies")
@patch("oneapp_size_analysis.main.discover_components", return_value=[])
@patch("oneapp_size_analysis.main.validate_app_names", return_value={"app_name": "MyApp"})
@patch("oneapp_size_analysis.main.demangle_symbols", return_value={})
@patch("oneapp_size_analysis.main.build_single_archive_report", return_value={"metadata": {}, "components": {}, "analysis_warnings": []})
@patch("oneapp_size_analysis.main.write_report")
def test_list_mode_link_map_flag_accepted(
    mock_write, mock_report, mock_demangle, mock_validate, mock_discover, mock_check, tmp_path
):
    archive = _make_minimal_archive(tmp_path)
    lm = _make_minimal_link_map(tmp_path)
    sys.argv = ["oneapp-size-analysis", str(archive), "--link-map", str(lm)]
    main()
    # Verify build_single_archive_report was called with a link_map kwarg
    call_kwargs = mock_report.call_args.kwargs
    assert "link_map" in call_kwargs


@patch("oneapp_size_analysis.main._check_dependencies")
def test_list_mode_link_map_nonexistent_exits(mock_check, tmp_path):
    archive = _make_minimal_archive(tmp_path)
    sys.argv = ["oneapp-size-analysis", str(archive), "--link-map", "/does/not/exist.txt"]
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code != 0


@patch("oneapp_size_analysis.main._check_dependencies")
@patch("oneapp_size_analysis.main.discover_components", return_value=[])
@patch("oneapp_size_analysis.main.validate_app_names", return_value={"app_name": "MyApp"})
@patch("oneapp_size_analysis.main.demangle_symbols", return_value={})
@patch("oneapp_size_analysis.main.build_report", return_value={"metadata": {}, "components": {}, "components_only_in_old": [], "components_only_in_new": [], "analysis_warnings": []})
@patch("oneapp_size_analysis.main.write_report")
def test_diff_mode_link_map_flags_accepted(
    mock_write, mock_report, mock_demangle, mock_validate, mock_discover, mock_check, tmp_path
):
    old_archive = _make_minimal_archive(tmp_path / "old", "OldApp")
    new_archive = _make_minimal_archive(tmp_path / "new", "NewApp")
    old_lm = _make_minimal_link_map(tmp_path, "old-linkmap.txt")
    new_lm = _make_minimal_link_map(tmp_path, "new-linkmap.txt")
    sys.argv = [
        "oneapp-size-analysis",
        str(old_archive), str(new_archive),
        "--old-link-map", str(old_lm),
        "--new-link-map", str(new_lm),
    ]
    main()
    call_kwargs = mock_report.call_args.kwargs
    assert "link_map_old" in call_kwargs
    assert "link_map_new" in call_kwargs


@patch("oneapp_size_analysis.main._check_dependencies")
def test_diff_mode_old_link_map_nonexistent_exits(mock_check, tmp_path):
    old_archive = _make_minimal_archive(tmp_path / "old", "OldApp")
    new_archive = _make_minimal_archive(tmp_path / "new", "NewApp")
    sys.argv = [
        "oneapp-size-analysis",
        str(old_archive), str(new_archive),
        "--old-link-map", "/does/not/exist.txt",
    ]
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code != 0


@patch("oneapp_size_analysis.main._check_dependencies")
def test_diff_mode_new_link_map_nonexistent_exits(mock_check, tmp_path):
    old_archive = _make_minimal_archive(tmp_path / "old", "OldApp")
    new_archive = _make_minimal_archive(tmp_path / "new", "NewApp")
    new_lm = _make_minimal_link_map(tmp_path, "new-linkmap.txt")
    sys.argv = [
        "oneapp-size-analysis",
        str(old_archive), str(new_archive),
        "--new-link-map", "/does/not/exist.txt",
    ]
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code != 0
