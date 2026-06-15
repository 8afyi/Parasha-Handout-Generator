#!/usr/bin/env python3
"""Generate side-by-side parasha sheets as LibreOffice Writer documents and PDFs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree
from zipfile import ZIP_STORED, ZipFile, ZipInfo

import requests
from odf.opendocument import OpenDocumentText, load
from odf.style import (
    Header,
    HeaderFooterProperties,
    HeaderStyle,
    MasterPage,
    PageLayout,
    PageLayoutProperties,
    ParagraphProperties,
    Style,
    TableCellProperties,
    TableColumnProperties,
    TextProperties,
)
from odf.table import Table, TableCell, TableColumn, TableRow
from odf.text import H, P, PageCount, PageNumber, S, Span

from divine_names import divine_name_replacement_enabled, replace_divine_names


OUTPUT_DIR = Path("sheets")
CACHE_DIR = Path(".cache/parasha_generator")
TEMPLATE_PATH = Path("template.ott")
ODT_MIMETYPE = "application/vnd.oasis.opendocument.text"
MANIFEST_NS = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
STYLE_NS = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
PDF_CONVERTERS = ("auto", "libreoffice", "pandoc")
HEBCAL_CSV_URL = "https://www.hebcal.com/sedrot/fullkriyah-{year}.csv"
SEFARIA_TEXT_URL = "https://www.sefaria.org/api/texts/{ref}"
EN_VERSION = "The Koren Jerusalem Bible"
HE_VERSION = "Tanach with Nikkud"
DEFAULT_LANGUAGE_MODE = "bilingual"
DEFAULT_ENGLISH_VERSION_SLUG = "koren"
DEFAULT_HEBREW_VERSION_SLUG = "nikkud"
LANGUAGE_MODES = ("bilingual", "english", "hebrew")
NBSP = "\u00a0"
StyleRef = Any


@dataclass(frozen=True)
class TextVersion:
    slug: str
    label: str
    sefaria_title: str


ENGLISH_VERSIONS: tuple[TextVersion, ...] = (
    TextVersion("koren", "Koren", "The Koren Jerusalem Bible"),
    TextVersion("jps-2023", "JPS 2023", "THE JPS TANAKH: Gender-Sensitive Edition"),
    TextVersion("jps-1985", "JPS 1985", "Tanakh: The Holy Scriptures, published by JPS"),
    TextVersion("jps-1917", "JPS 1917", "The Holy Scriptures: A New Translation (JPS 1917)"),
)
HEBREW_VERSIONS: tuple[TextVersion, ...] = (
    TextVersion("nikkud", "Tanach with Nikkud", "Tanach with Nikkud"),
    TextVersion("taamim", "Tanach with Ta'amei Hamikra", "Tanach with Ta'amei Hamikra"),
    TextVersion("text-only", "Tanach with Text Only", "Tanach with Text Only"),
)
ENGLISH_VERSION_BY_SLUG = {version.slug: version for version in ENGLISH_VERSIONS}
HEBREW_VERSION_BY_SLUG = {version.slug: version for version in HEBREW_VERSIONS}


@dataclass(frozen=True)
class GenerationOptions:
    language_mode: str = DEFAULT_LANGUAGE_MODE
    english_version: str = DEFAULT_ENGLISH_VERSION_SLUG
    hebrew_version: str = DEFAULT_HEBREW_VERSION_SLUG
    replace_divine_names: bool = True

BOOK_NAMES = sorted(
    [
        "Genesis",
        "Exodus",
        "Leviticus",
        "Numbers",
        "Deuteronomy",
        "Joshua",
        "Judges",
        "I Samuel",
        "II Samuel",
        "I Kings",
        "II Kings",
        "Isaiah",
        "Jeremiah",
        "Ezekiel",
        "Hosea",
        "Joel",
        "Amos",
        "Obadiah",
        "Jonah",
        "Micah",
        "Nahum",
        "Habakkuk",
        "Zephaniah",
        "Haggai",
        "Zechariah",
        "Malachi",
        "Psalms",
        "Proverbs",
        "Job",
        "Song of Songs",
        "Ruth",
        "Lamentations",
        "Ecclesiastes",
        "Esther",
        "Daniel",
        "Ezra",
        "Nehemiah",
        "I Chronicles",
        "II Chronicles",
    ],
    key=len,
    reverse=True,
)


class HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def get_text(self) -> str:
        return "".join(self.parts)


@dataclass(frozen=True)
class ReadingRow:
    date: date
    parashah: str
    aliyah: str
    reading: str
    verses: str


@dataclass(frozen=True)
class Verse:
    ref: str
    english: str
    hebrew: str


@dataclass(frozen=True)
class RefParts:
    book: str
    chapter: str
    verse: str


def strip_html(value: Any) -> str:
    text = "" if value is None else str(value)
    parser = HTMLTextExtractor()
    parser.feed(text)
    parser.close()
    return re.sub(r"\s+", " ", parser.get_text()).strip()


def flatten_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [strip_html(value)]
    if isinstance(value, list):
        flattened: list[str] = []
        for item in value:
            flattened.extend(flatten_text(item))
        return flattened
    return [strip_html(value)]


def safe_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-").lower()
    return slug or "sheet"


def label_for_aliyah(aliyah: str) -> str:
    if aliyah.isdigit():
        return f"Aliyah {aliyah}"
    if aliyah == "maf":
        return "Maftir"
    return aliyah


def default_generation_options() -> GenerationOptions:
    return GenerationOptions(replace_divine_names=divine_name_replacement_enabled())


def validate_slug(value: str, choices: tuple[str, ...], label: str) -> None:
    if value not in choices:
        raise ValueError(f"{label} must be one of {', '.join(choices)}")


def normalize_generation_options(options: GenerationOptions | None = None) -> GenerationOptions:
    options = options or default_generation_options()
    validate_slug(options.language_mode, LANGUAGE_MODES, "language mode")
    validate_slug(options.english_version, tuple(ENGLISH_VERSION_BY_SLUG), "English version")
    validate_slug(options.hebrew_version, tuple(HEBREW_VERSION_BY_SLUG), "Hebrew version")

    english_version = options.english_version if uses_english(options) else DEFAULT_ENGLISH_VERSION_SLUG
    hebrew_version = options.hebrew_version if uses_hebrew(options) else DEFAULT_HEBREW_VERSION_SLUG

    return GenerationOptions(
        language_mode=options.language_mode,
        english_version=english_version,
        hebrew_version=hebrew_version,
        replace_divine_names=options.replace_divine_names,
    )


def uses_english(options: GenerationOptions) -> bool:
    return options.language_mode in {"bilingual", "english"}


def uses_hebrew(options: GenerationOptions) -> bool:
    return options.language_mode in {"bilingual", "hebrew"}


def english_version_title(options: GenerationOptions) -> str:
    return ENGLISH_VERSION_BY_SLUG[options.english_version].sefaria_title


def hebrew_version_title(options: GenerationOptions) -> str:
    return HEBREW_VERSION_BY_SLUG[options.hebrew_version].sefaria_title


def has_hebrew_output(options: GenerationOptions) -> bool:
    return uses_hebrew(options)


def source_summary(options: GenerationOptions) -> str:
    options = normalize_generation_options(options)
    source_parts = ["Hebcal Full Kriyah CSV, Diaspora."]
    sefaria_parts: list[str] = []
    if uses_english(options):
        sefaria_parts.append(f"English {english_version_title(options)}")
    if uses_hebrew(options):
        sefaria_parts.append(f"Hebrew {hebrew_version_title(options)}")
    if sefaria_parts:
        source_parts.append(f"Sefaria texts: {', '.join(sefaria_parts)}.")
    return " ".join(source_parts)


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%d-%b-%Y").date()


def read_from_cache_or_url(cache_path: Path, url: str, session: requests.Session) -> str:
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    response = session.get(url, timeout=30)
    response.raise_for_status()
    cache_path.write_text(response.text, encoding="utf-8")
    return response.text


def fetch_hebcal_rows(years: tuple[int, ...], session: requests.Session) -> list[ReadingRow]:
    rows: list[ReadingRow] = []
    for year in years:
        url = HEBCAL_CSV_URL.format(year=year)
        csv_text = read_from_cache_or_url(CACHE_DIR / f"fullkriyah-{year}.csv", url, session)
        for row in csv.DictReader(csv_text.splitlines()):
            if not row or not row.get("Date"):
                continue
            rows.append(
                ReadingRow(
                    date=parse_date(row["Date"]),
                    parashah=row["Parashah"],
                    aliyah=row["Aliyah"],
                    reading=row["Reading"],
                    verses=row["Verses"],
                )
            )
    return rows


def shabbat_for_week(input_date: date) -> date:
    return input_date + timedelta(days=(5 - input_date.weekday()) % 7)


def hebcal_years_for_date(input_date: date) -> tuple[int, ...]:
    return (input_date.year + 3760, input_date.year + 3761)


def is_sheet_reading(aliyah: str) -> bool:
    return aliyah.isdigit() or aliyah == "maf" or aliyah.startswith("Haftara")


def weekly_parasha_group(rows: list[ReadingRow], reading_date: date) -> tuple[str, list[ReadingRow]]:
    groups: OrderedDict[str, list[ReadingRow]] = OrderedDict()
    for row in sorted(rows, key=lambda item: (item.parashah, aliyah_sort_key(item.aliyah))):
        if row.date == reading_date and is_sheet_reading(row.aliyah):
            groups.setdefault(row.parashah, []).append(row)

    full_groups = [
        (parashah, group)
        for parashah, group in groups.items()
        if any(row.aliyah == "7" for row in group) and any(row.aliyah.startswith("Haftara") for row in group)
    ]
    if not full_groups:
        raise RuntimeError(f"No diaspora Torah and haftarah reading found for {reading_date.isoformat()}")
    if len(full_groups) > 1:
        names = ", ".join(parashah for parashah, _ in full_groups)
        raise RuntimeError(f"Multiple full readings found for {reading_date.isoformat()}: {names}")

    parashah, group = full_groups[0]
    return parashah, sorted(group, key=lambda row: aliyah_sort_key(row.aliyah))


def aliyah_sort_key(aliyah: str) -> tuple[int, str]:
    if aliyah.isdigit():
        return (int(aliyah), aliyah)
    if aliyah == "maf":
        return (8, aliyah)
    if aliyah.startswith("Haftara"):
        return (9, aliyah)
    return (10, aliyah)


def detect_book(ref: str) -> str | None:
    for book in BOOK_NAMES:
        if ref == book or ref.startswith(book + " "):
            return book
    return None


def split_reading_refs(reading: str) -> tuple[list[str], str | None]:
    base, _, note = reading.partition("|")
    refs: list[str] = []
    last_book: str | None = None
    for part in base.split(";"):
        part = part.strip()
        if not part:
            continue
        book = detect_book(part)
        if book:
            last_book = book
            refs.append(part)
        elif last_book:
            refs.append(f"{last_book} {part}")
        else:
            raise ValueError(f"Cannot infer book name for reading segment: {part!r}")
    return refs, note.strip() or None


def sefaria_path(ref: str) -> str:
    path = ref.replace(" ", "_").replace(":", ".")
    return quote(path, safe="-._")


def cache_key_for_ref(
    ref: str,
    english_version: str | None = None,
    hebrew_version: str | None = None,
) -> str:
    key_data = {
        "ref": ref,
        "return_format": "text_only",
    }
    if english_version is not None:
        key_data["ven"] = english_version
    if hebrew_version is not None:
        key_data["vhe"] = hebrew_version
    digest = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"{safe_slug(ref)}-{digest}.json"


def fetch_sefaria(
    ref: str,
    session: requests.Session,
    english_version: str | None = None,
    hebrew_version: str | None = None,
) -> dict[str, Any]:
    cache_path = CACHE_DIR / "sefaria" / cache_key_for_ref(ref, english_version, hebrew_version)
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    url = SEFARIA_TEXT_URL.format(ref=sefaria_path(ref))
    params: dict[str, Any] = {
        "context": 0,
        "return_format": "text_only",
    }
    if english_version is not None:
        params["ven"] = english_version
    if hebrew_version is not None:
        params["vhe"] = hebrew_version
    if english_version is not None and hebrew_version is not None:
        params["lang"] = "bi"
    elif english_version is not None:
        params["lang"] = "en"
    elif hebrew_version is not None:
        params["lang"] = "he"

    response = session.get(
        url,
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        raise RuntimeError(f"Sefaria returned an error for {ref}: {data['error']}")
    if english_version is not None and data.get("versionTitle") != english_version:
        raise RuntimeError(
            f"Sefaria did not return requested English version for {ref}: "
            f"{english_version!r} (got {data.get('versionTitle')!r})"
        )
    if hebrew_version is not None and data.get("heVersionTitle") != hebrew_version:
        raise RuntimeError(
            f"Sefaria did not return requested Hebrew version for {ref}: "
            f"{hebrew_version!r} (got {data.get('heVersionTitle')!r})"
        )
    cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def refs_from_range(ref: str, verse_count: int | None = None) -> list[str]:
    whole_chapter = re.match(r"^(?P<book>.+?) (?P<chapter>\d+)$", ref)
    if whole_chapter:
        if verse_count is None:
            raise ValueError(f"Need verse count to expand whole-chapter range: {ref}")
        book = whole_chapter.group("book")
        chapter = int(whole_chapter.group("chapter"))
        return [f"{book} {chapter}:{verse}" for verse in range(1, verse_count + 1)]

    match = re.match(
        r"^(?P<book>.+?) (?P<start_chapter>\d+):(?P<start_verse>\d+)(?:-(?:(?P<end_chapter>\d+):)?(?P<end_verse>\d+))?$",
        ref,
    )
    if not match:
        raise ValueError(f"Cannot parse Sefaria range: {ref}")

    book = match.group("book")
    start_chapter = int(match.group("start_chapter"))
    start_verse = int(match.group("start_verse"))
    end_chapter = int(match.group("end_chapter") or start_chapter)
    end_verse = int(match.group("end_verse") or start_verse)

    if start_chapter != end_chapter:
        raise ValueError(f"Use spanningRefs to expand multi-chapter range: {ref}")
    refs = [f"{book} {start_chapter}:{verse}" for verse in range(start_verse, end_verse + 1)]
    if verse_count is not None and len(refs) != verse_count:
        raise ValueError(f"Expanded {ref} to {len(refs)} refs, expected {verse_count}")
    return refs


def refs_from_sefaria_data(data: dict[str, Any], text_key: str = "text") -> list[str]:
    spanning_refs = data.get("spanningRefs") or []
    if spanning_refs:
        text_segments = data.get(text_key)
        if not isinstance(text_segments, list) or len(text_segments) != len(spanning_refs):
            segment_count = len(flatten_text(data.get(text_key)))
            return [data.get("ref", data.get("indexTitle", ""))] * segment_count
        refs: list[str] = []
        for spanning_ref, segment in zip(spanning_refs, text_segments):
            refs.extend(refs_from_range(spanning_ref, len(flatten_text(segment))))
        return refs

    index_title = data["indexTitle"]
    sections = data["sections"]
    to_sections = data["toSections"]
    ref = f"{index_title} {sections[0]}:{sections[1]}-{to_sections[0]}:{to_sections[1]}"
    refs = refs_from_range(ref)
    segment_count = len(flatten_text(data.get(text_key)))
    if len(refs) != segment_count:
        refs = [data.get("ref", index_title)] * segment_count
    return refs


def fetch_verses_for_reading(
    reading: str,
    session: requests.Session,
    options: GenerationOptions,
) -> tuple[list[Verse], str | None]:
    refs, note = split_reading_refs(reading)
    verses: list[Verse] = []
    request_english = uses_english(options)
    request_hebrew = uses_hebrew(options)
    requested_english_version = english_version_title(options) if request_english else None
    requested_hebrew_version = hebrew_version_title(options) if request_hebrew else None

    for ref in refs:
        data = fetch_sefaria(
            ref,
            session,
            requested_english_version,
            requested_hebrew_version,
        )
        english = flatten_text(data.get("text")) if request_english else []
        hebrew = (
            [
                replace_divine_names(text, enabled=options.replace_divine_names)
                for text in flatten_text(data.get("he"))
            ]
            if request_hebrew
            else []
        )
        if request_english and not english:
            raise RuntimeError(f"Sefaria returned no English text for {ref}")
        if request_hebrew and not hebrew:
            raise RuntimeError(f"Sefaria returned no Hebrew text for {ref}")
        if request_english and request_hebrew and len(english) != len(hebrew):
            raise RuntimeError(
                f"Sefaria returned mismatched English/Hebrew verse counts for {ref}: "
                f"{len(english)} English vs {len(hebrew)} Hebrew"
            )
        verse_count = len(english) if request_english else len(hebrew)
        ref_text_key = "text" if request_english else "he"
        verse_refs = refs_from_sefaria_data(data, ref_text_key)
        if len(verse_refs) != verse_count:
            raise RuntimeError(
                f"Could not align verse references for {ref}: {len(verse_refs)} refs vs {verse_count} verses"
            )
        verses.extend(
            Verse(
                ref=verse_ref,
                english=english[index] if request_english else "",
                hebrew=hebrew[index] if request_hebrew else "",
            )
            for index, verse_ref in enumerate(verse_refs)
        )
    return verses, note


def parse_ref_parts(ref: str) -> RefParts:
    match = re.match(r"^(?P<book>.+?) (?P<chapter>\d+):(?P<verse>\d+)$", ref)
    if not match:
        return RefParts(book="", chapter="", verse=ref)
    return RefParts(
        book=match.group("book"),
        chapter=match.group("chapter"),
        verse=match.group("verse"),
    )


def chapter_blocks(verses: list[Verse]) -> list[tuple[tuple[str, str], list[Verse]]]:
    blocks: list[tuple[tuple[str, str], list[Verse]]] = []
    current_key: tuple[str, str] | None = None
    for verse in verses:
        parts = parse_ref_parts(verse.ref)
        key = (parts.book, parts.chapter)
        if key != current_key:
            blocks.append((key, []))
            current_key = key
        blocks[-1][1].append(verse)
    return blocks


def template_styles() -> dict[str, StyleRef]:
    return {
        "Title": "P1",
        "NormalText": "P4",
        "SmallText": "P4",
        "SectionHeading": "Heading_20_2",
        "EnglishText": "P3",
        "HebrewText": "P2",
        "VerseNumber": "verseno",
        "ChapterNumber": None,
        "PageHeader": "Header",
        "Table": "Table1",
        "HebrewColumn": "Table1.A",
        "EnglishColumn": "Table1.B",
        "FullColumn": None,
        "TableRow": "Table1.1",
        "TextCell": "Table1.A1",
    }


def add_runtime_styles(doc: Any, styles: dict[str, StyleRef]) -> None:
    full_col = Style(name="GeneratedFullColumn", family="table-column")
    full_col.addElement(TableColumnProperties(columnwidth="6.9in"))
    doc.styles.addElement(full_col)
    styles["FullColumn"] = full_col


def clear_document_text(doc: Any) -> None:
    for child in list(doc.text.childNodes):
        doc.text.removeChild(child)


def create_document(template_path: Path) -> tuple[Any, dict[str, StyleRef], bool]:
    if template_path.exists():
        doc = load(str(template_path))
        clear_document_text(doc)
        doc.mimetype = ODT_MIMETYPE
        styles = template_styles()
        add_runtime_styles(doc, styles)
        return doc, styles, True

    doc = OpenDocumentText()
    return doc, add_styles(doc), False


def clean_manifest_xml(manifest_xml: bytes) -> bytes:
    ElementTree.register_namespace("manifest", MANIFEST_NS)
    full_path_attr = f"{{{MANIFEST_NS}}}full-path"
    media_type_attr = f"{{{MANIFEST_NS}}}media-type"
    file_entry_tag = f"{{{MANIFEST_NS}}}file-entry"

    root = ElementTree.fromstring(manifest_xml)
    kept_root_entry = False
    for child in list(root):
        if child.tag != file_entry_tag or child.get(full_path_attr) != "/":
            continue
        if kept_root_entry:
            root.remove(child)
            continue
        child.set(media_type_attr, ODT_MIMETYPE)
        kept_root_entry = True

    if not kept_root_entry:
        root.insert(
            0,
            ElementTree.Element(
                file_entry_tag,
                {
                    full_path_attr: "/",
                    media_type_attr: ODT_MIMETYPE,
                },
            ),
        )

    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def clean_template_output_package(output_path: Path) -> None:
    temp_path = output_path.with_name(f"{output_path.name}.tmp")
    with ZipFile(output_path, "r") as source, ZipFile(temp_path, "w") as target:
        mimetype_info = ZipInfo("mimetype")
        mimetype_info.compress_type = ZIP_STORED
        target.writestr(mimetype_info, ODT_MIMETYPE)

        seen = {"mimetype"}
        for info in source.infolist():
            if info.filename in seen:
                continue
            data = source.read(info.filename)
            if info.filename == "META-INF/manifest.xml":
                data = clean_manifest_xml(data)
            target.writestr(info, data)
            seen.add(info.filename)

    temp_path.replace(output_path)


class PdfConversionError(RuntimeError):
    pass


def conversion_output(stdout: str | None, stderr: str | None) -> str:
    return "\n".join(part.strip() for part in (stdout, stderr) if part and part.strip())


def run_conversion_command(command: list[str], env: dict[str, str] | None = None) -> None:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            env=env,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        raise PdfConversionError(f"PDF conversion timed out: {shlex.join(command)}") from exc

    if result.returncode != 0:
        output = conversion_output(result.stdout, result.stderr)
        detail = f"\n{output}" if output else ""
        raise PdfConversionError(f"PDF conversion failed: {shlex.join(command)}{detail}")


def convert_odt_to_pdf_with_pandoc(odt_path: Path, pdf_path: Path) -> Path:
    executable = shutil.which("pandoc")
    if executable is None:
        raise PdfConversionError("pandoc was not found on PATH")

    run_conversion_command([executable, str(odt_path), "-o", str(pdf_path)])
    if not pdf_path.exists():
        raise PdfConversionError(f"pandoc finished but did not create {pdf_path}")
    return pdf_path


def find_libreoffice_executable() -> str | None:
    for name in ("soffice", "libreoffice"):
        executable = shutil.which(name)
        if executable is not None:
            return executable

    candidates = [
        Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "LibreOffice/program/soffice.exe",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
        / "LibreOffice/program/soffice.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def convert_odt_to_pdf_with_libreoffice(odt_path: Path, pdf_path: Path) -> Path:
    executable = find_libreoffice_executable()
    if executable is None:
        raise PdfConversionError("LibreOffice was not found")

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    profile_dir = (CACHE_DIR / "libreoffice-profile").resolve()
    home_dir = (CACHE_DIR / "libreoffice-home").resolve()
    runtime_dir = (CACHE_DIR / "libreoffice-runtime").resolve()
    for directory in (profile_dir, home_dir, runtime_dir):
        directory.mkdir(parents=True, exist_ok=True)
    runtime_dir.chmod(0o700)

    expected_pdf_path = pdf_path.parent / f"{odt_path.stem}.pdf"
    command = [
        executable,
        "--headless",
        "--nologo",
        "--nofirststartwizard",
        f"-env:UserInstallation={profile_dir.as_uri()}",
        "--convert-to",
        "pdf:writer_pdf_Export",
        "--outdir",
        str(pdf_path.parent),
        str(odt_path),
    ]
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home_dir),
            "SAL_USE_VCLPLUGIN": "svp",
            "XDG_RUNTIME_DIR": str(runtime_dir),
        }
    )
    run_conversion_command(command, env=env)

    if not expected_pdf_path.exists():
        raise PdfConversionError(f"LibreOffice finished but did not create {expected_pdf_path}")
    if expected_pdf_path != pdf_path:
        expected_pdf_path.replace(pdf_path)
    return pdf_path


def convert_odt_to_pdf(odt_path: Path, converter: str) -> Path:
    pdf_path = odt_path.with_suffix(".pdf")
    if pdf_path.exists():
        pdf_path.unlink()

    converters = ("libreoffice", "pandoc") if converter == "auto" else (converter,)
    errors: list[str] = []
    for selected_converter in converters:
        if pdf_path.exists():
            pdf_path.unlink()
        try:
            if selected_converter == "libreoffice":
                return convert_odt_to_pdf_with_libreoffice(odt_path, pdf_path)
            if selected_converter == "pandoc":
                return convert_odt_to_pdf_with_pandoc(odt_path, pdf_path)
        except PdfConversionError as exc:
            errors.append(f"{selected_converter}: {exc}")

    detail = "\n\n".join(errors) if errors else f"unknown converter: {converter}"
    raise RuntimeError(f"Could not create PDF for {odt_path}:\n{detail}")


def add_styles(doc: OpenDocumentText) -> dict[str, StyleRef]:
    styles: dict[str, StyleRef] = {}

    def add(style: Style) -> Style:
        doc.styles.addElement(style)
        styles[style.getAttribute("name")] = style
        return style

    normal = add(Style(name="NormalText", family="paragraph", masterpagename="Standard"))
    normal.addElement(TextProperties(fontsize="11pt"))

    page_header = add(Style(name="PageHeader", family="paragraph"))
    page_header.addElement(ParagraphProperties(textalign="start"))
    page_header.addElement(TextProperties(fontsize="9pt"))

    hebrew = add(Style(name="HebrewText", family="paragraph"))
    hebrew.addElement(ParagraphProperties(textalign="end", writingmode="rl-tb"))
    hebrew.addElement(TextProperties(fontsize="12pt"))

    small = add(Style(name="SmallText", family="paragraph"))
    small.addElement(TextProperties(fontsize="9pt"))

    heading = add(Style(name="SectionHeading", family="paragraph", masterpagename="Standard"))
    heading.addElement(TextProperties(fontsize="14pt", fontweight="bold"))
    styles["Title"] = heading

    verse_number = add(Style(name="VerseNumber", family="text"))
    verse_number.addElement(TextProperties(fontsize="8pt", fontweight="bold", color="#666666"))

    chapter_number = add(Style(name="ChapterNumber", family="text"))
    chapter_number.addElement(TextProperties(fontsize="13pt", fontweight="bold"))

    col = add(Style(name="HalfColumn", family="table-column"))
    col.addElement(TableColumnProperties(columnwidth="3.45in"))
    styles["HebrewColumn"] = col
    styles["EnglishColumn"] = col

    full_col = add(Style(name="FullColumn", family="table-column"))
    full_col.addElement(TableColumnProperties(columnwidth="6.9in"))
    styles["FullColumn"] = full_col

    cell = add(Style(name="TextCell", family="table-cell"))
    cell.addElement(
        TableCellProperties(border="0.05pt solid #9a9a9a", padding="0.05in", verticalalign="top")
    )

    header_cell = add(Style(name="HeaderCell", family="table-cell"))
    header_cell.addElement(
        TableCellProperties(border="0.05pt solid #666666", padding="0.05in", backgroundcolor="#eeeeee")
    )
    styles["EnglishText"] = normal
    styles["PageHeader"] = page_header
    styles["Table"] = None
    styles["TableRow"] = None

    return styles


def paragraph(text: str, style: StyleRef) -> P:
    return P(stylename=style, text=text)


def first_child_by_qname(element: Any, qname: tuple[str, str]) -> Any | None:
    for child in element.childNodes:
        if getattr(child, "qname", None) == qname:
            return child
    return None


def ensure_master_page(doc: Any) -> Any:
    master_page = first_child_by_qname(
        doc.masterstyles,
        (STYLE_NS, "master-page"),
    )
    if master_page is not None:
        return master_page

    page_layout = PageLayout(name="SheetPageLayout")
    page_layout.addElement(
        PageLayoutProperties(
            pagewidth="8.5in",
            pageheight="11in",
            margintop="0.4in",
            marginbottom="0.4in",
            marginleft="0.4in",
            marginright="0.4in",
        )
    )
    header_style = HeaderStyle()
    header_style.addElement(HeaderFooterProperties(minheight="0in", marginbottom="0.1965in"))
    page_layout.addElement(header_style)
    doc.automaticstyles.addElement(page_layout)

    master_page = MasterPage(name="Standard", pagelayoutname="SheetPageLayout")
    master_page.addElement(Header())
    doc.masterstyles.addElement(master_page)
    return master_page


def set_page_header(doc: Any, styles: dict[str, StyleRef], header_text: str) -> None:
    master_page = ensure_master_page(doc)
    header = first_child_by_qname(
        master_page,
        (STYLE_NS, "header"),
    )
    if header is None:
        header = Header()
        master_page.addElement(header)

    for child in list(header.childNodes):
        header.removeChild(child)

    page_number_paragraph = P(stylename=styles["PageHeader"])
    page_number_paragraph.addElement(PageNumber(selectpage="current", text="1"))
    page_number_paragraph.addElement(S())
    page_number_paragraph.addText("of ")
    page_number_paragraph.addElement(PageCount(text="1"))
    header.addElement(page_number_paragraph)
    header.addElement(P(stylename=styles["PageHeader"], text=header_text))


def add_numbered_text(paragraph_element: P, number: str, style: StyleRef) -> None:
    if style is None:
        paragraph_element.addText(number)
        return
    paragraph_element.addElement(Span(stylename=style, text=number))


def add_verse_number(paragraph_element: P, number: str, style: StyleRef) -> None:
    add_numbered_text(paragraph_element, f"{number}{NBSP}", style)


def chapter_paragraph(chapter: str, verses: list[Verse], language: str, styles: dict[str, StyleRef]) -> P:
    style = styles["HebrewText"] if language == "hebrew" else styles["EnglishText"]
    paragraph_element = P(stylename=style)
    if chapter:
        add_numbered_text(paragraph_element, f"{chapter} ", styles["ChapterNumber"])

    for verse in verses:
        parts = parse_ref_parts(verse.ref)
        verse_label = parts.verse if parts.verse else verse.ref
        text = verse.hebrew if language == "hebrew" else verse.english
        add_verse_number(paragraph_element, verse_label, styles["VerseNumber"])
        paragraph_element.addText(f"{text} ")

    return paragraph_element


def base_languages_for_options(options: GenerationOptions) -> list[str]:
    if options.language_mode == "bilingual":
        return ["hebrew", "english"]
    return [options.language_mode]


def table_column_style(language: str, column_count: int, styles: dict[str, StyleRef]) -> StyleRef:
    if column_count == 1:
        return styles["FullColumn"]
    return styles["HebrewColumn"] if language == "hebrew" else styles["EnglishColumn"]


def add_table_for_verses(
    doc: Any,
    styles: dict[str, StyleRef],
    name: str,
    verses: list[Verse],
    options: GenerationOptions,
) -> None:
    languages = base_languages_for_options(options)
    table_style = styles["Table"]
    table = (
        Table(name=safe_slug(name), stylename=table_style)
        if table_style is not None
        else Table(name=safe_slug(name))
    )
    for language in languages:
        column_style = table_column_style(language, len(languages), styles)
        table.addElement(TableColumn(stylename=column_style) if column_style is not None else TableColumn())

    for (_book, chapter), chapter_verses in chapter_blocks(verses):
        row_style = styles["TableRow"]
        row = TableRow(stylename=row_style) if row_style is not None else TableRow()
        for language in languages:
            cell = TableCell(stylename=styles["TextCell"])
            cell.addElement(chapter_paragraph(chapter, chapter_verses, language, styles))
            row.addElement(cell)
        table.addElement(row)

    doc.text.addElement(table)


def add_reading_section(
    doc: Any,
    styles: dict[str, StyleRef],
    row: ReadingRow,
    session: requests.Session,
    options: GenerationOptions,
) -> None:
    verses, note = fetch_verses_for_reading(row.reading, session, options)
    heading = f"{label_for_aliyah(row.aliyah)} - {row.reading.split('|', 1)[0].strip()}"
    if note:
        heading += f" ({note})"
    doc.text.addElement(H(outlinelevel=2, stylename=styles["SectionHeading"], text=heading))
    add_table_for_verses(doc, styles, f"{row.parashah}-{row.aliyah}-{row.reading}", verses, options)


def filename_suffixes(options: GenerationOptions) -> list[str]:
    suffixes: list[str] = []
    if options.language_mode == "english":
        suffix = "english"
        if options.english_version != DEFAULT_ENGLISH_VERSION_SLUG:
            suffix += f"-{options.english_version}"
        suffixes.append(suffix)
    elif options.language_mode == "hebrew":
        suffix = "hebrew"
        if options.hebrew_version != DEFAULT_HEBREW_VERSION_SLUG:
            suffix += f"-{options.hebrew_version}"
        suffixes.append(suffix)
    elif options.english_version != DEFAULT_ENGLISH_VERSION_SLUG:
        suffixes.append(options.english_version)
    if options.language_mode == "bilingual" and options.hebrew_version != DEFAULT_HEBREW_VERSION_SLUG:
        suffixes.append(options.hebrew_version)
    return suffixes


def output_filename(parasha_date: date, parashah: str, options: GenerationOptions) -> str:
    stem_parts = [parasha_date.isoformat(), safe_slug(parashah)]
    stem_parts.extend(filename_suffixes(options))
    return f"{'_'.join(stem_parts)}.odt"


def build_document(
    output_path: Path,
    parasha_date: date,
    parashah: str,
    rows: list[ReadingRow],
    session: requests.Session,
    template_path: Path,
    options: GenerationOptions,
) -> None:
    doc, styles, used_template = create_document(template_path)

    display_date = f"{parasha_date:%B} {parasha_date.day}, {parasha_date.year}"
    set_page_header(doc, styles, f"Parashat {parashah} - {display_date}")
    doc.text.addElement(H(outlinelevel=1, stylename=styles["Title"], text=f"Parashat {parashah}"))

    for row in rows:
        if is_sheet_reading(row.aliyah):
            add_reading_section(doc, styles, row, session, options)

    doc.text.addElement(H(outlinelevel=2, stylename=styles["SectionHeading"], text="Sources"))
    doc.text.addElement(
        paragraph(
            source_summary(options),
            styles["SmallText"],
        )
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    if used_template:
        clean_template_output_package(output_path)


def generate(
    input_date: date,
    output_dir: Path,
    template_path: Path,
    create_pdf: bool = True,
    pdf_converter: str = "auto",
    options: GenerationOptions | None = None,
) -> tuple[Path, Path | None]:
    options = normalize_generation_options(options)
    session = requests.Session()
    session.headers.update({"User-Agent": "parasha-sheet-generator/1.0"})
    parasha_date = shabbat_for_week(input_date)
    rows = fetch_hebcal_rows(hebcal_years_for_date(parasha_date), session)
    parashah, group_rows = weekly_parasha_group(rows, parasha_date)
    filename = output_filename(parasha_date, parashah, options)
    output_path = output_dir / filename
    print(f"Generating {output_path}")
    build_document(output_path, parasha_date, parashah, group_rows, session, template_path, options)
    pdf_path = None
    if create_pdf:
        print(f"Generating {output_path.with_suffix('.pdf')}")
        pdf_path = convert_odt_to_pdf(output_path, pdf_converter)
    return output_path, pdf_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "date",
        nargs="?",
        help="Gregorian date in YYYY-MM-DD format. Generates the reading for that week's Shabbat.",
    )
    parser.add_argument(
        "--start-date",
        help="Deprecated alias for the positional date argument.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(OUTPUT_DIR),
        help="Directory for generated .odt files",
    )
    parser.add_argument(
        "--template",
        default=str(TEMPLATE_PATH),
        help="LibreOffice .ott template to use for generated document formatting",
    )
    parser.add_argument(
        "--no-pdf",
        action="store_true",
        help="Generate only the .odt file",
    )
    parser.add_argument(
        "--pdf-converter",
        choices=PDF_CONVERTERS,
        default="auto",
        help="Tool to use for PDF conversion. auto prefers LibreOffice and falls back to pandoc.",
    )
    parser.add_argument(
        "--language-mode",
        choices=LANGUAGE_MODES,
        default=DEFAULT_LANGUAGE_MODE,
        help="Text languages to include in the sheet.",
    )
    parser.add_argument(
        "--english-version",
        choices=tuple(ENGLISH_VERSION_BY_SLUG),
        default=DEFAULT_ENGLISH_VERSION_SLUG,
        help="English Sefaria version slug to use when English is included.",
    )
    parser.add_argument(
        "--hebrew-version",
        choices=tuple(HEBREW_VERSION_BY_SLUG),
        default=DEFAULT_HEBREW_VERSION_SLUG,
        help="Hebrew Sefaria version slug to use when Hebrew is included.",
    )
    divine_name_group = parser.add_mutually_exclusive_group()
    divine_name_group.add_argument(
        "--replace-divine-names",
        dest="replace_divine_names",
        action="store_true",
        default=None,
        help="Replace Hebrew Divine names in Hebrew output.",
    )
    divine_name_group.add_argument(
        "--no-replace-divine-names",
        dest="replace_divine_names",
        action="store_false",
        help="Leave Hebrew Divine names unchanged.",
    )
    args = parser.parse_args()
    date_text = args.date or args.start_date
    if not date_text:
        parser.error("provide a date in YYYY-MM-DD format")
    replace_divine_names_option = (
        divine_name_replacement_enabled()
        if args.replace_divine_names is None
        else args.replace_divine_names
    )
    options = GenerationOptions(
        language_mode=args.language_mode,
        english_version=args.english_version,
        hebrew_version=args.hebrew_version,
        replace_divine_names=replace_divine_names_option,
    )
    odt_path, pdf_path = generate(
        date.fromisoformat(date_text),
        Path(args.output_dir),
        Path(args.template),
        create_pdf=not args.no_pdf,
        pdf_converter=args.pdf_converter,
        options=options,
    )
    print(f"Generated {odt_path}")
    if pdf_path is not None:
        print(f"Generated {pdf_path}")


if __name__ == "__main__":
    main()
