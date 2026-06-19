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

Open `http://SERVER:8000/`, choose a Gregorian date, type size, and text
options, click Generate, then use the LibreOffice `.odt` and PDF download
links.
If the default local port is already in use and `PARASHA_PORT` is not set, the
server automatically tries the next available port.
Generated `.odt` and `.pdf` downloads are deleted after one hour by default.

Environment variables:

```text
PARASHA_HOST=127.0.0.1
PARASHA_PORT=8000
PARASHA_OUTPUT_DIR=sheets
PARASHA_OUTPUT_RETENTION_SECONDS=3600
PARASHA_TEMPLATE=template.ott
PARASHA_LARGE_TYPE_TEMPLATE=template-largetype.ott
PARASHA_PDF_CONVERTER=auto
PARASHA_REPLACE_DIVINE_NAMES=1
PARASHA_TETRAGRAMMATON_REPLACEMENT=יקוק
PARASHA_YAH_REPLACEMENT=קה
PARASHA_ELOHIM_REPLACEMENT=אלקים
PARASHA_ELOHIM_STEM_REPLACEMENT=אלק
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

## Deploy (Debian/Ubuntu)

A convenience installer is included to set up the app, a Python virtualenv,
a `systemd` service and an nginx reverse proxy on Debian/Ubuntu systems.

Run as root on your server (override environment variables to customize):

```sh
# default install (includes LibreOffice and configures nginx for public-IP access)
sudo bash install-debian-webserver.sh

# customize install dir and domain/server name
sudo env \
  REPO_URL=https://github.com/8afyi/Parasha-Handout-Generator.git \
  INSTALL_DIR=/opt/parasha \
  SERVER_NAME=example.org \
  bash install-debian-webserver.sh

# enable HTTPS with Let's Encrypt/Certbot
sudo env \
  SERVER_NAME=example.org \
  ENABLE_HTTPS=1 \
  CERTBOT_EMAIL=admin@example.org \
  ENABLE_UFW=1 \
  SSH_PORT=22 \
  bash install-debian-webserver.sh

# enable UFW and allow SSH, HTTP, and HTTPS
sudo env ENABLE_UFW=1 SSH_PORT=22 bash install-debian-webserver.sh
```

After installation, verify the service and nginx:

```sh
systemctl status parasha.service
nginx -t
sudo ufw status
```

By default the nginx `server_name` is `_`, so the site should answer at
`http://SERVER_PUBLIC_IP/` without a domain. The installer does not enable UFW
unless `ENABLE_UFW=1` is set, but it will add HTTP/HTTPS rules if UFW is already
active.

HTTPS requires a real domain name; Let's Encrypt will not issue a certificate
for the default `_` server name or a bare IP address. Before running with
`ENABLE_HTTPS=1`, point the domain's A/AAAA records to the server and allow
inbound TCP 80 and 443 in your provider firewall. The installer installs
`certbot` and `python3-certbot-nginx`, requests the certificate, configures
nginx, and enables Certbot's renewal timer.

For multiple hostnames, include all names in `SERVER_NAME` or override
`CERTBOT_DOMAINS`:

```sh
sudo env \
  'SERVER_NAME=example.org www.example.org' \
  'CERTBOT_DOMAINS=example.org www.example.org' \
  ENABLE_HTTPS=1 \
  CERTBOT_EMAIL=admin@example.org \
  bash install-debian-webserver.sh
```

Useful HTTPS checks:

```sh
sudo certbot certificates
sudo certbot renew --dry-run
sudo ss -ltnp | grep -E ':(80|443)\b'
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

# English only, using JPS 2023
.venv/bin/python parasha_generator.py 2026-08-29 --language-mode english --english-version jps-2023

# Hebrew only, using ta'amei hamikra
.venv/bin/python parasha_generator.py 2026-08-29 --language-mode hebrew --hebrew-version taamim

# Leave Hebrew Divine names unchanged
.venv/bin/python parasha_generator.py 2026-08-29 --no-replace-divine-names
```

Text option slugs:

```text
--language-mode bilingual|english|hebrew
--english-version koren|jps-2023|jps-1985|jps-1917
--hebrew-version nikkud|taamim|text-only
```

The default output remains bilingual Koren English with `Tanach with Nikkud`.

Hebrew Divine names are replaced by default before the sheet is written. Use
`--no-replace-divine-names` or set `PARASHA_REPLACE_DIVINE_NAMES=0` to disable
this, or override the replacement strings with the environment variables listed
above.

## Requirements

- Python 3
- Python packages from `requirements.txt`
- LibreOffice for PDF export
- Network access the first time a parasha is generated, so the script can fetch Hebcal and Sefaria data

Fetched data is cached under `.cache/parasha_generator/`, so repeat runs for the same source text can often run offline.
