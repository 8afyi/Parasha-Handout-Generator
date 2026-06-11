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
