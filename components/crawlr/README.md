# 🕷️ CRAWLR — Site Monitor

A Python web application that crawls entire websites, saves every article as a PDF, and runs weekly to detect new, changed, or deleted content.

---

## Features

| Feature | Description |
|---|---|
| **Full crawl** | Discovers every page on a site via link traversal |
| **PDF export** | Saves each article as a high-quality PDF (via headless Chromium) |
| **Change detection** | Hashes article content; re-downloads only changed pages |
| **Weekly scheduler** | Auto-runs every Sunday at 02:00 UTC |
| **Real-time UI** | Live progress via Server-Sent Events |
| **Sitemap view** | Hierarchical view of all discovered URLs |
| **Session history** | Every crawl run is stored with full article tracking |

---

## Setup

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Install Playwright browser (one-time setup)

```bash
playwright install chromium
```

> If you can't use Playwright, install `wkhtmltopdf` as a fallback:
> - **Ubuntu/Debian**: `sudo apt-get install wkhtmltopdf`
> - **macOS**: `brew install wkhtmltopdf`
> - **Windows**: Download from https://wkhtmltopdf.org/downloads.html

### 3. Run

```bash
python app.py
```

Open **http://localhost:5000** in your browser.

---

## Usage

### Starting a crawl

1. Paste any website URL into the input field (e.g. `https://docs.example.com`)
2. Click **▶ START**
3. Watch the real-time activity log on the right panel
4. View results in the **Articles** tab — filtered by status

### Status meanings

| Badge | Meaning |
|---|---|
| **New** | First time seen — PDF downloaded |
| **Changed** | Content hash changed — PDF re-downloaded, revision incremented |
| **Unchanged** | Content identical since last crawl |
| **Deleted** | URL was present before but is no longer found on the site |
| **Error** | Page could not be fetched (4xx/5xx or timeout) |

### Weekly auto-crawl

1. Go to the **Schedule** tab
2. Add a site URL and optional name
3. The scheduler will re-crawl it every Sunday at 02:00 UTC
4. Only **new** and **changed** articles will be re-downloaded
5. **Deleted** articles are automatically removed from the sitemap

### Running a scheduled site immediately

In the Schedule tab, click **▶ NOW** next to any site.

---

## File structure

```
site-crawler/
├── app.py              ← Flask server + API routes
├── crawler.py          ← Link discovery + change detection
├── pdf_utils.py        ← PDF generation (Playwright / pdfkit)
├── database.py         ← SQLite operations
├── scheduler_jobs.py   ← APScheduler weekly jobs
├── requirements.txt
├── crawler.db          ← Created on first run
├── pdfs/               ← PDF output directory
│   └── example.com/
│       └── some-article.pdf
└── templates/
    └── index.html      ← Web dashboard
```

---

## Configuration

Environment variables:

| Variable | Default | Description |
|---|---|---|
| `CRAWLER_DB` | `crawler.db` | Path to SQLite database |

To change the port: edit the last line in `app.py`:
```python
app.run(host='0.0.0.0', port=5000, ...)
```

---

## Notes

- The crawler is **polite** — it inserts delays between requests (0.35s discovery, 0.2s processing) to avoid overwhelming servers
- PDFs are saved in `pdfs/<domain>/` with filenames derived from the URL path
- The SQLite database persists all session history across restarts
- The scheduler runs in a background thread — the web UI is always available
