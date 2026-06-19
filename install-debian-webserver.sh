#!/usr/bin/env bash
set -Eeuo pipefail

# Debian web-server installer for Parasha Handout Generator.
#
# Run on the server as root:
#   sudo bash install-debian-webserver.sh
#
# With overrides:
#   sudo env SERVER_NAME=example.org ENABLE_UFW=1 bash install-debian-webserver.sh
#
# With HTTPS:
#   sudo env SERVER_NAME=example.org ENABLE_HTTPS=1 CERTBOT_EMAIL=admin@example.org ENABLE_UFW=1 bash install-debian-webserver.sh
#
# Common overrides:
#   REPO_URL=https://github.com/8afyi/Parasha-Handout-Generator.git
#   INSTALL_DIR=/opt/parasha
#   SERVICE_USER=parasha
#   SERVICE_NAME=parasha
#   SERVER_NAME=_                  # "_" makes nginx answer by public IP
#   DOMAIN=example.org             # Backward-compatible alias for SERVER_NAME
#   APP_PORT=8000
#   ENABLE_UFW=0                   # 1 enables ufw with 22/tcp, 80/tcp, 443/tcp
#   SSH_PORT=22
#   ENABLE_HTTPS=0                 # 1 installs Certbot and configures Let's Encrypt HTTPS
#   CERTBOT_EMAIL=admin@example.org
#   CERTBOT_DOMAINS=example.org    # defaults to SERVER_NAME; space-separate multiple names
#   CERTBOT_STAGING=0              # 1 uses Let's Encrypt staging for test installs
#   CERTBOT_REDIRECT=1             # 1 redirects HTTP to HTTPS
#   SKIP_LIBREOFFICE=0             # 1 skips PDF tools; the web app expects PDFs

REPO_URL="${REPO_URL:-https://github.com/8afyi/Parasha-Handout-Generator.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/parasha}"
SERVICE_USER="${SERVICE_USER:-parasha}"
SERVICE_NAME="${SERVICE_NAME:-parasha}"
SERVER_NAME="${SERVER_NAME:-${DOMAIN:-_}}"
APP_HOST="${APP_HOST:-127.0.0.1}"
APP_PORT="${APP_PORT:-8000}"
ENABLE_UFW="${ENABLE_UFW:-0}"
SSH_PORT="${SSH_PORT:-22}"
ENABLE_HTTPS="${ENABLE_HTTPS:-${ENABLE_CERTBOT:-0}}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"
CERTBOT_DOMAINS="${CERTBOT_DOMAINS:-$SERVER_NAME}"
CERTBOT_STAGING="${CERTBOT_STAGING:-0}"
CERTBOT_REDIRECT="${CERTBOT_REDIRECT:-1}"
SKIP_LIBREOFFICE="${SKIP_LIBREOFFICE:-0}"
DISABLE_NGINX_DEFAULT="${DISABLE_NGINX_DEFAULT:-1}"

die() {
  echo "ERROR: $*" >&2
  exit 1
}

log() {
  echo
  echo "==> $*"
}

on_error() {
  local line="$1"
  local command="$2"
  echo "ERROR: failed at line $line: $command" >&2
}
trap 'on_error "$LINENO" "$BASH_COMMAND"' ERR

usage() {
  cat <<'EOF'
Usage:
  sudo bash install-debian-webserver.sh

Environment variables:
  REPO_URL                 Git URL to deploy.
  INSTALL_DIR              Install path. Default: /opt/parasha
  SERVICE_USER             System user for the app. Default: parasha
  SERVICE_NAME             systemd/nginx site name. Default: parasha
  SERVER_NAME              nginx server_name. Default: _ for public-IP access
  DOMAIN                   Backward-compatible alias for SERVER_NAME
  APP_PORT                 Local app port behind nginx. Default: 8000
  ENABLE_UFW=1             Install/enable UFW and allow SSH/HTTP/HTTPS.
  SSH_PORT=22              SSH port to allow when ENABLE_UFW=1.
  ENABLE_HTTPS=1           Install Certbot and configure Let's Encrypt HTTPS.
  ENABLE_CERTBOT=1         Alias for ENABLE_HTTPS.
  CERTBOT_EMAIL            Email for Let's Encrypt registration. Required with ENABLE_HTTPS=1.
  CERTBOT_DOMAINS          Space-separated certificate domains. Default: SERVER_NAME.
  CERTBOT_STAGING=1        Use Let's Encrypt staging for test installs.
  CERTBOT_REDIRECT=0       Do not redirect HTTP to HTTPS. Default: redirect enabled.
  SKIP_LIBREOFFICE=1       Skip LibreOffice/font packages. PDF generation may fail.
EOF
}

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    die "run this installer as root, for example: sudo bash install-debian-webserver.sh"
  fi
}

validate_name() {
  local name="$1"
  local label="$2"
  if [[ ! "$name" =~ ^[A-Za-z0-9_.@-]+$ ]]; then
    die "$label may only contain letters, digits, dot, underscore, at-sign, and hyphen: $name"
  fi
}

validate_service_user() {
  if [[ ! "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]]; then
    die "SERVICE_USER is not a valid Debian system user name: $SERVICE_USER"
  fi
}

validate_server_name() {
  local token
  for token in $SERVER_NAME; do
    if [[ ! "$token" =~ ^[A-Za-z0-9_.-]+$ ]]; then
      die "SERVER_NAME contains an invalid nginx server_name token: $token"
    fi
  done
}

validate_bool() {
  local name="$1"
  local value="$2"
  if [ "$value" != "0" ] && [ "$value" != "1" ]; then
    die "$name must be 0 or 1; got: $value"
  fi
}

validate_certbot_domains() {
  local token
  local found=0

  for token in $CERTBOT_DOMAINS; do
    found=1
    if [[ ! "$token" =~ ^[A-Za-z0-9.-]+$ ]]; then
      die "CERTBOT_DOMAINS contains an invalid domain token: $token"
    fi
    if [ "$token" = "_" ] || [ "$token" = "localhost" ]; then
      die "ENABLE_HTTPS=1 requires a real public DNS name, not: $token"
    fi
    if [[ "$token" =~ ^[0-9]+(\.[0-9]+){3}$ ]]; then
      die "ENABLE_HTTPS=1 cannot issue a Let's Encrypt certificate for an IP address: $token"
    fi
    if [[ "$token" != *.* ]]; then
      die "ENABLE_HTTPS=1 requires fully qualified domain names; got: $token"
    fi
  done

  if [ "$found" -eq 0 ]; then
    die "ENABLE_HTTPS=1 requires CERTBOT_DOMAINS or SERVER_NAME"
  fi
}

validate_config() {
  validate_service_user
  validate_name "$SERVICE_NAME" "SERVICE_NAME"
  validate_server_name
  validate_bool "ENABLE_UFW" "$ENABLE_UFW"
  validate_bool "ENABLE_HTTPS" "$ENABLE_HTTPS"
  validate_bool "CERTBOT_STAGING" "$CERTBOT_STAGING"
  validate_bool "CERTBOT_REDIRECT" "$CERTBOT_REDIRECT"

  if [[ "$INSTALL_DIR" =~ [[:space:]] ]]; then
    die "INSTALL_DIR must not contain whitespace because it is used in systemd and nginx paths"
  fi
  case "$INSTALL_DIR" in
    ""|"/"|"/bin"|"/boot"|"/dev"|"/etc"|"/home"|"/lib"|"/lib64"|"/opt"|"/proc"|"/root"|"/run"|"/sbin"|"/sys"|"/tmp"|"/usr"|"/var")
      die "refusing unsafe INSTALL_DIR: $INSTALL_DIR"
      ;;
  esac

  if [[ ! "$APP_PORT" =~ ^[0-9]+$ ]] || [ "$APP_PORT" -lt 1 ] || [ "$APP_PORT" -gt 65535 ]; then
    die "APP_PORT must be a TCP port number from 1 to 65535"
  fi
  if [[ ! "$SSH_PORT" =~ ^[0-9]+$ ]] || [ "$SSH_PORT" -lt 1 ] || [ "$SSH_PORT" -gt 65535 ]; then
    die "SSH_PORT must be a TCP port number from 1 to 65535"
  fi
  if [ "$APP_HOST" != "127.0.0.1" ] && [ "$APP_HOST" != "localhost" ]; then
    die "APP_HOST should stay on 127.0.0.1 behind nginx; got: $APP_HOST"
  fi

  if [ "$ENABLE_HTTPS" = "1" ]; then
    if [ -z "$CERTBOT_EMAIL" ]; then
      die "ENABLE_HTTPS=1 requires CERTBOT_EMAIL, for example: CERTBOT_EMAIL=admin@example.org"
    fi
    validate_certbot_domains
  fi
}

print_config() {
  echo "Installer configuration:"
  echo "  REPO_URL=$REPO_URL"
  echo "  INSTALL_DIR=$INSTALL_DIR"
  echo "  SERVICE_USER=$SERVICE_USER"
  echo "  SERVICE_NAME=$SERVICE_NAME"
  echo "  SERVER_NAME=$SERVER_NAME"
  echo "  APP_HOST=$APP_HOST"
  echo "  APP_PORT=$APP_PORT"
  echo "  ENABLE_UFW=$ENABLE_UFW"
  echo "  SSH_PORT=$SSH_PORT"
  echo "  ENABLE_HTTPS=$ENABLE_HTTPS"
  if [ "$ENABLE_HTTPS" = "1" ]; then
    echo "  CERTBOT_EMAIL=$CERTBOT_EMAIL"
    echo "  CERTBOT_DOMAINS=$CERTBOT_DOMAINS"
    echo "  CERTBOT_STAGING=$CERTBOT_STAGING"
    echo "  CERTBOT_REDIRECT=$CERTBOT_REDIRECT"
  fi
  echo "  SKIP_LIBREOFFICE=$SKIP_LIBREOFFICE"
}

install_packages() {
  log "Installing Debian packages"
  export DEBIAN_FRONTEND=noninteractive

  apt-get update

  local packages=(
    ca-certificates
    curl
    git
    nginx
    python3
    python3-pip
    python3-venv
  )

  if [ "$SKIP_LIBREOFFICE" != "1" ]; then
    packages+=(
      fonts-dejavu-core
      fonts-noto-core
      fonts-noto-extra
      libreoffice-core
      libreoffice-writer
    )
  fi

  if [ "$ENABLE_UFW" = "1" ]; then
    packages+=(ufw)
  fi

  if [ "$ENABLE_HTTPS" = "1" ]; then
    packages+=(
      certbot
      python3-certbot-nginx
    )
  fi

  apt-get install -y --no-install-recommends "${packages[@]}"
}

create_service_user() {
  log "Creating service user"
  if id "$SERVICE_USER" >/dev/null 2>&1; then
    echo "User $SERVICE_USER already exists."
    return
  fi

  useradd \
    --system \
    --home-dir "$INSTALL_DIR" \
    --no-create-home \
    --shell /usr/sbin/nologin \
    "$SERVICE_USER"
}

ensure_install_parent() {
  local parent
  parent="$(dirname "$INSTALL_DIR")"
  install -d -m 0755 "$parent"
}

directory_is_empty() {
  [ -d "$1" ] && [ -z "$(find "$1" -mindepth 1 -maxdepth 1 -print -quit)" ]
}

default_branch_for_repo() {
  local remote_head branch
  remote_head="$(git -C "$INSTALL_DIR" symbolic-ref --quiet --short refs/remotes/origin/HEAD || true)"
  branch="${remote_head#origin/}"

  if [ -z "$branch" ] || [ "$branch" = "$remote_head" ]; then
    branch="$(git -C "$INSTALL_DIR" remote show origin | awk '/HEAD branch/ {print $NF}' || true)"
  fi

  if [ -z "$branch" ]; then
    branch="main"
  fi

  printf '%s\n' "$branch"
}

deploy_code() {
  log "Deploying application source"
  ensure_install_parent

  if [ -d "$INSTALL_DIR/.git" ]; then
    echo "Updating existing repository in $INSTALL_DIR"
    git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"
    git -C "$INSTALL_DIR" fetch --prune origin

    local branch
    branch="$(default_branch_for_repo)"
    git -C "$INSTALL_DIR" checkout -B "$branch" "origin/$branch"
    git -C "$INSTALL_DIR" reset --hard "origin/$branch"
  else
    if [ -e "$INSTALL_DIR" ]; then
      if directory_is_empty "$INSTALL_DIR"; then
        rmdir "$INSTALL_DIR"
      else
        die "$INSTALL_DIR exists and is not a git checkout. Move it aside or choose another INSTALL_DIR."
      fi
    fi

    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
  fi

  [ -f "$INSTALL_DIR/web_server.py" ] || die "web_server.py was not found after deployment"
  [ -f "$INSTALL_DIR/requirements.txt" ] || die "requirements.txt was not found after deployment"
}

setup_venv() {
  log "Creating Python virtual environment"
  python3 -m venv "$INSTALL_DIR/venv"
  "$INSTALL_DIR/venv/bin/python" -m pip install --upgrade pip wheel
  "$INSTALL_DIR/venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"
}

prepare_runtime_permissions() {
  log "Preparing runtime directories"
  local group
  group="$(id -gn "$SERVICE_USER")"

  chown -R root:root "$INSTALL_DIR"
  chmod -R u=rwX,go=rX "$INSTALL_DIR"

  install -d -m 0755 -o "$SERVICE_USER" -g "$group" "$INSTALL_DIR/sheets"
  install -d -m 0755 -o "$SERVICE_USER" -g "$group" "$INSTALL_DIR/.cache"
  install -d -m 0755 -o "$SERVICE_USER" -g "$group" "$INSTALL_DIR/.cache/parasha_generator"

  chown -R "$SERVICE_USER:$group" "$INSTALL_DIR/sheets" "$INSTALL_DIR/.cache"
}

write_systemd_service() {
  log "Writing systemd service"
  local service_path="/etc/systemd/system/$SERVICE_NAME.service"
  local group
  group="$(id -gn "$SERVICE_USER")"

  cat > "$service_path" <<EOF
[Unit]
Description=Parasha Handout Generator web service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$group
WorkingDirectory=$INSTALL_DIR
Environment=PYTHONUNBUFFERED=1
Environment=PARASHA_HOST=$APP_HOST
Environment=PARASHA_PORT=$APP_PORT
Environment=PARASHA_OUTPUT_DIR=sheets
Environment=PARASHA_TEMPLATE=template.ott
Environment=PARASHA_LARGE_TYPE_TEMPLATE=template-largetype.ott
Environment=XDG_CACHE_HOME=$INSTALL_DIR/.cache
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/web_server.py
Restart=on-failure
RestartSec=5
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME.service"
}

restart_app_service() {
  log "Restarting Python web service"
  systemctl restart "$SERVICE_NAME.service"
}

configure_nginx() {
  log "Configuring nginx"
  local nginx_conf="/etc/nginx/sites-available/$SERVICE_NAME"
  install -d -m 0755 /etc/nginx/sites-available /etc/nginx/sites-enabled

  cat > "$nginx_conf" <<EOF
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name $SERVER_NAME;

    client_max_body_size 1m;

    location / {
        proxy_pass http://127.0.0.1:$APP_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300;
    }
}
EOF

  ln -sf "$nginx_conf" "/etc/nginx/sites-enabled/$SERVICE_NAME"

  if [ "$DISABLE_NGINX_DEFAULT" = "1" ]; then
    rm -f /etc/nginx/sites-enabled/default
  fi

  nginx -t
  systemctl enable nginx
  systemctl restart nginx
}

configure_firewall() {
  if [ "$ENABLE_UFW" = "1" ]; then
    log "Enabling UFW firewall"
    ufw allow "$SSH_PORT/tcp"
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw --force enable
    return
  fi

  if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
    log "Adding HTTP/HTTPS rules to active UFW firewall"
    ufw allow 80/tcp
    ufw allow 443/tcp
  else
    log "Skipping UFW changes"
    echo "UFW is not being enabled. If your provider firewall is active, allow inbound TCP 80 and 443."
  fi
}

first_server_name() {
  local token
  for token in $SERVER_NAME; do
    printf '%s\n' "$token"
    return 0
  done
}

first_certbot_domain() {
  local token
  for token in $CERTBOT_DOMAINS; do
    printf '%s\n' "$token"
    return 0
  done
}

configure_https() {
  if [ "$ENABLE_HTTPS" != "1" ]; then
    return
  fi

  log "Configuring HTTPS with Certbot"
  local domain_args=()
  local staging_args=()
  local redirect_arg="--redirect"
  local token

  for token in $CERTBOT_DOMAINS; do
    domain_args+=("-d" "$token")
  done

  if [ "$CERTBOT_STAGING" = "1" ]; then
    staging_args+=(--staging)
  fi

  if [ "$CERTBOT_REDIRECT" = "0" ]; then
    redirect_arg="--no-redirect"
  fi

  certbot \
    --nginx \
    --non-interactive \
    --agree-tos \
    --no-eff-email \
    --email "$CERTBOT_EMAIL" \
    --keep-until-expiring \
    --expand \
    "$redirect_arg" \
    "${staging_args[@]}" \
    "${domain_args[@]}"

  nginx -t
  systemctl reload nginx
  systemctl enable certbot.timer >/dev/null 2>&1 || true
}

verify_installation() {
  log "Verifying service and local HTTP response"
  systemctl is-active --quiet "$SERVICE_NAME.service"
  curl_with_retries "http://127.0.0.1:$APP_PORT/"
  curl_with_retries "http://127.0.0.1/"

  if [ "$ENABLE_HTTPS" = "1" ]; then
    local domain
    domain="$(first_certbot_domain)"
    curl_https_with_retries "$domain"
  fi
}

curl_with_retries() {
  local url="$1"
  local attempt

  for attempt in 1 2 3 4 5; do
    if curl -fsS "$url" >/dev/null; then
      return 0
    fi
    sleep 1
  done

  curl -fsS "$url" >/dev/null
}

curl_https_with_retries() {
  local domain="$1"
  local attempt
  local curl_args=(-fsS --resolve "$domain:443:127.0.0.1")

  if [ "$CERTBOT_STAGING" = "1" ]; then
    curl_args+=(-k)
  fi

  for attempt in 1 2 3 4 5; do
    if curl "${curl_args[@]}" "https://$domain/" >/dev/null; then
      return 0
    fi
    sleep 1
  done

  curl "${curl_args[@]}" "https://$domain/" >/dev/null
}

main() {
  if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    usage
    exit 0
  fi

  require_root
  validate_config
  print_config

  install_packages
  create_service_user
  deploy_code
  setup_venv
  prepare_runtime_permissions
  write_systemd_service
  restart_app_service
  configure_nginx
  configure_firewall
  configure_https
  verify_installation

  echo
  echo "Installation complete."
  if [ "$ENABLE_HTTPS" = "1" ]; then
    echo "Open this site at: https://$(first_certbot_domain)/"
  elif [ "$SERVER_NAME" != "_" ]; then
    echo "Open this site at: http://$(first_server_name)/"
  else
    echo "Open this site at: http://SERVER_PUBLIC_IP/"
  fi
  if [ "$SERVER_NAME" != "_" ]; then
    echo "Configured nginx server_name: $SERVER_NAME"
  fi
  echo "Service status: systemctl status $SERVICE_NAME.service"
  echo "Nginx test: nginx -t"
  if [ "$ENABLE_HTTPS" = "1" ]; then
    echo "Certbot renew test: certbot renew --dry-run"
  fi
}

main "$@"
