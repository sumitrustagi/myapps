#!/usr/bin/env bash
# =============================================================================
#  Unified Component Installer (Ubuntu 20.04 / 22.04 / 24.04)
#
#  Pick one or more of the following components, give each web component an
#  FQDN, and this script will:
#    • install all required system packages
#    • install per-component Python / Node deps (per-service venv)
#    • drop static webpages into /var/www/<slug>/
#    • generate per-component nginx site configs
#    • obtain Let's Encrypt certificates via certbot --nginx (auto-renew on)
#    • create per-service systemd units + a /usr/local/bin/<service> helper
#
#  Components:
#    1) crawlr               (Python Flask, port 5000)
#    2) vg-config-converter  (Python Flask, port 5001)
#    3) notebooklm-proxy     (Node.js + Express, port 3001)
#    4) bandwidth            (static HTML)
#    5) sizing-tool          (static HTML)
#    6) hld-generator        (static HTML)
#    7) license-calc         (static HTML)
#    8) multi-caller         (Tkinter desktop GUI — no FQDN/nginx)
#
#  Usage:
#    sudo bash install.sh                       # interactive
#    sudo bash install.sh --list                # list components and exit
#    sudo bash install.sh --help                # show help
#    sudo bash install.sh \
#       --components crawlr,bandwidth \
#       --email ops@example.com \
#       --fqdn-crawlr=crawlr.example.com \
#       --fqdn-bandwidth=bw.example.com \
#       --yes
# =============================================================================
set -euo pipefail
IFS=$'\n\t'

# ── Constants ────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPONENTS_DIR="${SCRIPT_DIR}/components"
REQUIREMENTS_DIR="${SCRIPT_DIR}/requirements"

# Component slugs (must match directory names where applicable).
ALL_COMPONENTS=(
  crawlr
  vg-config-converter
  notebooklm-proxy
  bandwidth
  sizing-tool
  hld-generator
  license-calc
  multi-caller
)

# Web components (those that need an FQDN + nginx site).
declare -A COMPONENT_KIND=(
  [crawlr]=python-service
  [vg-config-converter]=python-service
  [notebooklm-proxy]=node-service
  [bandwidth]=static
  [sizing-tool]=static
  [hld-generator]=static
  [license-calc]=static
  [multi-caller]=desktop
)

# Default internal ports for service components.
declare -A DEFAULT_PORT=(
  [crawlr]=5000
  [vg-config-converter]=5001
  [notebooklm-proxy]=3001
)

declare -A COMPONENT_DESC=(
  [crawlr]="Site Monitor (Flask + Playwright crawler with weekly scheduler)"
  [vg-config-converter]="Cisco VG350 → VG410/VG420 config converter (Flask)"
  [notebooklm-proxy]="Private team frontend proxy for Google NotebookLM (Node.js)"
  [bandwidth]="Network & Bandwidth Planner (static HTML)"
  [sizing-tool]="Cisco UC Sizing Tool (static HTML)"
  [hld-generator]="Webex Calling HLD Generator (static HTML)"
  [license-calc]="Cisco UC License Calculator (static HTML)"
  [multi-caller]="Multi-Platform Bulk Call Tester (Tkinter desktop GUI — no web)"
)

# ── Colours ──────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  RED=$'\033[0;31m'; GRN=$'\033[0;32m'; YEL=$'\033[0;33m'
  CYN=$'\033[0;36m'; BLD=$'\033[1m';    RST=$'\033[0m'
else
  RED=""; GRN=""; YEL=""; CYN=""; BLD=""; RST=""
fi

ok()   { printf "%s✔%s  %s\n"  "$GRN" "$RST" "$*"; }
inf()  { printf "%s→%s  %s\n"  "$CYN" "$RST" "$*"; }
wrn()  { printf "%s⚠%s  %s\n"  "$YEL" "$RST" "$*"; }
die()  { printf "%s✖  ERROR:%s  %s\n" "$RED" "$RST" "$*" >&2; exit 1; }
hdr()  {
  printf "\n%s%s═══════════════════════════════════════════════%s\n" "$BLD" "$CYN" "$RST"
  printf "%s%s  %s%s\n" "$BLD" "$CYN" "$*" "$RST"
  printf "%s%s═══════════════════════════════════════════════%s\n\n" "$BLD" "$CYN" "$RST"
}

# ── State (populated from CLI flags / interactive prompts) ───────────────────
SELECTED_COMPONENTS=()
LE_EMAIL=""
USE_SSL="y"
ASSUME_YES="n"
LIST_ONLY="n"
declare -A FQDN=()  # FQDN[component]=domain.example.com

# ── Usage / help ─────────────────────────────────────────────────────────────
usage() {
  cat <<EOF
${BLD}Unified Component Installer${RST}

${BLD}Usage:${RST}
  sudo bash install.sh                                  # interactive
  sudo bash install.sh --list                           # list components
  sudo bash install.sh --components <c1>,<c2>,... [opts]

${BLD}Options:${RST}
  -c, --components <list>       Comma-separated component slugs (or 'all').
      --fqdn-<slug>=<domain>    FQDN for a web component (repeatable).
  -e, --email <addr>            Let's Encrypt notification email.
      --no-ssl                  Skip certbot; serve HTTP only.
  -y, --yes                     Skip confirmation prompts.
      --list                    List components and exit.
  -h, --help                    Show this help.

${BLD}Components:${RST}
$(for c in "${ALL_COMPONENTS[@]}"; do
    printf "  %-22s %s\n" "$c" "${COMPONENT_DESC[$c]}"
  done)

${BLD}Examples:${RST}
  sudo bash install.sh --list

  sudo bash install.sh --components crawlr,bandwidth \\
       --email ops@example.com \\
       --fqdn-crawlr=crawlr.example.com \\
       --fqdn-bandwidth=bw.example.com --yes

  sudo bash install.sh --components vg-config-converter --no-ssl --yes \\
       --fqdn-vg-config-converter=vg.example.com
EOF
}

list_components() {
  printf "%-22s %-15s %s\n" "SLUG" "KIND" "DESCRIPTION"
  printf "%-22s %-15s %s\n" "----" "----" "-----------"
  for c in "${ALL_COMPONENTS[@]}"; do
    printf "%-22s %-15s %s\n" "$c" "${COMPONENT_KIND[$c]}" "${COMPONENT_DESC[$c]}"
  done
}

# ── Argument parsing ─────────────────────────────────────────────────────────
parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)        usage; exit 0 ;;
      --list)           LIST_ONLY="y"; shift ;;
      -c|--components)
        if [[ "${2,,}" == "all" ]]; then
          SELECTED_COMPONENTS=("${ALL_COMPONENTS[@]}")
        else
          IFS=',' read -ra SELECTED_COMPONENTS <<<"$2"
        fi
        shift 2 ;;
      --components=*)
        local val="${1#*=}"
        if [[ "${val,,}" == "all" ]]; then
          SELECTED_COMPONENTS=("${ALL_COMPONENTS[@]}")
        else
          IFS=',' read -ra SELECTED_COMPONENTS <<<"$val"
        fi
        shift ;;
      -e|--email)       LE_EMAIL="$2"; shift 2 ;;
      --email=*)        LE_EMAIL="${1#*=}"; shift ;;
      --no-ssl)         USE_SSL="n"; shift ;;
      -y|--yes)         ASSUME_YES="y"; shift ;;
      --fqdn-*)
        local kv="${1#--fqdn-}"
        local k="${kv%%=*}"
        local v="${kv#*=}"
        [[ "$kv" == *=* && -n "$k" && -n "$v" ]] || die "Bad --fqdn-* flag: $1 (use --fqdn-<slug>=domain)"
        FQDN["$k"]="$v"
        shift ;;
      *) die "Unknown argument: $1 (try --help)" ;;
    esac
  done
}

# ── Pre-flight checks ────────────────────────────────────────────────────────
preflight() {
  [[ $EUID -eq 0 ]] || die "Run as root: sudo bash install.sh"

  if [[ ! -r /etc/os-release ]]; then
    die "Cannot detect OS — /etc/os-release missing."
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian) ok "Detected ${PRETTY_NAME:-$ID}" ;;
    *) wrn "Detected ${PRETTY_NAME:-$ID} — script targets Ubuntu/Debian; continuing best-effort." ;;
  esac

  for d in "$COMPONENTS_DIR" "$REQUIREMENTS_DIR"; do
    [[ -d "$d" ]] || die "Required directory missing: $d (run from the deploy/ folder)"
  done
}

# Verify every supplied component slug is one we know about.
validate_components() {
  for c in "${SELECTED_COMPONENTS[@]}"; do
    [[ -n "${COMPONENT_KIND[$c]:-}" ]] || die "Unknown component: '$c' (try: bash install.sh --list)"
  done

  # Verify every --fqdn-<slug> flag also matches a known web component.
  for k in "${!FQDN[@]}"; do
    [[ -n "${COMPONENT_KIND[$k]:-}" ]] || die "FQDN given for unknown component: '$k'"
    case "${COMPONENT_KIND[$k]}" in
      desktop) wrn "FQDN given for desktop-only '$k' will be ignored." ;;
    esac
  done
}

# ── Interactive component picker ─────────────────────────────────────────────
prompt_components() {
  if [[ ${#SELECTED_COMPONENTS[@]} -gt 0 ]]; then
    return
  fi
  hdr "Component selection"
  local i=1
  for c in "${ALL_COMPONENTS[@]}"; do
    printf "  %2d) %-22s %s\n" "$i" "$c" "${COMPONENT_DESC[$c]}"
    ((i++))
  done
  echo ""
  echo "Enter comma-separated numbers (e.g. 1,3,5) or 'all' for everything."
  read -rp "Components: " choice
  choice="${choice// /}"
  if [[ "${choice,,}" == "all" ]]; then
    SELECTED_COMPONENTS=("${ALL_COMPONENTS[@]}")
    return
  fi
  IFS=',' read -ra picks <<<"$choice"
  for p in "${picks[@]}"; do
    [[ "$p" =~ ^[0-9]+$ ]] || die "Not a number: $p"
    (( p >= 1 && p <= ${#ALL_COMPONENTS[@]} )) || die "Out of range: $p"
    SELECTED_COMPONENTS+=("${ALL_COMPONENTS[p-1]}")
  done
  [[ ${#SELECTED_COMPONENTS[@]} -gt 0 ]] || die "No components selected."
}

# ── Per-component FQDN prompts ───────────────────────────────────────────────
prompt_fqdns() {
  local needs_email="n"
  for c in "${SELECTED_COMPONENTS[@]}"; do
    case "${COMPONENT_KIND[$c]:-}" in
      python-service|node-service|static)
        if [[ -z "${FQDN[$c]:-}" ]]; then
          read -rp "FQDN for ${BLD}${c}${RST} (e.g. ${c}.example.com — leave blank to skip): " val
          FQDN[$c]="$val"
        fi
        if [[ -n "${FQDN[$c]:-}" && "$USE_SSL" == "y" ]]; then
          needs_email="y"
        fi
        ;;
      desktop)
        : ;;  # no FQDN
      *) die "Unknown component kind for $c" ;;
    esac
  done

  if [[ "$needs_email" == "y" && -z "$LE_EMAIL" ]]; then
    while true; do
      read -rp "Email for Let's Encrypt renewal notices: " LE_EMAIL
      [[ "$LE_EMAIL" == *@* ]] && break
      wrn "That doesn't look like an email."
    done
  fi
}

confirm_summary() {
  hdr "Summary"
  echo "  Components:"
  for c in "${SELECTED_COMPONENTS[@]}"; do
    local f="${FQDN[$c]:-<no FQDN>}"
    printf "    • %-22s  %-15s  %s\n" "$c" "${COMPONENT_KIND[$c]}" "$f"
  done
  echo "  Use SSL (Let's Encrypt) : ${USE_SSL}"
  [[ -n "$LE_EMAIL" ]] && echo "  LE email                : ${LE_EMAIL}"
  echo ""
  if [[ "$ASSUME_YES" != "y" ]]; then
    read -rp "Proceed? [y/N]: " c
    [[ "${c,,}" == "y" ]] || { echo "Aborted."; exit 0; }
  fi
}

# ── System packages ──────────────────────────────────────────────────────────
apt_update_done="n"
apt_update_once() {
  if [[ "$apt_update_done" != "y" ]]; then
    inf "Running apt-get update…"
    apt-get update -qq
    apt_update_done="y"
  fi
}

apt_install() {
  apt_update_once
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@"
}

install_common_packages() {
  hdr "System packages"
  local pkgs=(curl wget ca-certificates gnupg lsb-release jq)
  apt_install "${pkgs[@]}"

  # nginx is installed if any component is web-facing.
  for c in "${SELECTED_COMPONENTS[@]}"; do
    case "${COMPONENT_KIND[$c]}" in
      python-service|node-service|static)
        if ! command -v nginx >/dev/null 2>&1; then
          inf "Installing nginx"
          apt_install nginx
        fi
        # Disable the default site so we don't fight over server_name _;
        rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
        break ;;
    esac
  done
  ok "Base packages ready"
}

# ── Certbot install + auto-renew ─────────────────────────────────────────────
ensure_certbot() {
  [[ "$USE_SSL" == "y" ]] || return 0
  if command -v certbot >/dev/null 2>&1; then
    ok "certbot already present: $(certbot --version 2>&1 | head -n1)"
    return 0
  fi
  inf "Installing certbot…"
  if command -v snap >/dev/null 2>&1; then
    snap install --classic certbot >/dev/null 2>&1 || true
    if command -v /snap/bin/certbot >/dev/null 2>&1 && ! command -v certbot >/dev/null 2>&1; then
      ln -sf /snap/bin/certbot /usr/bin/certbot
    fi
  fi
  if ! command -v certbot >/dev/null 2>&1; then
    apt_install certbot python3-certbot-nginx
  fi
  command -v certbot >/dev/null 2>&1 || die "certbot install failed"
  ok "certbot installed"
}

ensure_renewal_timer() {
  [[ "$USE_SSL" == "y" ]] || return 0
  # Prefer the systemd timer (snap or apt).
  if systemctl list-unit-files 2>/dev/null | grep -q '^snap.certbot.renew.timer'; then
    systemctl enable --now snap.certbot.renew.timer >/dev/null 2>&1 || true
    ok "Auto-renew via snap.certbot.renew.timer"
  elif systemctl list-unit-files 2>/dev/null | grep -q '^certbot.timer'; then
    systemctl enable --now certbot.timer >/dev/null 2>&1 || true
    ok "Auto-renew via certbot.timer"
  else
    cat >/etc/cron.d/certbot-renew <<'EOF'
# Renew Let's Encrypt certificates twice a day; reload nginx on success.
0 */12 * * * root certbot -q renew --deploy-hook "systemctl reload nginx"
EOF
    chmod 644 /etc/cron.d/certbot-renew
    ok "Auto-renew via /etc/cron.d/certbot-renew"
  fi

  # Always (re)install a deploy hook so nginx picks up renewed certs.
  mkdir -p /etc/letsencrypt/renewal-hooks/deploy
  cat >/etc/letsencrypt/renewal-hooks/deploy/00-reload-nginx.sh <<'EOF'
#!/bin/sh
systemctl reload nginx 2>/dev/null || true
EOF
  chmod +x /etc/letsencrypt/renewal-hooks/deploy/00-reload-nginx.sh
}

# Issue (or renew, idempotent) a cert for one or more domains, and let certbot
# rewrite the relevant nginx site config to add the SSL server block.
issue_cert() {
  [[ "$USE_SSL" == "y" ]] || return 0
  local domain="$1"
  [[ -n "$domain" ]] || return 0
  if [[ -d "/etc/letsencrypt/live/${domain}" ]]; then
    ok "Cert already present for ${domain}"
  else
    inf "Issuing Let's Encrypt cert for ${domain}…"
    certbot --nginx --non-interactive --agree-tos \
            --email "$LE_EMAIL" \
            --domain "$domain" \
            --redirect --keep-until-expiring \
      || wrn "certbot failed for ${domain} — DNS may not be pointed yet. You can re-run: certbot --nginx -d ${domain}"
  fi
}

# Ensure nginx is enabled and running, then reload it after config changes.
nginx_apply() {
  systemctl enable nginx >/dev/null 2>&1 || true
  if systemctl is-active --quiet nginx; then
    systemctl reload nginx
  else
    systemctl start nginx
  fi
}

# ── nginx site helpers ───────────────────────────────────────────────────────
write_nginx_proxy_site() {
  local slug="$1" domain="$2" upstream_port="$3" extra="$4"
  local conf="/etc/nginx/sites-available/${slug}"
  cat >"$conf" <<EOF
# Generated by unified installer for component '${slug}'.
upstream ${slug}_upstream {
    server 127.0.0.1:${upstream_port} fail_timeout=5s max_fails=3;
    keepalive 16;
}

server {
    listen 80;
    listen [::]:80;
    server_name ${domain};

    client_max_body_size 32m;
    proxy_read_timeout    300s;
    proxy_connect_timeout 10s;

    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/javascript;

${extra}
    location / {
        proxy_pass         http://${slug}_upstream;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade           \$http_upgrade;
        proxy_set_header   Connection        "upgrade";
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
    }
}
EOF
  ln -sf "$conf" "/etc/nginx/sites-enabled/${slug}"
  nginx -t >/dev/null
  nginx_apply
  ok "nginx site enabled: /etc/nginx/sites-available/${slug}"
}

write_nginx_static_site() {
  local slug="$1" domain="$2" webroot="$3"
  local conf="/etc/nginx/sites-available/${slug}"
  cat >"$conf" <<EOF
# Generated by unified installer for static component '${slug}'.
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};

    root ${webroot};
    index index.html;

    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/javascript image/svg+xml;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    # Long cache for static asset extensions.
    location ~* \.(?:css|js|svg|png|jpe?g|gif|woff2?|ttf|ico)\$ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
EOF
  ln -sf "$conf" "/etc/nginx/sites-enabled/${slug}"
  nginx -t >/dev/null
  nginx_apply
  ok "nginx static site enabled: /etc/nginx/sites-available/${slug}"
}

# ── systemd helpers ──────────────────────────────────────────────────────────
write_systemd_unit() {
  local name="$1" desc="$2" workdir="$3" execstart="$4" user="$5" envfile="${6:-}"
  local extra_env="${7:-}"
  local unit="/etc/systemd/system/${name}.service"
  {
    echo "[Unit]"
    echo "Description=${desc}"
    echo "After=network.target"
    echo "Wants=network-online.target"
    echo ""
    echo "[Service]"
    echo "Type=simple"
    echo "User=${user}"
    echo "Group=${user}"
    echo "WorkingDirectory=${workdir}"
    [[ -n "$envfile"   ]] && echo "EnvironmentFile=${envfile}"
    [[ -n "$extra_env" ]] && echo "${extra_env}"
    echo "ExecStart=${execstart}"
    echo "Restart=on-failure"
    echo "RestartSec=8"
    echo "NoNewPrivileges=yes"
    echo "PrivateTmp=yes"
    echo "ProtectSystem=strict"
    echo "ProtectHome=yes"
    echo "ReadWritePaths=${workdir}"
    echo ""
    echo "[Install]"
    echo "WantedBy=multi-user.target"
  } >"$unit"
  systemctl daemon-reload
  systemctl enable --now "${name}.service" >/dev/null 2>&1 || true
}

write_management_helper() {
  local name="$1"
  local helper="/usr/local/bin/${name}"
  cat >"$helper" <<EOF
#!/usr/bin/env bash
# Management helper for systemd service '${name}'.
set -euo pipefail
case "\${1:-help}" in
  start)     systemctl start  ${name} ;;
  stop)      systemctl stop   ${name} ;;
  restart)   systemctl restart ${name} ;;
  status)    systemctl status ${name} --no-pager ;;
  logs)      journalctl -u ${name} -f --no-pager ;;
  renew-ssl) certbot renew --quiet && systemctl reload nginx ;;
  help|*)
    cat <<USAGE
${name} <command>
  start       Start the service
  stop        Stop the service
  restart     Restart the service
  status      Show service status
  logs        Tail logs
  renew-ssl   Force a certbot renewal + nginx reload
USAGE
  ;;
esac
EOF
  chmod +x "$helper"
}

ensure_user() {
  local user="$1" home="$2"
  if ! id "$user" >/dev/null 2>&1; then
    useradd --system --shell /usr/sbin/nologin --home-dir "$home" --create-home "$user"
    ok "Created system user: ${user}"
  fi
}

# =============================================================================
# Per-component installers
# =============================================================================

# ── crawlr ───────────────────────────────────────────────────────────────────
install_crawlr() {
  hdr "Installing component: crawlr"
  local slug="crawlr"
  local install_dir="/opt/${slug}"
  local user="${slug}"
  local port="${DEFAULT_PORT[$slug]}"
  local src="${COMPONENTS_DIR}/${slug}"
  local domain="${FQDN[$slug]:-}"

  apt_install python3 python3-venv python3-dev build-essential \
              libssl-dev libffi-dev libxml2-dev libxslt1-dev zlib1g-dev libjpeg-dev

  ensure_user "$user" "$install_dir"
  mkdir -p "${install_dir}"/{pdfs,logs,templates}
  cp "${src}"/{app.py,crawler.py,database.py,pdf_utils.py,scheduler_jobs.py} "$install_dir/"
  cp -r "${src}/templates/." "${install_dir}/templates/"

  python3 -m venv "${install_dir}/venv"
  "${install_dir}/venv/bin/pip" install --upgrade pip wheel >/dev/null
  "${install_dir}/venv/bin/pip" install -r "${REQUIREMENTS_DIR}/${slug}.txt" >/dev/null
  ok "Python deps installed"

  inf "Installing Playwright Chromium (this can take a minute)…"
  PLAYWRIGHT_BROWSERS_PATH="${install_dir}/.playwright-browsers" \
    "${install_dir}/venv/bin/playwright" install chromium --with-deps >/dev/null 2>&1 \
    || wrn "Playwright install hit a snag — re-run: sudo -u ${user} ${install_dir}/venv/bin/playwright install chromium --with-deps"

  if [[ -f "${install_dir}/.env" ]]; then
    wrn "Keeping existing ${install_dir}/.env (delete to regenerate)"
  else
    cat >"${install_dir}/.env" <<EOF
CRAWLR_HOST=127.0.0.1
CRAWLR_PORT=${port}
CRAWLR_DB=${install_dir}/crawler.db
CRAWLR_PDF_DIR=${install_dir}/pdfs
CRAWLR_LOG=${install_dir}/logs/crawlr.log
PLAYWRIGHT_BROWSERS_PATH=${install_dir}/.playwright-browsers
EOF
  fi
  chmod 600 "${install_dir}/.env"

  chown -R "${user}:${user}" "$install_dir"
  chmod 750 "$install_dir"
  chmod 600 "${install_dir}/.env"
  chmod 755 "${install_dir}/pdfs" "${install_dir}/logs"

  write_systemd_unit "$slug" "CRAWLR Site Monitor" "$install_dir" \
    "${install_dir}/venv/bin/python ${install_dir}/app.py" "$user" \
    "${install_dir}/.env" \
    "Environment=PATH=${install_dir}/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

  write_management_helper "$slug"

  if [[ -n "$domain" ]]; then
    local sse_block
    sse_block=$(cat <<NGX

    # Server-Sent Events — disable buffering.
    location /api/stream {
        proxy_pass         http://${slug}_upstream;
        proxy_http_version 1.1;
        proxy_set_header   Connection "";
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 86400s;
        chunked_transfer_encoding on;
    }
NGX
)
    write_nginx_proxy_site "$slug" "$domain" "$port" "$sse_block"
    issue_cert "$domain"
  fi

  ok "crawlr installed (port ${port}; service: ${slug})"
}

# ── vg-config-converter ──────────────────────────────────────────────────────
install_vg_config_converter() {
  hdr "Installing component: vg-config-converter"
  local slug="vg-config-converter"
  local install_dir="/opt/${slug}"
  local user="vgconv"
  local port="${DEFAULT_PORT[$slug]}"
  local src="${COMPONENTS_DIR}/${slug}"
  local domain="${FQDN[$slug]:-}"

  apt_install python3 python3-venv python3-dev build-essential

  ensure_user "$user" "$install_dir"
  mkdir -p "$install_dir"
  cp "${src}/app.py" "${install_dir}/app.py"
  cp -r "${src}/templates" "${install_dir}/templates"
  cp -r "${src}/static"    "${install_dir}/static"

  python3 -m venv "${install_dir}/venv"
  "${install_dir}/venv/bin/pip" install --upgrade pip wheel >/dev/null
  "${install_dir}/venv/bin/pip" install -r "${REQUIREMENTS_DIR}/${slug}.txt" >/dev/null
  ok "Python deps installed"

  chown -R "${user}:${user}" "$install_dir"
  chmod 750 "$install_dir"

  write_systemd_unit "$slug" "VG Config Converter (Flask)" "$install_dir" \
    "${install_dir}/venv/bin/gunicorn --workers 2 --bind 127.0.0.1:${port} --access-logfile - --error-logfile - app:app" \
    "$user" "" \
    "Environment=PATH=${install_dir}/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

  write_management_helper "$slug"

  if [[ -n "$domain" ]]; then
    write_nginx_proxy_site "$slug" "$domain" "$port" ""
    issue_cert "$domain"
  fi

  ok "vg-config-converter installed (port ${port}; service: ${slug})"
}

# ── notebooklm-proxy ─────────────────────────────────────────────────────────
install_notebooklm_proxy() {
  hdr "Installing component: notebooklm-proxy"
  local slug="notebooklm-proxy"
  local install_dir="/opt/${slug}"
  local user="nblm"
  local port="${DEFAULT_PORT[$slug]}"
  local src="${COMPONENTS_DIR}/${slug}"
  local domain="${FQDN[$slug]:-}"

  # Node.js 20 via NodeSource if missing or older than 20.
  local need_node="n"
  if ! command -v node >/dev/null 2>&1; then
    need_node="y"
  else
    local major; major="$(node -v | sed 's/^v//' | cut -d. -f1)"
    [[ "$major" =~ ^[0-9]+$ ]] && (( major < 20 )) && need_node="y"
  fi
  if [[ "$need_node" == "y" ]]; then
    inf "Installing Node.js 20…"
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null 2>&1
    apt_install nodejs
  fi
  ok "Node.js: $(node -v)"

  # Chromium (system) so Puppeteer doesn't have to download.
  apt_install chromium-browser ca-certificates fonts-liberation libnss3 libxss1 libasound2 \
              libatk-bridge2.0-0 libatk1.0-0 libcups2 libdbus-1-3 libgbm1 libgtk-3-0 \
              libpangocairo-1.0-0 libxcomposite1 libxdamage1 libxrandr2 xdg-utils \
              || wrn "Some Chromium deps missing — Puppeteer may download its own browser."

  ensure_user "$user" "$install_dir"
  mkdir -p "${install_dir}/logs" "${install_dir}/session-data"

  # Copy source but preserve persistent state (session-data, .env) across re-runs.
  # rsync's --exclude keeps a previously-completed Google login intact.
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --exclude='node_modules/' --exclude='session-data/' --exclude='.env' \
          "${src}/" "${install_dir}/"
  else
    apt_install rsync
    rsync -a --exclude='node_modules/' --exclude='session-data/' --exclude='.env' \
          "${src}/" "${install_dir}/"
  fi
  rm -rf "${install_dir}/node_modules"

  pushd "$install_dir" >/dev/null
  PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true npm install --silent --no-audit --no-fund
  popd >/dev/null
  ok "Node deps installed"

  local chromium_path
  chromium_path="$(command -v chromium-browser || command -v chromium || echo "")"

  if [[ -f "${install_dir}/.env" ]]; then
    wrn "Keeping existing ${install_dir}/.env (delete to regenerate)"
  else
    cat >"${install_dir}/.env" <<EOF
PORT=${port}
NOTEBOOK_URL=https://notebooklm.google.com/notebook/REPLACE_ME
ALLOWED_ORIGIN=${domain:+https://${domain}}
EOF
    [[ -n "$chromium_path" ]] && echo "PUPPETEER_EXECUTABLE_PATH=${chromium_path}" >>"${install_dir}/.env"
  fi
  chmod 600 "${install_dir}/.env"

  chown -R "${user}:${user}" "$install_dir"
  chmod 750 "$install_dir"
  chmod 600 "${install_dir}/.env"

  write_systemd_unit "$slug" "NotebookLM Proxy (Express)" "$install_dir" \
    "/usr/bin/env node ${install_dir}/server.js" "$user" \
    "${install_dir}/.env" \
    "Environment=NODE_ENV=production"

  write_management_helper "$slug"

  if [[ -n "$domain" ]]; then
    write_nginx_proxy_site "$slug" "$domain" "$port" ""
    issue_cert "$domain"
  fi

  wrn "notebooklm-proxy: edit ${install_dir}/.env to set NOTEBOOK_URL, then run:"
  wrn "  sudo -u ${user} node ${install_dir}/loginOnce.js   # one-time Google login"
  wrn "  systemctl restart ${slug}"
  ok "notebooklm-proxy installed (port ${port}; service: ${slug})"
}

# ── Static webpages ──────────────────────────────────────────────────────────
install_static() {
  local slug="$1"
  hdr "Installing static component: ${slug}"
  local domain="${FQDN[$slug]:-}"
  local src="${COMPONENTS_DIR}/webpages/${slug}"
  local webroot="/var/www/${slug}"

  [[ -f "${src}/index.html" ]] || die "Missing static source: ${src}/index.html"

  mkdir -p "$webroot"
  # Clean stale files from a previous deploy without nuking the directory
  # itself (preserves any unrelated content the admin has placed there).
  find "$webroot" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  cp -r "${src}/." "${webroot}/"
  chown -R www-data:www-data "$webroot"
  chmod -R a+rX "$webroot"
  ok "Deployed: ${webroot}"

  if [[ -n "$domain" ]]; then
    write_nginx_static_site "$slug" "$domain" "$webroot"
    issue_cert "$domain"
  else
    wrn "No FQDN given for ${slug} — files deployed to ${webroot} but no nginx site was created."
    wrn "You can later: bash install.sh --components ${slug} --fqdn-${slug}=YOUR.DOMAIN --email YOU@x"
  fi
}

# ── multi-caller (Tkinter desktop app) ───────────────────────────────────────
install_multi_caller() {
  hdr "Installing component: multi-caller (desktop Tkinter)"
  local slug="multi-caller"
  local install_dir="/opt/${slug}"
  local src="${COMPONENTS_DIR}/${slug}"

  apt_install python3 python3-venv python3-tk
  mkdir -p "$install_dir"
  cp "${src}"/*.py "$install_dir/"
  cp "${src}/sample_numbers.csv" "$install_dir/" 2>/dev/null || true

  python3 -m venv --system-site-packages "${install_dir}/venv"
  "${install_dir}/venv/bin/pip" install --upgrade pip wheel >/dev/null
  "${install_dir}/venv/bin/pip" install -r "${REQUIREMENTS_DIR}/${slug}.txt" >/dev/null

  cat >/usr/local/bin/multi-caller <<EOF
#!/usr/bin/env bash
# Launcher for the Multi-Platform Bulk Call Tester (Tkinter GUI).
# Requires a desktop session (DISPLAY) — does not run headlessly.
exec ${install_dir}/venv/bin/python ${install_dir}/multi_caller.py "\$@"
EOF
  chmod +x /usr/local/bin/multi-caller
  ok "multi-caller installed. Launch with:  multi-caller   (needs a desktop / X11 DISPLAY)"
}

# ── Dispatcher ───────────────────────────────────────────────────────────────
run_installs() {
  install_common_packages
  ensure_certbot

  for c in "${SELECTED_COMPONENTS[@]}"; do
    case "$c" in
      crawlr)               install_crawlr ;;
      vg-config-converter)  install_vg_config_converter ;;
      notebooklm-proxy)     install_notebooklm_proxy ;;
      bandwidth|sizing-tool|hld-generator|license-calc) install_static "$c" ;;
      multi-caller)         install_multi_caller ;;
      *) die "Unknown component: $c" ;;
    esac
  done

  ensure_renewal_timer
}

print_summary() {
  hdr "Done"
  for c in "${SELECTED_COMPONENTS[@]}"; do
    local kind="${COMPONENT_KIND[$c]}"
    local domain="${FQDN[$c]:-}"
    case "$kind" in
      python-service|node-service)
        local port="${DEFAULT_PORT[$c]}"
        if [[ -n "$domain" ]]; then
          if [[ "$USE_SSL" == "y" && -d "/etc/letsencrypt/live/${domain}" ]]; then
            printf "  • %-22s  https://%s\n" "$c" "$domain"
          else
            printf "  • %-22s  http://%s   (no cert yet)\n" "$c" "$domain"
          fi
        else
          printf "  • %-22s  http://127.0.0.1:%s   (no FQDN)\n" "$c" "$port"
        fi
        printf "      Helper: %s {start|stop|restart|status|logs|renew-ssl}\n" "$c"
        ;;
      static)
        if [[ -n "$domain" ]]; then
          if [[ "$USE_SSL" == "y" && -d "/etc/letsencrypt/live/${domain}" ]]; then
            printf "  • %-22s  https://%s\n" "$c" "$domain"
          else
            printf "  • %-22s  http://%s   (no cert yet)\n" "$c" "$domain"
          fi
        else
          printf "  • %-22s  /var/www/%s   (deploy only — no nginx site)\n" "$c" "$c"
        fi
        ;;
      desktop)
        printf "  • %-22s  Run:  multi-caller  (needs DISPLAY)\n" "$c"
        ;;
    esac
  done
  echo ""
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
  parse_args "$@"
  if [[ "$LIST_ONLY" == "y" ]]; then
    list_components
    exit 0
  fi
  preflight
  prompt_components
  validate_components
  prompt_fqdns
  confirm_summary
  run_installs
  print_summary
}

main "$@"
