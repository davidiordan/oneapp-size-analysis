# oneapp_size_analysis/oneapp_size_analysis/linkmap.py
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class SymbolEntry:
    library: str
    source_file: str
    linker_bytes: int
    in_text: bool


@dataclass
class LibraryEntry:
    total_bytes: int
    text_bytes: int
    symbol_count: int


@dataclass
class LinkMapData:
    symbols: Dict[str, SymbolEntry]
    libraries: Dict[str, LibraryEntry]
    total_text_bytes: int


def _extract_library_name(file_index: int, path: str) -> str:
    """Extract a human-readable library name from an object file path."""
    if file_index == 0:
        return "Linker Synthesized"
    parts = path.replace("\\", "/").split("/")
    # Rule 2: *.build component
    for part in parts:
        if part.endswith(".build"):
            return part[: -len(".build")]
    # Rule 3: *.framework component
    for part in parts:
        if part.endswith(".framework"):
            return part[: -len(".framework")]
    # Rule 4: filename stem
    filename = parts[-1]
    if filename.endswith(".o"):
        return filename[:-2]
    return filename


def _parse_object_files(lines: List[str]) -> Dict[int, Tuple[str, str, str]]:
    """Parse # Object files: lines. Returns {index: (full_path, library_name, source_file)}."""
    result: Dict[int, Tuple[str, str, str]] = {}
    pattern = re.compile(r"^\[\s*(\d+)\]\s+(.+)$")
    for line in lines:
        m = pattern.match(line.strip())
        if not m:
            continue
        idx = int(m.group(1))
        path = m.group(2).strip()
        library = _extract_library_name(idx, path)
        source_file = os.path.basename(path)
        result[idx] = (path, library, source_file)
    return result


def _parse_sections(lines: List[str]) -> List[Tuple[int, int, str, str]]:
    """Parse # Sections: lines. Returns [(start_addr, size, segment, section_name)]."""
    result: List[Tuple[int, int, str, str]] = []
    pattern = re.compile(r"^0x([0-9A-Fa-f]+)\s+0x([0-9A-Fa-f]+)\s+(\S+)\s+(\S+)$")
    for line in lines:
        m = pattern.match(line.strip())
        if not m:
            continue
        addr = int(m.group(1), 16)
        size = int(m.group(2), 16)
        segment = m.group(3)
        section = m.group(4)
        result.append((addr, size, segment, section))
    return result


def _in_text_section(addr: int, sections: List[Tuple[int, int, str, str]]) -> bool:
    """Return True if addr falls within a __TEXT __text section range."""
    for start, size, segment, section in sections:
        if segment == "__TEXT" and section == "__text":
            if start <= addr < start + size:
                return True
    return False


def _parse_symbols(
    lines: List[str],
    object_files: Dict[int, Tuple[str, str, str]],
    sections: List[Tuple[int, int, str, str]],
    warnings: List[str],
) -> Dict[str, SymbolEntry]:
    """Parse # Symbols: lines into a symbol_name → SymbolEntry dict."""
    result: Dict[str, SymbolEntry] = {}
    pattern = re.compile(r"^0x([0-9A-Fa-f]+)\s+0x([0-9A-Fa-f]+)\s+\[\s*(\d+)\]\s+(.+)$")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        m = pattern.match(stripped)
        if not m:
            continue
        addr = int(m.group(1), 16)
        size = int(m.group(2), 16)
        file_idx = int(m.group(3))
        name = m.group(4).strip()

        if name in result:
            warnings.append(
                f"Warning: duplicate symbol in link map, keeping first: {name}"
            )
            continue

        file_info = object_files.get(
            file_idx, (f"[{file_idx}]", "Unknown", f"[{file_idx}]")
        )
        _, library, source_file = file_info
        result[name] = SymbolEntry(
            library=library,
            source_file=source_file,
            linker_bytes=size,
            in_text=_in_text_section(addr, sections),
        )
    return result


def _build_libraries(
    symbols: Dict[str, SymbolEntry],
) -> Tuple[Dict[str, LibraryEntry], int]:
    """Aggregate symbol entries into per-library totals. Returns (libraries, total_text_bytes)."""
    libs: Dict[str, LibraryEntry] = {}
    total_text = 0
    for sym in symbols.values():
        if sym.library not in libs:
            libs[sym.library] = LibraryEntry(
                total_bytes=0, text_bytes=0, symbol_count=0
            )
        entry = libs[sym.library]
        entry.total_bytes += sym.linker_bytes
        entry.symbol_count += 1
        if sym.in_text:
            entry.text_bytes += sym.linker_bytes
            total_text += sym.linker_bytes
    return libs, total_text


def parse_link_map(path: str, warnings: List[str]) -> Optional[LinkMapData]:
    """Parse a linker link map file. Returns None on failure, appending a warning."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as exc:
        warnings.append(f"Warning: could not read link map {path}: {exc}")
        return None

    obj_lines: List[str] = []
    sect_lines: List[str] = []
    sym_lines: List[str] = []
    current = None

    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "# Object files:":
            current = "obj"
            continue
        elif stripped == "# Sections:":
            current = "sect"
            continue
        elif stripped == "# Symbols:":
            current = "sym"
            continue
        if current == "obj":
            obj_lines.append(line)
        elif current == "sect":
            sect_lines.append(line)
        elif current == "sym":
            sym_lines.append(line)

    object_files = _parse_object_files(obj_lines)
    sections = _parse_sections(sect_lines)
    symbols = _parse_symbols(sym_lines, object_files, sections, warnings)
    libraries, total_text_bytes = _build_libraries(symbols)

    return LinkMapData(
        symbols=symbols,
        libraries=libraries,
        total_text_bytes=total_text_bytes,
    )
