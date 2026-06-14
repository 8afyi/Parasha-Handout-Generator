#!/usr/bin/env python3
"""Tiny web frontend for generating parasha sheets."""

from __future__ import annotations

import errno
import html
import os
import shutil
import threading
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from parasha_generator import PDF_CONVERTERS, generate


PROJECT_DIR = Path(__file__).resolve().parent
GENERATE_LOCK = threading.Lock()
MAX_FORM_BYTES = 4096
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
PORT_SEARCH_ATTEMPTS = 20


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


def pdf_converter() -> str:
    converter = os.environ.get("PARASHA_PDF_CONVERTER", "auto")
    if converter not in PDF_CONVERTERS:
        raise ValueError(
            f"PARASHA_PDF_CONVERTER must be one of {', '.join(PDF_CONVERTERS)}"
        )
    return converter


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
</head>

<body>
<header><h1>Parasha Sheet Generator</h1></header>
<main><section>

  {body}

  </section></main>


 </body>
</html>
""".encode("utf-8")


def form_page(message: str = "") -> bytes:
    today = date.today().isoformat()
    escaped_message = f"<p>{html.escape(message)}</p>" if message else ""
    body = f"""
{escaped_message}

<article><p>
Pick a date. The generator finds that week's Shabbat reading, fetches the diaspora Torah and haftarah readings, and builds a side-by-side Hebrew/English sheet divided by aliyot.
</p></article>

<article>
<form action="/generate" method="post">
 <fieldset role="group">
  <input id="date" name="date" type="date" value="{today}" required>
  <button type="submit">Generate</button>
 </fieldset>
</form>
</article>
"""
    return page("Parasha Sheet Generator", body)


def result_page(input_date: date, odt_path: Path, pdf_path: Path | None) -> bytes:
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
<ul>
  {link_items}
</ul>
</article>
<article>
 <p><a href="/">Generate another sheet</a></p>
 <h2>Sources</h2>
<p><a href=https://hebcal.com>Hebcal</a>: Full Kriyah CSV, Diaspora. <a href=https://sefaria.org>Sefaria</a> texts: English The Koren Jerusalem Bible and Hebrew Tanach with Nikkud.</p>
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
            selected_date = self.read_form_date()
        except ValueError as exc:
            self.send_html(HTTPStatus.BAD_REQUEST, form_page(str(exc)))
            return

        try:
            with GENERATE_LOCK:
                odt_path, pdf_path = generate(
                    selected_date,
                    output_dir(),
                    template_path(),
                    create_pdf=True,
                    pdf_converter=pdf_converter(),
                )
        except Exception as exc:
            self.send_html(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                error_page(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    f"Generation failed: {exc}",
                ),
            )
            return

        self.send_html(HTTPStatus.OK, result_page(selected_date, odt_path, pdf_path))

    def read_form_date(self) -> date:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            raise ValueError("Missing form body.")
        try:
            length = int(length_header)
        except ValueError as exc:
            raise ValueError("Invalid form body length.") from exc
        if length < 0 or length > MAX_FORM_BYTES:
            raise ValueError("Form body is too large.")

        body = self.rfile.read(length).decode("utf-8", errors="replace")
        values = parse_qs(body, keep_blank_values=True)
        date_values = values.get("date", [])
        if not date_values or not date_values[0]:
            raise ValueError("Choose a date.")
        try:
            return date.fromisoformat(date_values[0])
        except ValueError as exc:
            raise ValueError("Use a date in YYYY-MM-DD format.") from exc

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
    server, selected_port = create_server(host, port, port_is_explicit)
    if selected_port != port:
        print(f"Port {port} is already in use; using {selected_port} instead.")
    print(f"Serving on http://{host}:{selected_port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
