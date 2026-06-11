#!/usr/bin/env bash
set -euo pipefail

# Simple Debian/Ubuntu installer for Parasha Handout Generator web app
# Usage:
#   sudo ./install-debian-webserver.sh [options]
# Environment variables (override as needed):
#   REPO_URL - Git URL to clone (default: https://github.com/8afyi/Parasha-Handout-Generator.git)
#   INSTALL_DIR - Installation directory (default: /opt/parasha)
#   SERVICE_USER - System user to run the service (default: parasha)
#   DOMAIN - Optional domain name to put in nginx `server_name` (default: _ )
#   SKIP_LIBREOFFICE - if set to "1", skip installing LibreOffice and pandoc (default: 0)

REPO_URL="${REPO_URL:-https://github.com/8afyi/Parasha-Handout-Generator.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/parasha}"
SERVICE_USER="${SERVICE_USER:-parasha}"
SERVICE_NAME="parasha"
DOMAIN="${DOMAIN:-_}"
SKIP_LIBREOFFICE="${SKIP_LIBREOFFICE:-0}"

echo "Installer configuration:"
echo "  REPO_URL=$REPO_URL"
echo "  INSTALL_DIR=$INSTALL_DIR"
echo "  SERVICE_USER=$SERVICE_USER"
echo "  DOMAIN=$DOMAIN"
echo "  SKIP_LIBREOFFICE=$SKIP_LIBREOFFICE"

if [ "$(id -u)" -ne 0 ]; then
  echo "This installer must be run as root (sudo)." >&2
  exit 1
fi

apt_update() {
  echo "Updating package list..."
  apt-get update -y
}

install_packages() {
  echo "Installing packages..."
  local pkgs=(python3 python3-venv python3-pip nginx git curl ufw)
  if [ "$SKIP_LIBREOFFICE" != "1" ]; then
    pkgs+=(libreoffice-core libreoffice-writer pandoc)
  fi
  apt-get install -y "${pkgs[@]}"
}

create_service_user() {
  if id "$SERVICE_USER" &>/dev/null; then
    echo "User $SERVICE_USER already exists."
  else
    echo "Creating system user $SERVICE_USER..."
    useradd --system --create-home --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER" || true
  fi
}

checkout_code() {
  echo "Deploying application to $INSTALL_DIR..."
  if [ -d "$INSTALL_DIR/.git" ]; then
    echo "Repository already present, pulling latest changes..."
    git -C "$INSTALL_DIR" fetch --all --prune
    git -C "$INSTALL_DIR" reset --hard origin/HEAD || true
  else
    rm -rf "$INSTALL_DIR"
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
  fi
  chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
}

setup_venv() {
  echo "Setting up Python virtualenv..."
  python3 -m venv "$INSTALL_DIR/venv"
  # Ensure pip is current
  "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
  if [ -f "$INSTALL_DIR/requirements.txt" ]; then
    "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
  fi
  chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/venv"
}

write_systemd_service() {
  echo "Writing systemd service file..."
  cat > "/etc/systemd/system/$SERVICE_NAME.service" <<EOF
[Unit]
Description=Parasha Handout Generator web service
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment=PARASHA_HOST=127.0.0.1
Environment=PARASHA_PORT=8000
Environment=PARASHA_OUTPUT_DIR=sheets
Environment=PARASHA_TEMPLATE=template.ott
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/web_server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now "$SERVICE_NAME.service"
}

configure_nginx() {
  echo "Configuring nginx reverse proxy..."
  local nginx_conf="/etc/nginx/sites-available/$SERVICE_NAME"
  cat > "$nginx_conf" <<'EOF'
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
  ln -sf "$nginx_conf" "/etc/nginx/sites-enabled/$SERVICE_NAME"
  # Remove default site to avoid conflicts
  if [ -f /etc/nginx/sites-enabled/default ]; then
    rm -f /etc/nginx/sites-enabled/default
  fi
  nginx -t
  systemctl restart nginx
}

configure_firewall() {
  echo "Configuring UFW firewall (allow OpenSSH and Nginx Full)..."
  # Allow SSH first to avoid locking out
  ufw allow OpenSSH
  ufw allow 'Nginx Full'
  if ufw status | grep -q inactive; then
    ufw --force enable
  fi
}

main() {
  apt_update
  install_packages
  create_service_user
  checkout_code
  setup_venv
  write_systemd_service
  configure_nginx
  configure_firewall

  echo
  echo "Installation complete."
  echo "The service is running: systemctl status $SERVICE_NAME.service"
  echo "If you have a real domain, point it to this server and consider running Certbot for HTTPS."
}

main "$@"
