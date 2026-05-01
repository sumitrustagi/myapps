"""
PDF generation utilities.

Backend is selected via the PDF_BACKEND environment variable (set by installer):
  PDF_BACKEND=playwright   — headless Chromium (default, best quality, handles JS)
  PDF_BACKEND=wkhtmltopdf  — lighter, no JS support

PLAYWRIGHT_BROWSERS_PATH env var points Playwright to the custom browser install
location set up by the installer (default: ~/.cache/ms-playwright).
"""
import os
import logging

log = logging.getLogger('crawlr.pdf')

# Read backend preference once at module load; default to playwright
_BACKEND = os.environ.get('PDF_BACKEND', 'playwright').lower().strip()

# Ensure Playwright finds browsers installed in the custom path from .env
_PW_BROWSERS = os.environ.get('PLAYWRIGHT_BROWSERS_PATH', '')
if _PW_BROWSERS:
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = _PW_BROWSERS


def save_page_as_pdf(url: str, output_path: str) -> bool:
    """Save a webpage as PDF. Returns True on success."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    if _BACKEND == 'wkhtmltopdf':
        # wkhtmltopdf only — no playwright fallback
        try:
            return _pdfkit_pdf(url, output_path)
        except Exception as e:
            log.error('wkhtmltopdf failed (%s): %s', url, e)
            return False

    # Playwright (default) with wkhtmltopdf fallback
    try:
        return _playwright_pdf(url, output_path)
    except Exception as e:
        log.warning('Playwright failed (%s): %s — trying wkhtmltopdf fallback', url, e)

    try:
        return _pdfkit_pdf(url, output_path)
    except Exception as e:
        log.error('wkhtmltopdf fallback also failed (%s): %s', url, e)

    return False


def _playwright_pdf(url: str, output_path: str) -> bool:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-software-rasterizer',
            ]
        )
        ctx = browser.new_context(
            viewport={'width': 1280, 'height': 900},
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            )
        )
        page = ctx.new_page()
        page.goto(url, wait_until='domcontentloaded', timeout=30_000)
        page.wait_for_timeout(1500)  # let JS settle

        # Hide print-unfriendly chrome
        page.evaluate("""() => {
            const sel = [
                'nav', 'header', 'footer',
                '.cookie-banner', '.cookie-notice', '.cookie-bar',
                '.popup', '.modal', '.overlay',
                '.ad', '.advertisement', '.ads',
                '[class*="cookie"]', '[id*="cookie"]',
                '[class*="banner"]', '[id*="banner"]',
                '[class*="newsletter"]', '[id*="newsletter"]',
                '[class*="subscribe"]',
            ];
            sel.forEach(s => {
                document.querySelectorAll(s).forEach(el => {
                    el.style.display = 'none';
                });
            });
        }""")

        page.pdf(
            path=output_path,
            format='A4',
            print_background=True,
            margin={'top': '15mm', 'right': '12mm', 'bottom': '15mm', 'left': '12mm'}
        )
        browser.close()

    ok = os.path.exists(output_path) and os.path.getsize(output_path) > 200
    if ok:
        log.debug('PDF saved (playwright): %s', output_path)
    return ok


def _pdfkit_pdf(url: str, output_path: str) -> bool:
    import pdfkit

    # Use xvfb wrapper if present (installed by installer for headless servers)
    wkhtmltopdf_bin = '/usr/local/bin/wkhtmltopdf-xvfb'
    if not os.path.isfile(wkhtmltopdf_bin):
        wkhtmltopdf_bin = None  # let pdfkit find it on PATH

    config = pdfkit.configuration(wkhtmltopdf=wkhtmltopdf_bin) if wkhtmltopdf_bin else None

    opts = {
        'page-size':              'A4',
        'margin-top':             '15mm',
        'margin-right':           '12mm',
        'margin-bottom':          '15mm',
        'margin-left':            '12mm',
        'encoding':               'UTF-8',
        'no-outline':             None,
        'quiet':                  '',
        'load-error-handling':    'ignore',
        'load-media-error-handling': 'ignore',
        'disable-javascript':     None,
    }

    kwargs: dict = {'options': opts}
    if config:
        kwargs['configuration'] = config

    pdfkit.from_url(url, output_path, **kwargs)

    ok = os.path.exists(output_path) and os.path.getsize(output_path) > 200
    if ok:
        log.debug('PDF saved (wkhtmltopdf): %s', output_path)
    return ok
