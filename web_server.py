#!/usr/bin/env python3
"""Tiny web frontend for generating parasha sheets."""

from __future__ import annotations

import errno
import html
import os
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from parasha_generator import (
    DEFAULT_ENGLISH_VERSION_SLUG,
    DEFAULT_HEBREW_VERSION_SLUG,
    DEFAULT_LANGUAGE_MODE,
    DEFAULT_TYPE_SIZE_SLUG,
    ENGLISH_VERSIONS,
    HEBREW_VERSIONS,
    PDF_CONVERTERS,
    TYPE_SIZE_PRESETS,
    GenerationOptions,
    default_generation_options,
    generate,
    has_hebrew_output,
    normalize_generation_options,
    source_summary,
)


PROJECT_DIR = Path(__file__).resolve().parent
GENERATE_LOCK = threading.Lock()
MAX_FORM_BYTES = 4096
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
PORT_SEARCH_ATTEMPTS = 20
DEFAULT_OUTPUT_RETENTION_SECONDS = 3600


TYPE_SIZE_OPTIONS = [(preset.slug, preset.label) for preset in TYPE_SIZE_PRESETS]
TYPE_SIZE_LABEL_BY_SLUG = {preset.slug: preset.label for preset in TYPE_SIZE_PRESETS}


@dataclass(frozen=True)
class FormError(ValueError):
    message: str
    selected_date: date | str | None = None
    options: GenerationOptions | None = None
    type_size: str | None = None

    def __str__(self) -> str:
        return self.message


def project_path_from_env(name: str, default: str) -> Path:
    value = os.environ.get(name, default)
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def output_dir() -> Path:
    return project_path_from_env("PARASHA_OUTPUT_DIR", "sheets")


def template_path() -> Path:
    return project_path_from_env("PARASHA_TEMPLATE", "template.ott")


def normalize_type_size(value: str | None) -> str:
    if not value:
        return DEFAULT_TYPE_SIZE_SLUG
    if value not in TYPE_SIZE_LABEL_BY_SLUG:
        raise ValueError(
            "Type size must be one of "
            f"{', '.join(slug for slug, _label in TYPE_SIZE_OPTIONS)}."
        )
    return value


def type_size_label(value: str) -> str:
    return TYPE_SIZE_LABEL_BY_SLUG[normalize_type_size(value)]


def pdf_converter() -> str:
    converter = os.environ.get("PARASHA_PDF_CONVERTER", "auto")
    if converter not in PDF_CONVERTERS:
        raise ValueError(
            f"PARASHA_PDF_CONVERTER must be one of {', '.join(PDF_CONVERTERS)}"
        )
    return converter


def output_retention_seconds() -> int:
    value = os.environ.get(
        "PARASHA_OUTPUT_RETENTION_SECONDS",
        str(DEFAULT_OUTPUT_RETENTION_SECONDS),
    )
    try:
        retention = int(value)
    except ValueError as exc:
        raise ValueError("PARASHA_OUTPUT_RETENTION_SECONDS must be an integer.") from exc
    if retention < 0:
        raise ValueError("PARASHA_OUTPUT_RETENTION_SECONDS must be zero or greater.")
    return retention


def cleanup_output_files() -> None:
    retention = output_retention_seconds()
    if retention == 0:
        return

    base_dir = output_dir()
    if not base_dir.exists():
        return
    cutoff = time.time() - retention
    for path in base_dir.iterdir():
        if path.suffix not in {".odt", ".pdf"} or not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except FileNotFoundError:
            continue


def configured_port() -> tuple[int, bool]:
    value = os.environ.get("PARASHA_PORT")
    if value is None:
        return DEFAULT_PORT, False
    try:
        return int(value), True
    except ValueError as exc:
        raise SystemExit("PARASHA_PORT must be an integer.") from exc


def create_server(host: str, port: int, port_is_explicit: bool) -> tuple[ThreadingHTTPServer, int]:
    ports = (port,) if port_is_explicit else range(port, port + PORT_SEARCH_ATTEMPTS)
    last_error: OSError | None = None

    for selected_port in ports:
        try:
            return ThreadingHTTPServer((host, selected_port), ParashaHandler), selected_port
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            last_error = exc
            if port_is_explicit:
                raise SystemExit(
                    f"Port {selected_port} is already in use. "
                    "Stop the existing server or choose another port, for example: "
                    "PARASHA_PORT=8001 .venv/bin/python web_server.py"
                ) from exc

    raise SystemExit(
        f"No available port found from {port} through {port + PORT_SEARCH_ATTEMPTS - 1}."
    ) from last_error


def page(title: str, body: str) -> bytes:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{
      color: #333333;
      background: #fefefe;
      font-family: sans-serif;
    }}
    header, main, footer {{
      margin: 0 auto;
      max-width: 48rem;
      padding: 1rem;
    }}
    form {{
      display: grid;
      gap: 1rem;
    }}
    fieldset {{
      display: grid;
      gap: 0.75rem;
      margin: 0;
      padding: 1rem;
    }}
    legend {{
      font-weight: 700;
      padding: 0 0.3rem;
    }}
    label {{
      display: grid;
      gap: 0.25rem;
      font-weight: 650;
    }}
    input, select, button {{
      font: inherit;
      max-width: 100%;
    }}
    input[type="date"], select {{
      border: 1px solid #cccccc;
      padding: 0.45rem 0.5rem;
    }}
    input[type="checkbox"] {{
      margin-right: 0.4rem;
    }}
    button {{
      background: #333366;
      border: 0;
      border-radius: 0.25rem;
      color: white;
      font-weight: 700;
      padding: 0.6rem 0.9rem;
      width: fit-content;
    }}
    p {{
      margin: 0.5rem 0;
    }}
    ul {{
      padding-left: 1.25rem;
    }}
    [hidden] {{
      display: none !important;
    }}
  </style>
</head>

<body>
<header><h1>Parasha Sheet Generator</h1></header>
<main><section>

  {body}

  </section></main>

<footer><p>Powered by <a href="https://sefaria.org">Sefaria</a> and <a href="https://www.hebcal.com">Hebcal</a>.  Koren and JPS texts available under the <a href="https://creativecommons.org/licenses/by-nc/4.0/">CC BY-NC 4.0</a> license.  Please send comments to <a href="mailto:parasha@lautman.net">parasha@lautman.net</a>.</p></footer>

 </body>
</html>
""".encode("utf-8")


def selected_attr(current: str, value: str) -> str:
    return ' selected' if current == value else ""


def checked_attr(value: bool) -> str:
    return " checked" if value else ""


def hidden_attr(value: bool) -> str:
    return " hidden" if value else ""


def disabled_attr(value: bool) -> str:
    return " disabled" if value else ""


def option_tags(options: list[tuple[str, str]], selected: str) -> str:
    return "\n".join(
        f'<option value="{html.escape(value)}"{selected_attr(selected, value)}>{html.escape(label)}</option>'
        for value, label in options
    )


def form_page(
    message: str = "",
    selected_date: date | str | None = None,
    options: GenerationOptions | None = None,
    type_size: str | None = None,
) -> bytes:
    options = normalize_generation_options(options or default_generation_options())
    selected_date = selected_date or date.today()
    selected_date_text = selected_date if isinstance(selected_date, str) else selected_date.isoformat()
    try:
        selected_type_size = normalize_type_size(type_size or options.type_size)
    except ValueError:
        selected_type_size = DEFAULT_TYPE_SIZE_SLUG
    escaped_message = f"<p>{html.escape(message)}</p>" if message else ""
    type_size_options = option_tags(
        TYPE_SIZE_OPTIONS,
        selected_type_size,
    )
    language_options = option_tags(
        [
            ("bilingual", "Hebrew and English"),
            ("english", "English only"),
            ("hebrew", "Hebrew only"),
        ],
        options.language_mode,
    )
    english_options = option_tags(
        [(version.slug, version.label) for version in ENGLISH_VERSIONS],
        options.english_version,
    )
    hebrew_options = option_tags(
        [(version.slug, version.label) for version in HEBREW_VERSIONS],
        options.hebrew_version,
    )
    show_english = options.language_mode != "hebrew"
    show_hebrew = options.language_mode != "english"
    show_divine_names = has_hebrew_output(options)
    body = f"""
{escaped_message}

<article>
<form action="/generate" method="post">
 <fieldset>
  <legend>Sheet</legend>
  <p>Select a date and options. The program will generate a parasha handout sheet for the Shabbat of that week (in the Diaspora) as a PDF and as a LibreOffice document for further editing.  </p>

  <label for="date">Date
   <input id="date" name="date" type="date" value="{html.escape(selected_date_text)}" required>
  </label>
  <label for="language_mode">Language
   <select id="language_mode" name="language_mode">
    {language_options}
   </select>
  </label>
  <label for="type_size">Type size
   <select id="type_size" name="type_size">
    {type_size_options}
   </select>
  </label>
 </fieldset>

 <fieldset>
  <legend>Texts</legend>
  <label id="english-options" for="english_version"{hidden_attr(not show_english)}>English
   <select id="english_version" name="english_version"{disabled_attr(not show_english)}>
    {english_options}
   </select>
  </label>
  <label id="hebrew-options" for="hebrew_version"{hidden_attr(not show_hebrew)}>Hebrew
   <select id="hebrew_version" name="hebrew_version"{disabled_attr(not show_hebrew)}>
    {hebrew_options}
   </select>
  </label>
  <label id="divine-name-options"{hidden_attr(not show_divine_names)}>
   <span><input id="replace_divine_names" name="replace_divine_names" type="checkbox" value="1"{checked_attr(options.replace_divine_names)}{disabled_attr(not show_divine_names)}>Replace divine names</span>
  </label>
 </fieldset>

 <fieldset>
  <button type="submit">Generate</button>
 </fieldset>
</form>
</article>


<script>
  function updateControls() {{
    const language = document.getElementById("language_mode").value;
    const toggle = function(id, visible) {{
      const element = document.getElementById(id);
      element.hidden = !visible;
      element.querySelectorAll("input, select").forEach(function(control) {{
        control.disabled = !visible;
      }});
    }};
    toggle("english-options", language !== "hebrew");
    toggle("hebrew-options", language !== "english");
    toggle("divine-name-options", language !== "english");
  }}
  document.addEventListener("DOMContentLoaded", function() {{
    document.getElementById("language_mode").addEventListener("change", updateControls);
    updateControls();
  }});
</script>
"""
    return page("Parasha Sheet Generator", body)


def result_page(
    input_date: date,
    odt_path: Path,
    pdf_path: Path | None,
    options: GenerationOptions,
) -> bytes:
    odt_name = odt_path.name
    links = [
        (
            "Download LibreOffice file (.odt)",
            f"/download/{quote(odt_name)}",
        )
    ]   
    if pdf_path is not None:
        links.append(("Download PDF file", f"/download/{quote(pdf_path.name)}"))

    link_items = "\n".join(
        f'<li><a href="{href}">{html.escape(label)}</a></li>' for label, href in links
    )
    body = f"""
<article><h2>Done</h2>
<p>Date: {html.escape(input_date.isoformat())}</p>
<p>Type size: {html.escape(type_size_label(options.type_size))}</p>
<ul>
  {link_items}
</ul>
</article>
<article>
 <p><a href="/">Generate another sheet</a></p>
 <h2>Sources</h2>
<p>{html.escape(source_summary(options))}</p>
</article>
"""
    return page("Generated", body)


def error_page(status: HTTPStatus, message: str) -> bytes:
    body = f"""
<h1>{status.value} {html.escape(status.phrase)}</h1>
<p>{html.escape(message)}</p>
<p><a href="/">Back</a></p>
"""
    return page(status.phrase, body)


class ParashaHandler(BaseHTTPRequestHandler):
    server_version = "ParashaSheetHTTP/1.0"

    def do_GET(self) -> None:
        try:
            cleanup_output_files()
        except Exception as exc:
            self.send_html(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                error_page(HTTPStatus.INTERNAL_SERVER_ERROR, f"Cleanup failed: {exc}"),
            )
            return

        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_html(HTTPStatus.OK, form_page())
            return
        if parsed.path.startswith("/download/"):
            self.send_download(parsed.path.removeprefix("/download/"))
            return
        self.send_html(
            HTTPStatus.NOT_FOUND,
            error_page(HTTPStatus.NOT_FOUND, "That page does not exist."),
        )

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/generate":
            self.send_html(
                HTTPStatus.NOT_FOUND,
                error_page(HTTPStatus.NOT_FOUND, "That page does not exist."),
            )
            return

        try:
            selected_date, options = self.read_form()
        except FormError as exc:
            self.send_html(
                HTTPStatus.BAD_REQUEST,
                form_page(str(exc), exc.selected_date, exc.options, exc.type_size),
            )
            return

        try:
            cleanup_output_files()
            with GENERATE_LOCK:
                odt_path, pdf_path = generate(
                    selected_date,
                    output_dir(),
                    template_path(),
                    create_pdf=True,
                    pdf_converter=pdf_converter(),
                    options=options,
                )
            cleanup_output_files()
        except Exception as exc:
            self.send_html(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                form_page(
                    f"Generation failed: {exc}",
                    selected_date,
                    options,
                    options.type_size,
                ),
            )
            return

        self.send_html(
            HTTPStatus.OK,
            result_page(selected_date, odt_path, pdf_path, options),
        )

    def read_form_values(self) -> dict[str, list[str]]:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            raise FormError("Missing form body.")
        try:
            length = int(length_header)
        except ValueError as exc:
            raise FormError("Invalid form body length.") from exc
        if length < 0 or length > MAX_FORM_BYTES:
            raise FormError("Form body is too large.")

        body = self.rfile.read(length).decode("utf-8", errors="replace")
        return parse_qs(body, keep_blank_values=True)

    def form_value(self, values: dict[str, list[str]], name: str, default: str) -> str:
        candidates = values.get(name, [])
        return candidates[0] if candidates and candidates[0] else default

    def type_size_from_values(self, values: dict[str, list[str]]) -> str:
        old_field_value = self.form_value(values, "template_size", DEFAULT_TYPE_SIZE_SLUG)
        return self.form_value(values, "type_size", old_field_value)

    def options_from_values(self, values: dict[str, list[str]]) -> GenerationOptions:
        options = GenerationOptions(
            language_mode=self.form_value(values, "language_mode", DEFAULT_LANGUAGE_MODE),
            english_version=self.form_value(
                values,
                "english_version",
                DEFAULT_ENGLISH_VERSION_SLUG,
            ),
            hebrew_version=self.form_value(
                values,
                "hebrew_version",
                DEFAULT_HEBREW_VERSION_SLUG,
            ),
            replace_divine_names="replace_divine_names" in values,
            type_size=self.type_size_from_values(values),
        )
        return normalize_generation_options(options)

    def date_from_values(self, values: dict[str, list[str]]) -> date:
        date_values = values.get("date", [])
        if not date_values or not date_values[0]:
            raise ValueError("Choose a date.")
        try:
            return date.fromisoformat(date_values[0])
        except ValueError as exc:
            raise ValueError("Use a date in YYYY-MM-DD format.") from exc

    def read_form(self) -> tuple[date, GenerationOptions]:
        values = self.read_form_values()
        selected_date_value = self.form_value(values, "date", date.today().isoformat())
        type_size_value = self.type_size_from_values(values)
        try:
            options = self.options_from_values(values)
        except ValueError as exc:
            raise FormError(
                str(exc),
                selected_date_value,
                default_generation_options(),
                type_size_value,
            ) from exc
        try:
            selected_date = self.date_from_values(values)
        except ValueError as exc:
            raise FormError(str(exc), selected_date_value, options, options.type_size) from exc
        return selected_date, options

    def send_download(self, encoded_name: str) -> None:
        filename = unquote(encoded_name)
        if "/" in filename or "\\" in filename:
            self.send_html(
                HTTPStatus.BAD_REQUEST,
                error_page(HTTPStatus.BAD_REQUEST, "Invalid filename."),
            )
            return
        if Path(filename).suffix not in {".odt", ".pdf"}:
            self.send_html(
                HTTPStatus.NOT_FOUND,
                error_page(HTTPStatus.NOT_FOUND, "That file is not available."),
            )
            return

        base_dir = output_dir().resolve()
        path = (base_dir / filename).resolve()
        if path.parent != base_dir or not path.is_file():
            self.send_html(
                HTTPStatus.NOT_FOUND,
                error_page(HTTPStatus.NOT_FOUND, "That file is not available."),
            )
            return

        content_type = (
            "application/vnd.oasis.opendocument.text"
            if path.suffix == ".odt"
            else "application/pdf"
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{path.name}"',
        )
        self.end_headers()
        with path.open("rb") as file_obj:
            shutil.copyfileobj(file_obj, self.wfile)

    def send_html(self, status: HTTPStatus, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    os.chdir(PROJECT_DIR)
    host = os.environ.get("PARASHA_HOST", DEFAULT_HOST)
    port, port_is_explicit = configured_port()
    output_dir().mkdir(parents=True, exist_ok=True)
    cleanup_output_files()
    server, selected_port = create_server(host, port, port_is_explicit)
    if selected_port != port:
        print(f"Port {port} is already in use; using {selected_port} instead.")
    print(f"Serving on http://{host}:{selected_port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
