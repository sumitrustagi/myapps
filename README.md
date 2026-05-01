# Unified Component Installer

A single Bash installer that lets you pick **one or more** of 8 components and
deploys them to a single Ubuntu host, with nginx + Let's Encrypt and
auto-renewal handled for you.

Tested on Ubuntu **20.04 / 22.04 / 24.04**.

## Components

| Slug                  | Kind             | Default port | Notes                                                            |
|-----------------------|------------------|-------------:|------------------------------------------------------------------|
| `crawlr`              | Python service   |         5000 | Flask + Playwright/Chromium. Weekly auto-crawl scheduler.        |
| `vg-config-converter` | Python service   |         5001 | Flask, served via gunicorn.                                      |
| `notebooklm-proxy`    | Node.js service  |         3001 | Express + Puppeteer. Requires a one-time Google login (see below). |
| `bandwidth`           | Static HTML      |       80/443 | Network & Bandwidth Planner.                                     |
| `sizing-tool`         | Static HTML      |       80/443 | Cisco UC Sizing Tool.                                            |
| `hld-generator`       | Static HTML      |       80/443 | Webex Calling HLD Generator.                                     |
| `license-calc`        | Static HTML      |       80/443 | Cisco UC License Calculator.                                     |
| `multi-caller`        | Tkinter desktop  |            — | **No web deployment** — needs an X11 desktop. Launcher: `multi-caller`. |

## Quick start

```bash
# Unzip the package somewhere on the target host, then:
cd deploy
sudo bash install.sh
```

You'll be asked which components to install, then for an FQDN per web component
and a single email address for Let's Encrypt notifications.

### Non-interactive

```bash
sudo bash install.sh \
    --components crawlr,vg-config-converter,bandwidth,sizing-tool \
    --email ops@example.com \
    --fqdn-crawlr=crawlr.example.com \
    --fqdn-vg-config-converter=vg.example.com \
    --fqdn-bandwidth=bw.example.com \
    --fqdn-sizing-tool=sizing.example.com \
    --yes
```

### Without SSL (HTTP only)

Useful for internal LAN / staging:

```bash
sudo bash install.sh --components vg-config-converter --no-ssl --yes \
    --fqdn-vg-config-converter=vg.local
```

### List components and exit

```bash
bash install.sh --list
```

## DNS prerequisite

Before running the script with SSL enabled, **point each FQDN's A/AAAA record at
this server's public IP**. Certbot's HTTP-01 challenge needs reachable DNS.

If DNS isn't ready yet, run with `--no-ssl` first; later, when DNS is live,
re-run the same command without `--no-ssl` to issue the certs.

## What the script does

1. Detects Ubuntu/Debian and refuses anything else (with a warning, best-effort).
2. Installs base system packages: `nginx`, `curl`, `jq`, etc.
3. Installs `certbot` via snap (preferred) or apt fallback.
4. Per component:
   * **Python services** — creates `/opt/<slug>` + dedicated system user,
     a per-service venv, installs requirements from `requirements/<slug>.txt`,
     writes a systemd unit and a `/usr/local/bin/<slug>` helper.
   * **Node service** — installs Node.js 20 (NodeSource) if missing, sets
     `PUPPETEER_EXECUTABLE_PATH` to the system Chromium, writes systemd unit.
   * **Static HTML** — copies into `/var/www/<slug>/` (owner: `www-data`).
5. Generates an nginx site for each web component (server\_name = its FQDN).
6. Issues a Let's Encrypt cert via `certbot --nginx` (HTTP→HTTPS redirect on),
   unless `--no-ssl` is passed.
7. Enables auto-renewal:
   * `snap.certbot.renew.timer`, or
   * `certbot.timer` (apt installs), or
   * a fallback cron line in `/etc/cron.d/certbot-renew`.
8. Drops a deploy hook at `/etc/letsencrypt/renewal-hooks/deploy/00-reload-nginx.sh`
   so nginx reloads after each renewal.

## Per-component notes

### crawlr

* Service: `crawlr.service`. Helper: `crawlr {start|stop|restart|status|logs|renew-ssl}`.
* Files: `/opt/crawlr/`. PDFs: `/opt/crawlr/pdfs/`. DB: `/opt/crawlr/crawler.db`.
* nginx site adds a special `location /api/stream` that disables buffering for
  the SSE log stream.
* If the Playwright Chromium download fails (rate-limit, slow network), re-run:

  ```bash
  sudo -u crawlr /opt/crawlr/venv/bin/playwright install chromium --with-deps
  ```

### vg-config-converter

* Service: `vg-config-converter.service`, served via gunicorn on `127.0.0.1:5001`.
* Helper: `vg-config-converter {start|stop|restart|status|logs|renew-ssl}`.

### notebooklm-proxy

* Service: `notebooklm-proxy.service`. Helper: `notebooklm-proxy {start|...}`.
* **One-time Google login required** before it can serve traffic:

  ```bash
  sudo nano /opt/notebooklm-proxy/.env        # set NOTEBOOK_URL
  sudo -u nblm node /opt/notebooklm-proxy/loginOnce.js
  sudo systemctl restart notebooklm-proxy
  ```

* Edit `NOTEBOOK_URL` in `/opt/notebooklm-proxy/.env` to the URL of your
  NotebookLM notebook (`https://notebooklm.google.com/notebook/...`).

### Static pages (bandwidth, sizing-tool, hld-generator, license-calc)

* Files: `/var/www/<slug>/index.html`.
* nginx serves them with long cache for static assets and gzip on.
* To **update** a static page, drop your new `index.html` into
  `components/webpages/<slug>/` and re-run the installer for that slug:

  ```bash
  sudo bash install.sh --components sizing-tool --yes \
      --fqdn-sizing-tool=sizing.example.com --email ops@example.com
  ```

### multi-caller (desktop)

* Tkinter GUI, **not a web service**.
* Installs to `/opt/multi-caller/`, creates `multi-caller` launcher in
  `/usr/local/bin/`. Run from a graphical session (`DISPLAY` set).

## Python requirements files

`requirements/` contains:

| File                              | Purpose                                                              |
|-----------------------------------|----------------------------------------------------------------------|
| `crawlr.txt`                      | Used by the per-service venv at `/opt/crawlr/venv`.                  |
| `vg-config-converter.txt`         | Used by the per-service venv at `/opt/vg-config-converter/venv`.     |
| `multi-caller.txt`                | Used by the per-service venv at `/opt/multi-caller/venv`.            |
| `all.txt`                         | Aggregate file if you'd rather use a single shared venv (advanced).  |

The installer always uses the per-component file; `all.txt` is only there for
convenience if you want to try a single-venv layout.

## Re-running / updating

The installer is idempotent. Re-run with the same flags to:

* re-deploy updated source from `components/` (e.g. a new `index.html`);
* re-issue / extend a certificate (skipped if still valid);
* re-create / refresh systemd units and nginx sites.

It will **not** overwrite an existing `.env` file for components that have one
(`/opt/crawlr/.env`, `/opt/notebooklm-proxy/.env`) — delete those first if you
need a clean rewrite.

## Troubleshooting

| Symptom                                              | Fix                                                                 |
|------------------------------------------------------|---------------------------------------------------------------------|
| `certbot` fails with "DNS problem" or 403 on /.well-known | Ensure A/AAAA records point at this host; firewall ports 80/443 open. |
| Service fails to start                               | `journalctl -u <slug> -n 100 --no-pager`                            |
| nginx reload fails                                   | `nginx -t`                                                          |
| Want to remove a component                           | `systemctl disable --now <slug>; rm -rf /opt/<slug>; rm /etc/nginx/sites-enabled/<slug>; certbot delete --cert-name <fqdn>` |
