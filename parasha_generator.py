#!/usr/bin/env python3
"""Generate side-by-side parasha sheets as LibreOffice Writer documents."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
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


OUTPUT_DIR = Path("sheets")
CACHE_DIR = Path(".cache/parasha_generator")
TEMPLATE_PATH = Path("template.ott")
ODT_MIMETYPE = "application/vnd.oasis.opendocument.text"
MANIFEST_NS = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
STYLE_NS = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
HEBCAL_CSV_URL = "https://www.hebcal.com/sedrot/fullkriyah-{year}.csv"
SEFARIA_TEXT_URL = "https://www.sefaria.org/api/texts/{ref}"
EN_VERSION = "THE JPS TANAKH: Gender-Sensitive Edition"
HE_VERSION = "Tanach with Nikkud"
NBSP = "\u00a0"
StyleRef = Any

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


def cache_key_for_ref(ref: str) -> str:
    key_data = {
        "ref": ref,
        "ven": EN_VERSION,
        "vhe": HE_VERSION,
    }
    digest = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"{safe_slug(ref)}-{digest}.json"


def fetch_sefaria(ref: str, session: requests.Session) -> dict[str, Any]:
    cache_path = CACHE_DIR / "sefaria" / cache_key_for_ref(ref)
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    url = SEFARIA_TEXT_URL.format(ref=sefaria_path(ref))
    response = session.get(
        url,
        params={"context": 0, "ven": EN_VERSION, "vhe": HE_VERSION},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        raise RuntimeError(f"Sefaria returned an error for {ref}: {data['error']}")
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


def refs_from_sefaria_data(data: dict[str, Any]) -> list[str]:
    spanning_refs = data.get("spanningRefs") or []
    if spanning_refs:
        text_segments = data.get("text")
        if not isinstance(text_segments, list) or len(text_segments) != len(spanning_refs):
            english_count = len(flatten_text(data.get("text")))
            return [data.get("ref", data.get("indexTitle", ""))] * english_count
        refs: list[str] = []
        for spanning_ref, segment in zip(spanning_refs, text_segments):
            refs.extend(refs_from_range(spanning_ref, len(flatten_text(segment))))
        return refs

    index_title = data["indexTitle"]
    sections = data["sections"]
    to_sections = data["toSections"]
    ref = f"{index_title} {sections[0]}:{sections[1]}-{to_sections[0]}:{to_sections[1]}"
    refs = refs_from_range(ref)
    english_count = len(flatten_text(data.get("text")))
    if len(refs) != english_count:
        refs = [data.get("ref", index_title)] * english_count
    return refs


def fetch_verses_for_reading(reading: str, session: requests.Session) -> tuple[list[Verse], str | None]:
    refs, note = split_reading_refs(reading)
    verses: list[Verse] = []
    for ref in refs:
        data = fetch_sefaria(ref, session)
        english = flatten_text(data.get("text"))
        hebrew = flatten_text(data.get("he"))
        if len(english) != len(hebrew):
            raise RuntimeError(
                f"Sefaria returned mismatched English/Hebrew verse counts for {ref}: "
                f"{len(english)} English vs {len(hebrew)} Hebrew"
            )
        verse_refs = refs_from_sefaria_data(data)
        if len(verse_refs) != len(english):
            raise RuntimeError(
                f"Could not align verse references for {ref}: {len(verse_refs)} refs vs {len(english)} verses"
            )
        verses.extend(
            Verse(ref=verse_ref, english=en, hebrew=he)
            for verse_ref, en, he in zip(verse_refs, english, hebrew)
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
        "TableRow": "Table1.1",
        "TextCell": "Table1.A1",
    }


def clear_document_text(doc: Any) -> None:
    for child in list(doc.text.childNodes):
        doc.text.removeChild(child)


def create_document(template_path: Path) -> tuple[Any, dict[str, StyleRef], bool]:
    if template_path.exists():
        doc = load(str(template_path))
        clear_document_text(doc)
        doc.mimetype = ODT_MIMETYPE
        return doc, template_styles(), True

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


def add_table_for_verses(doc: Any, styles: dict[str, StyleRef], name: str, verses: list[Verse]) -> None:
    table_style = styles["Table"]
    table = (
        Table(name=safe_slug(name), stylename=table_style)
        if table_style is not None
        else Table(name=safe_slug(name))
    )
    table.addElement(TableColumn(stylename=styles["HebrewColumn"]))
    table.addElement(TableColumn(stylename=styles["EnglishColumn"]))

    for (_book, chapter), chapter_verses in chapter_blocks(verses):
        row_style = styles["TableRow"]
        row = TableRow(stylename=row_style) if row_style is not None else TableRow()
        hebrew_cell = TableCell(stylename=styles["TextCell"])
        hebrew_cell.addElement(chapter_paragraph(chapter, chapter_verses, "hebrew", styles))
        english_cell = TableCell(stylename=styles["TextCell"])
        english_cell.addElement(chapter_paragraph(chapter, chapter_verses, "english", styles))
        row.addElement(hebrew_cell)
        row.addElement(english_cell)
        table.addElement(row)

    doc.text.addElement(table)


def add_reading_section(
    doc: Any,
    styles: dict[str, StyleRef],
    row: ReadingRow,
    session: requests.Session,
) -> None:
    verses, note = fetch_verses_for_reading(row.reading, session)
    heading = f"{label_for_aliyah(row.aliyah)} - {row.reading.split('|', 1)[0].strip()}"
    if note:
        heading += f" ({note})"
    doc.text.addElement(H(outlinelevel=2, stylename=styles["SectionHeading"], text=heading))
    add_table_for_verses(doc, styles, f"{row.parashah}-{row.aliyah}-{row.reading}", verses)


def build_document(
    output_path: Path,
    parasha_date: date,
    parashah: str,
    rows: list[ReadingRow],
    session: requests.Session,
    template_path: Path,
) -> None:
    doc, styles, used_template = create_document(template_path)

    display_date = f"{parasha_date:%B} {parasha_date.day}, {parasha_date.year}"
    set_page_header(doc, styles, f"Parashat {parashah} - {display_date}")
    doc.text.addElement(H(outlinelevel=1, stylename=styles["Title"], text=f"Parashat {parashah}"))
    doc.text.addElement(paragraph(f"Diaspora reading for {display_date}", styles["NormalText"]))
    doc.text.addElement(
        paragraph(
            "Aliyot and haftarah from Hebcal. Hebrew and English text from Sefaria.",
            styles["SmallText"],
        )
    )

    for row in rows:
        if is_sheet_reading(row.aliyah):
            add_reading_section(doc, styles, row, session)

    doc.text.addElement(H(outlinelevel=2, stylename=styles["SectionHeading"], text="Sources"))
    doc.text.addElement(
        paragraph(
            f"Hebcal Full Kriyah CSV, Diaspora. Sefaria texts: English {EN_VERSION} and Hebrew {HE_VERSION}.",
            styles["SmallText"],
        )
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    if used_template:
        clean_template_output_package(output_path)


def generate(input_date: date, output_dir: Path, template_path: Path) -> Path:
    session = requests.Session()
    session.headers.update({"User-Agent": "parasha-sheet-generator/1.0"})
    parasha_date = shabbat_for_week(input_date)
    rows = fetch_hebcal_rows(hebcal_years_for_date(parasha_date), session)
    parashah, group_rows = weekly_parasha_group(rows, parasha_date)
    filename = f"{parasha_date.isoformat()}_{safe_slug(parashah)}.odt"
    output_path = output_dir / filename
    print(f"Generating {output_path}")
    build_document(output_path, parasha_date, parashah, group_rows, session, template_path)
    return output_path


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
    args = parser.parse_args()
    date_text = args.date or args.start_date
    if not date_text:
        parser.error("provide a date in YYYY-MM-DD format")
    output_path = generate(date.fromisoformat(date_text), Path(args.output_dir), Path(args.template))
    print(f"Generated {output_path}")


if __name__ == "__main__":
    main()
