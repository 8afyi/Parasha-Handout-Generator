# Parasha Sheet Generator

## Requirements

You need LibreOffice and Python.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

You can use the installer scripts to install LibreOffice, Python, and the requirements.

Generate a sheet:

```sh
# macOS or Debian/Ubuntu
.venv/bin/python parasha_generator.py 2026-08-29

# Windows 11 PowerShell
.\.venv\Scripts\python.exe parasha_generator.py 2026-08-29
```

The script creates both files in `sheets/`:

```text
sheets/2026-08-29_ki-tavo.odt
sheets/2026-08-29_ki-tavo.pdf
```

## Web frontend

The project also includes a minimal web frontend with no extra Python dependencies.

```sh
# Debian/Ubuntu setup
./install-debian-ubuntu.sh

# Local-only web server
.venv/bin/python web_server.py

# Direct public bind on a server
PARASHA_HOST=0.0.0.0 PARASHA_PORT=8000 .venv/bin/python web_server.py
```

Open `http://SERVER:8000/`, enter a Gregorian date, click Generate, then use the
LibreOffice `.odt` and PDF download links.

Environment variables:

```text
PARASHA_HOST=127.0.0.1
PARASHA_PORT=8000
PARASHA_OUTPUT_DIR=sheets
PARASHA_TEMPLATE=template.ott
PARASHA_PDF_CONVERTER=auto
```

For a Debian systemd install, copy this project to `/opt/parashasheet`, create a
`parashasheet` service user that can write to `/opt/parashasheet`, copy
`parashasheet-web.service.example` to
`/etc/systemd/system/parashasheet-web.service`, and run:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now parashasheet-web
```

The example service binds to `127.0.0.1:8000`, which is suitable behind nginx or
another reverse proxy. Change `PARASHA_HOST` to `0.0.0.0` in the service file if
you want the Python server to listen directly on the public interface.

## Usage

```sh
# macOS or Debian/Ubuntu
.venv/bin/python parasha_generator.py YYYY-MM-DD

# Windows 11 PowerShell
.\.venv\Scripts\python.exe parasha_generator.py YYYY-MM-DD
```

The date can be any Gregorian date in the target week. The generator finds that week's Shabbat reading, fetches the diaspora Torah and haftarah readings, and builds side-by-side Hebrew/English sheets.

Common options:

```sh
# Write output somewhere other than sheets/
.venv/bin/python parasha_generator.py 2026-08-29 --output-dir output

# Use a different LibreOffice template
.venv/bin/python parasha_generator.py 2026-08-29 --template path/to/template.ott

# Generate only the ODT
.venv/bin/python parasha_generator.py 2026-08-29 --no-pdf

# Force a PDF converter
.venv/bin/python parasha_generator.py 2026-08-29 --pdf-converter libreoffice
.venv/bin/python parasha_generator.py 2026-08-29 --pdf-converter pandoc
```

## Requirements

- Python 3
- Python packages from `requirements.txt`
- LibreOffice for PDF export
- Network access the first time a parasha is generated, so the script can fetch Hebcal and Sefaria data

Fetched data is cached under `.cache/parasha_generator/`, so repeat runs for the same source text can often run offline.
