# oneapp_size_analysis/tests/test_demangle.py
from unittest.mock import patch, MagicMock
import subprocess

from oneapp_size_analysis.demangle import demangle_symbols


def _mock_run(input_text: str, demangled_lines: list):
    """Return a mock subprocess.run result whose stdout matches demangled_lines."""
    mock = MagicMock()
    mock.stdout = "\n".join(demangled_lines) + "\n"
    return mock


def test_demangle_empty():
    result = demangle_symbols([])
    assert result == {}


def test_demangle_swift_symbols():
    symbols = ["_$s3FooBarV", "_$s3BazQuxC"]
    demangled = ["Foo.Bar", "Baz.Qux"]
    mock_result = MagicMock()
    mock_result.stdout = "\n".join(demangled) + "\n"

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        result = demangle_symbols(symbols)

    # subprocess.run called once with all symbols via stdin
    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args
    assert call_kwargs.kwargs["input"] == "\n".join(symbols)

    assert result["_$s3FooBarV"] == "Foo.Bar"
    assert result["_$s3BazQuxC"] == "Baz.Qux"


def test_demangle_non_swift_passthrough():
    symbols = ["-[NSObject init]", "__Z3foov"]
    mock_result = MagicMock()
    # swift-demangle echoes non-Swift symbols unchanged
    mock_result.stdout = "-[NSObject init]\n__Z3foov\n"

    with patch("subprocess.run", return_value=mock_result):
        result = demangle_symbols(symbols)

    assert result["-[NSObject init]"] == "-[NSObject init]"
    assert result["__Z3foov"] == "__Z3foov"


def test_demangle_deduplication():
    """Duplicate input symbols should be sent only once to swift-demangle."""
    symbols = ["_$s3FooBarV", "_$s3FooBarV", "_$s3FooBarV"]
    mock_result = MagicMock()
    mock_result.stdout = "Foo.Bar\n"

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        result = demangle_symbols(symbols)

    call_kwargs = mock_run.call_args
    # Only one unique symbol sent
    assert call_kwargs.kwargs["input"].strip().count("\n") == 0  # one line, no newlines within
    assert result["_$s3FooBarV"] == "Foo.Bar"


def test_demangle_preserves_all_original_keys():
    """Every input symbol appears as a key in the result, even after dedup."""
    symbols = ["_$s3FooBarV", "_$s3FooBarV"]
    mock_result = MagicMock()
    mock_result.stdout = "Foo.Bar\n"

    with patch("subprocess.run", return_value=mock_result):
        result = demangle_symbols(symbols)

    assert "_$s3FooBarV" in result
