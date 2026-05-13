#!/usr/bin/env python3
"""Remove AI-generation notice watermarks from PDFs and DOCX files.

For PDFs, the script removes matching footer text objects from page content
streams. For DOCX files, it removes matching short paragraphs from document,
header, and footer XML parts.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET


DEFAULT_PATTERNS = [
    r"由\s*AI\s*生成",
    r"AI\s*生成",
    r"内容\s*由\s*AI",
    r"谨慎\s*参考",
    r"仅供\s*参考",
]
# Hard cap for a removable notice. This keeps broad patterns such as "AI生成"
# from deleting long body paragraphs that happen to mention the same phrase.
MAX_NOTICE_TEXT_CHARS = 240
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W_P = f"{{{WORD_NS}}}p"
W_T = f"{{{WORD_NS}}}t"
DOCX_TEXT_PART_RE = re.compile(r"^word/(document|header\d*|footer\d*)\.xml$")

# Preserve familiar OOXML namespace prefixes when ElementTree serializes edited
# DOCX XML parts. Without registration it may emit generic ns0/ns1 prefixes,
# which is valid XML but makes diffs and manual review harder.
for prefix, uri in {
    "w": WORD_NS,
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
}.items():
    ET.register_namespace(prefix, uri)


def require_pypdf():
    """Import PDF dependencies only when PDF processing is requested.

    DOCX support uses only the standard library, so missing pypdf should not
    block DOCX cleanup.
    """
    try:
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import ContentStream
    except ImportError as exc:  # pragma: no cover - depends on host env
        raise SystemExit(
            "Missing dependency for PDF support: pypdf. Install it with `python3 -m pip install pypdf`."
        ) from exc
    return PdfReader, PdfWriter, ContentStream


def normalize_text(text: str) -> str:
    """Normalize extracted text before applying notice patterns.

    Browser/PDF exporters may split text with control bytes or compatibility
    glyphs, so matching works on a compact normalized form.
    """
    replacements = {
        "\x00": "",
        "\x01": "",
        "\u200b": "",
        "\u00a0": " ",
        "⽣": "生",
        "⼈": "人",
        "⽤": "用",
        "⼯": "工",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r"\s+", "", text)


def compile_patterns(extra_patterns: Iterable[str]) -> list[re.Pattern[str]]:
    """Compile default and caller-supplied notice patterns."""
    patterns = list(DEFAULT_PATTERNS) + list(extra_patterns)
    return [re.compile(pattern, re.IGNORECASE) for pattern in patterns]


def matches_notice(text: str, patterns: list[re.Pattern[str]]) -> bool:
    """Return whether a short normalized text fragment is an AI notice."""
    normalized = normalize_text(text)
    if not normalized or len(normalized) > MAX_NOTICE_TEXT_CHARS:
        return False
    return any(pattern.search(normalized) for pattern in patterns)


def collect_footer_text_objects(page, patterns: list[re.Pattern[str]], footer_ratio: float):
    """Return PDF text object indexes that look like footer AI notices.

    The y-coordinate test uses the page's extracted text span instead of the
    physical page box. Some Chromium/pdfcpu PDFs map text through large
    transforms, and a physical-page footer test can miss those documents.
    """
    object_text: dict[int, list[str]] = defaultdict(list)
    object_y: dict[int, list[float]] = defaultdict(list)
    current_text_object = -1
    op_index = -1

    def before(operator, operands, cm, tm):
        nonlocal current_text_object, op_index
        op_index += 1
        if operator == b"BT":
            current_text_object = op_index
        elif operator == b"ET":
            current_text_object = -1

    def text_visitor(text, cm, tm, font_dict, font_size):
        if current_text_object < 0 or not text:
            return
        object_text[current_text_object].append(text)
        try:
            x = float(tm[4])
            y = float(tm[5])
            a, b, c, d, e, f = [float(value) for value in cm]
            device_y = (b * x) + (d * y) + f
        except Exception:
            return
        object_y[current_text_object].append(device_y)

    page.extract_text(visitor_operand_before=before, visitor_text=text_visitor)

    all_y = [value for values in object_y.values() for value in values]
    if not all_y:
        return []
    min_page_y = min(all_y)
    max_page_y = max(all_y)
    footer_limit = min_page_y + ((max_page_y - min_page_y) * footer_ratio)
    matches = []
    for start_index, pieces in object_text.items():
        text = normalize_text("".join(pieces))
        if not text:
            continue
        ys = object_y.get(start_index, [])
        # Require the whole text object to stay inside the footer band. If only
        # one run is in the footer, deleting the entire BT...ET object could
        # remove unrelated body text emitted in the same object.
        if not ys or max(ys) > footer_limit:
            continue
        if matches_notice(text, patterns):
            matches.append(
                {
                    "start": start_index,
                    "text": text,
                    "min_y": min(ys),
                    "max_y": max(ys),
                }
            )
    return matches


def ranges_for_text_objects(operations, starts: set[int]) -> list[tuple[int, int]]:
    """Expand BT operation starts to inclusive BT...ET operation ranges."""
    ranges = []
    for start in sorted(starts):
        end = start
        depth = 0
        for index in range(start, len(operations)):
            operator = operations[index][1]
            if operator == b"BT":
                depth += 1
            elif operator == b"ET":
                depth -= 1
                if depth <= 0:
                    end = index
                    break
        ranges.append((start, end))
    return ranges


def remove_operation_ranges(operations, ranges: list[tuple[int, int]]):
    """Return content-stream operations after dropping selected ranges."""
    remove_indexes = set()
    for start, end in ranges:
        remove_indexes.update(range(start, end + 1))
    return [operation for index, operation in enumerate(operations) if index not in remove_indexes]


def replace_content_stream(page, reader, operations) -> None:
    """Replace a PDF page content stream with edited operations."""
    PdfReader, PdfWriter, ContentStream = require_pypdf()
    original = ContentStream(page.get_contents(), reader)
    original.operations = operations
    page.replace_contents(original)


def default_output_path(input_path: Path) -> Path:
    """Build the default sibling output path."""
    return input_path.with_name(f"{input_path.stem}.cleaned{input_path.suffix}")


def resolve_output_path(args, input_path: Path) -> Path:
    """Resolve and validate the output path before any write occurs."""
    output_path = Path(args.output).expanduser().resolve() if args.output else default_output_path(input_path)
    if output_path == input_path:
        raise SystemExit("Output path must differ from input path.")
    if not output_path.parent.exists():
        raise SystemExit(f"Output directory not found: {output_path.parent}")
    return output_path


def validate_output(input_path: Path, output_path: Path, patterns, footer_ratio: float) -> dict[str, int]:
    """Validate a cleaned PDF before publishing it to the requested path."""
    PdfReader, PdfWriter, ContentStream = require_pypdf()
    input_reader = PdfReader(str(input_path))
    output_reader = PdfReader(str(output_path))
    input_pages = len(input_reader.pages)
    output_pages = len(output_reader.pages)
    if output_pages != input_pages:
        raise RuntimeError(f"page count changed: input={input_pages}, output={output_pages}")

    remaining = []
    for page_number, page in enumerate(output_reader.pages, start=1):
        matches = collect_footer_text_objects(page, patterns, footer_ratio)
        if matches:
            remaining.append(str(page_number))
    if remaining:
        raise RuntimeError(f"matching footer notices remain on pages: {', '.join(remaining)}")

    return {"pages": output_pages}


def write_temp_pdf(writer, output_path: Path) -> Path:
    """Write PDF output to a same-directory temp file for safe replacement."""
    with tempfile.NamedTemporaryFile(
        dir=output_path.parent,
        prefix=f".{output_path.stem}.",
        suffix=".tmp.pdf",
        delete=False,
    ) as handle:
        tmp_path = Path(handle.name)
        writer.write(handle)
    return tmp_path


def paragraph_text(paragraph: ET.Element) -> str:
    """Collect all text runs inside a DOCX paragraph."""
    return "".join(text_node.text or "" for text_node in paragraph.iter(W_T))


def remove_matching_docx_paragraphs(xml_bytes: bytes, patterns: list[re.Pattern[str]], part_name: str):
    """Remove matching short notice paragraphs from one DOCX XML part."""
    root = ET.fromstring(xml_bytes)
    matches = []

    for parent in root.iter():
        children = list(parent)
        remove_indexes = []
        for index, child in enumerate(children):
            if child.tag != W_P:
                continue
            text = paragraph_text(child)
            if matches_notice(text, patterns):
                remove_indexes.append(index)
                matches.append({"part": part_name, "text": normalize_text(text)})
        for index in reversed(remove_indexes):
            parent.remove(children[index])

    return ET.tostring(root, encoding="utf-8", xml_declaration=True), matches


def collect_docx_matches(input_path: Path, patterns: list[re.Pattern[str]]):
    """Find matching notice paragraphs in editable DOCX text parts."""
    matches = []
    with zipfile.ZipFile(input_path) as zin:
        for info in zin.infolist():
            if not DOCX_TEXT_PART_RE.match(info.filename):
                continue
            try:
                root = ET.fromstring(zin.read(info.filename))
            except ET.ParseError:
                continue
            for paragraph in root.iter(W_P):
                text = paragraph_text(paragraph)
                if matches_notice(text, patterns):
                    matches.append({"part": info.filename, "text": normalize_text(text)})
    return matches


def write_temp_docx(input_path: Path, output_path: Path, patterns: list[re.Pattern[str]]):
    """Rewrite a DOCX package with matching notice paragraphs removed.

    Only document/header/footer XML parts are edited; all other package entries
    are copied through unchanged.
    """
    changed_parts = []
    all_matches = []
    with tempfile.NamedTemporaryFile(
        dir=output_path.parent,
        prefix=f".{output_path.stem}.",
        suffix=".tmp.docx",
        delete=False,
    ) as handle:
        tmp_path = Path(handle.name)

    try:
        with zipfile.ZipFile(input_path) as zin, zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if DOCX_TEXT_PART_RE.match(info.filename):
                    data, matches = remove_matching_docx_paragraphs(data, patterns, info.filename)
                    if matches:
                        changed_parts.append(info.filename)
                        all_matches.extend(matches)
                zout.writestr(info, data)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return tmp_path, changed_parts, all_matches


def validate_docx_output(input_path: Path, output_path: Path, patterns) -> dict[str, int]:
    """Validate cleaned DOCX structure and remaining notice matches."""
    with zipfile.ZipFile(input_path) as zin, zipfile.ZipFile(output_path) as zout:
        input_names = sorted(info.filename for info in zin.infolist())
        output_names = sorted(info.filename for info in zout.infolist())
    if input_names != output_names:
        raise RuntimeError("DOCX package entries changed unexpectedly")

    remaining = collect_docx_matches(output_path, patterns)
    if remaining:
        parts = ", ".join(sorted({match["part"] for match in remaining}))
        raise RuntimeError(f"matching notices remain in: {parts}")

    return {"parts": len(output_names)}


def process_docx(args) -> int:
    """Process a DOCX input file."""
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise SystemExit(f"Input DOCX not found: {input_path}")
    output_path = resolve_output_path(args, input_path)
    patterns = compile_patterns(args.pattern)

    matches = collect_docx_matches(input_path, patterns)
    if args.dry_run:
        print_docx_report(input_path, None, matches, [], None)
        return 0
    if not matches and not args.force:
        raise SystemExit("No matching AI watermark notice was found. Use --pattern if appropriate.")

    tmp_path, changed_parts, all_matches = write_temp_docx(input_path, output_path, patterns)
    try:
        validation = validate_docx_output(input_path, tmp_path, patterns)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise SystemExit(f"Validation failed: {exc}") from exc
    tmp_path.replace(output_path)

    print_docx_report(input_path, output_path, all_matches, changed_parts, validation)
    return 0


def process_pdf(args) -> int:
    """Process a PDF input file."""
    PdfReader, PdfWriter, ContentStream = require_pypdf()
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise SystemExit(f"Input PDF not found: {input_path}")
    output_path = resolve_output_path(args, input_path)

    patterns = compile_patterns(args.pattern)
    writer = PdfWriter(clone_from=str(input_path))

    changed_pages = []
    all_matches = []

    for page_number, page in enumerate(writer.pages, start=1):
        matches = collect_footer_text_objects(page, patterns, args.footer_ratio)
        if matches:
            all_matches.append((page_number, matches))
            if not args.dry_run:
                content = ContentStream(page.get_contents(), writer)
                ranges = ranges_for_text_objects(content.operations, {match["start"] for match in matches})
                new_operations = remove_operation_ranges(content.operations, ranges)
                replace_content_stream(page, writer, new_operations)
                changed_pages.append(page_number)

    if args.dry_run:
        print_report(input_path, None, all_matches, [])
        return 0

    if not changed_pages and not args.force:
        raise SystemExit(
            "No matching footer AI watermark text was found. "
            "Use --pattern or --footer-ratio if appropriate."
        )

    tmp_path = write_temp_pdf(writer, output_path)
    try:
        validation = validate_output(input_path, tmp_path, patterns, args.footer_ratio)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise SystemExit(f"Validation failed: {exc}") from exc
    tmp_path.replace(output_path)

    print_report(input_path, output_path, all_matches, changed_pages, validation)
    return 0


def print_report(
    input_path: Path,
    output_path: Path | None,
    all_matches,
    changed_pages: list[int],
    validation: dict[str, int] | None = None,
) -> None:
    """Print a PDF processing report."""
    print(f"Input: {input_path}")
    if output_path:
        print(f"Output: {output_path}")
    if not all_matches:
        print("Matches: none")
    else:
        print("Matches:")
        for page_number, matches in all_matches:
            for match in matches:
                print(
                    f"  page {page_number}: y={match['min_y']:.1f}-{match['max_y']:.1f} "
                    f"text={match['text']!r}"
                )
    if changed_pages:
        pages = ", ".join(str(page) for page in changed_pages)
        print(f"Changed pages: {pages}")
    if validation:
        print(f"Validation: ok ({validation['pages']} pages, no remaining matches)")


def print_docx_report(
    input_path: Path,
    output_path: Path | None,
    matches,
    changed_parts: list[str],
    validation: dict[str, int] | None,
) -> None:
    """Print a DOCX processing report."""
    print(f"Input: {input_path}")
    if output_path:
        print(f"Output: {output_path}")
    if not matches:
        print("Matches: none")
    else:
        print("Matches:")
        for match in matches:
            print(f"  {match['part']}: text={match['text']!r}")
    if changed_parts:
        print("Changed parts: " + ", ".join(sorted(set(changed_parts))))
    if validation:
        print(f"Validation: ok ({validation['parts']} package entries, no remaining matches)")


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Input PDF or DOCX path.")
    parser.add_argument("--output", "-o", help="Output path. Defaults to <name>.cleaned.<ext>.")
    parser.add_argument("--pattern", action="append", default=[], help="Additional regex pattern to match.")
    parser.add_argument(
        "--footer-ratio",
        type=float,
        default=0.16,
        help="Bottom page fraction considered footer. Default: 0.16.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report matches without writing a PDF.")
    parser.add_argument("--force", action="store_true", help="Write a copy even when no matches are found.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch processing by input file extension."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.footer_ratio <= 0 or args.footer_ratio > 0.5:
        parser.error("--footer-ratio must be > 0 and <= 0.5")
    suffix = Path(args.input).suffix.lower()
    if suffix == ".pdf":
        return process_pdf(args)
    if suffix == ".docx":
        return process_docx(args)
    parser.error("input must be a .pdf or .docx file")


if __name__ == "__main__":
    sys.exit(main())
