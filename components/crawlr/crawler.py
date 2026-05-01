"""
CRAWLR — Web Crawler + Download Manager
========================================
• Discovers all pages on a site
• Authenticates with login-protected sites (form login, Basic Auth, token, cookies)
• Downloads ALL asset types: HTML→PDF, documents, videos, audio, archives, images
• Detects changed/new/deleted content on weekly re-runs
"""

import os
import re
import json
import time
import hashlib
import mimetypes
import logging
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from database import (
    get_or_create_article, update_article_status,
    mark_deleted_articles, complete_session, log_activity,
    get_site_auth,
)
from pdf_utils import save_page_as_pdf

log = logging.getLogger('crawlr.crawler')

_DOWNLOADS_BASE = os.environ.get('CRAWLR_PDF_DIR', os.path.abspath('pdfs'))

# ── File type classification ──────────────────────────────────────────────────

# Extensions we CRAWL for links (HTML pages)
CRAWL_EXTENSIONS = {'', '.html', '.htm', '.php', '.asp', '.aspx', '.jsp',
                    '.cfm', '.shtml', '.xhtml'}

# Extensions we DOWNLOAD directly (not crawl for links)
DOWNLOAD_TYPES = {
    # Documents
    '.pdf':   'document', '.doc':  'document', '.docx': 'document',
    '.xls':   'document', '.xlsx': 'document', '.ppt':  'document',
    '.pptx':  'document', '.odt':  'document', '.ods':  'document',
    '.odp':   'document', '.rtf':  'document', '.txt':  'document',
    '.csv':   'document', '.epub': 'document', '.mobi': 'document',
    # Videos
    '.mp4':   'video',    '.avi':  'video',    '.mov':  'video',
    '.mkv':   'video',    '.webm': 'video',    '.flv':  'video',
    '.wmv':   'video',    '.m4v':  'video',    '.ts':   'video',
    '.m3u8':  'video',
    # Audio
    '.mp3':   'audio',    '.wav':  'audio',    '.ogg':  'audio',
    '.flac':  'audio',    '.aac':  'audio',    '.m4a':  'audio',
    # Images
    '.jpg':   'image',    '.jpeg': 'image',    '.png':  'image',
    '.gif':   'image',    '.webp': 'image',    '.svg':  'image',
    '.tiff':  'image',    '.bmp':  'image',
    # Archives
    '.zip':   'archive',  '.tar':  'archive',  '.gz':   'archive',
    '.rar':   'archive',  '.7z':   'archive',  '.bz2':  'archive',
    # Data / code
    '.json':  'data',     '.xml':  'data',     '.yaml': 'data',
    '.sql':   'data',
}

# Extensions to skip entirely (styles, scripts, fonts)
SKIP_ALWAYS = {'.css', '.js', '.woff', '.woff2', '.ttf', '.eot', '.otf',
               '.ico', '.map', '.min.js', '.min.css'}

# URL path patterns that are never crawled
SKIP_PATTERNS = [
    '/wp-admin/', '/wp-includes/', '/wp-login',
    '/feed/', '/rss/', '/atom/',
    '/author/', '/authors/',
    '/logout', '/register', '/signup',
    '/cart', '/checkout', '/wishlist',
    '?s=', '?q=', '?p=',
    'javascript:', 'mailto:', 'tel:',
]

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
}

CONTENT_TYPE_MAP = {
    'application/pdf':         'document',
    'application/msword':      'document',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'document',
    'application/vnd.ms-excel': 'document',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'document',
    'application/vnd.ms-powerpoint': 'document',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'document',
    'application/zip':         'archive',
    'application/x-rar':       'archive',
    'application/x-7z-compressed': 'archive',
    'video/mp4':               'video',
    'video/webm':              'video',
    'video/avi':               'video',
    'audio/mpeg':              'audio',
    'audio/ogg':               'audio',
    'image/jpeg':              'image',
    'image/png':               'image',
    'image/gif':               'image',
    'image/webp':              'image',
    'text/plain':              'document',
    'text/csv':                'document',
}


# ═══════════════════════════════════════════════════════════════════════════════
# Authentication
# ═══════════════════════════════════════════════════════════════════════════════

class AuthConfig:
    def __init__(self, auth_type='none', username='', password='',
                 login_url='', login_user_field='username',
                 login_pass_field='password', extra_fields=None,
                 token='', cookies=''):
        self.auth_type         = auth_type          # none | basic | form | token | cookie
        self.username          = username
        self.password          = password
        self.login_url         = login_url
        self.login_user_field  = login_user_field or 'username'
        self.login_pass_field  = login_pass_field or 'password'
        self.extra_fields      = extra_fields or {}  # dict of extra form fields
        self.token             = token
        self.cookies           = cookies             # raw cookie string or JSON


def build_session(auth: AuthConfig) -> requests.Session:
    """Create a requests.Session pre-configured with auth."""
    session = requests.Session()
    session.max_redirects = 10
    session.headers.update(HEADERS)

    if auth.auth_type == 'basic':
        session.auth = (auth.username, auth.password)
        log.info('[Auth] HTTP Basic Auth configured for %s', auth.username)

    elif auth.auth_type == 'token':
        session.headers['Authorization'] = f'Bearer {auth.token}'
        log.info('[Auth] Bearer token configured')

    elif auth.auth_type == 'cookie':
        _inject_cookies(session, auth.cookies)
        log.info('[Auth] Cookies injected')

    elif auth.auth_type == 'form':
        # Form login happens lazily in login_to_site()
        pass

    return session


def login_to_site(session: requests.Session, auth: AuthConfig,
                  base_url: str) -> bool:
    """
    Perform a form-based POST login.
    Returns True if login appears successful.
    """
    if auth.auth_type != 'form':
        return True

    login_url = auth.login_url or _guess_login_url(base_url)
    if not login_url:
        log.warning('[Auth] Form login: no login URL specified')
        return False

    # First fetch the login page to grab CSRF tokens, hidden fields
    try:
        resp = session.get(login_url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        log.error('[Auth] Could not fetch login page %s: %s', login_url, e)
        return False

    soup = BeautifulSoup(resp.text, 'lxml')
    form = (soup.find('form', id=re.compile(r'login|signin|auth', re.I)) or
            soup.find('form', action=re.compile(r'login|signin|auth', re.I)) or
            soup.find('form'))

    payload: dict = {}

    # Collect all hidden fields (CSRF, nonce, etc.)
    if form:
        for inp in form.find_all('input', type='hidden'):
            name = inp.get('name', '')
            val  = inp.get('value', '')
            if name:
                payload[name] = val
        # Determine POST action
        action = form.get('action', '')
        if action:
            post_url = urljoin(login_url, action)
        else:
            post_url = login_url
    else:
        post_url = login_url

    payload[auth.login_user_field] = auth.username
    payload[auth.login_pass_field] = auth.password

    # Merge any extra fields (e.g. domain, remember_me)
    payload.update(auth.extra_fields)

    log.info('[Auth] POSTing login to %s as %s', post_url, auth.username)
    try:
        r = session.post(post_url, data=payload, timeout=20, allow_redirects=True)
    except Exception as e:
        log.error('[Auth] Login POST failed: %s', e)
        return False

    # Heuristic success checks
    indicators_fail = ['incorrect password', 'invalid credentials', 'login failed',
                       'wrong password', 'authentication failed', 'error logging in',
                       'invalid username', 'bad credentials']
    body_lower = r.text.lower()
    for ind in indicators_fail:
        if ind in body_lower:
            log.warning('[Auth] Login likely failed — "%s" found in response', ind)
            return False

    # Success if we were redirected away from login page or got 200 without error
    if r.url != post_url and 'login' not in r.url.lower():
        log.info('[Auth] Login redirect to %s — likely success', r.url)
        return True

    # Check cookies were set
    if session.cookies:
        log.info('[Auth] Login cookies set: %s', list(session.cookies.keys()))
        return True

    log.warning('[Auth] Login result unclear — continuing anyway')
    return True


def _inject_cookies(session: requests.Session, cookie_str: str):
    """Parse a cookie string (Name=Value; ...) or JSON object into the session."""
    if not cookie_str:
        return
    cookie_str = cookie_str.strip()
    try:
        data = json.loads(cookie_str)
        if isinstance(data, list):
            for c in data:
                session.cookies.set(c['name'], c['value'],
                                    domain=c.get('domain', ''),
                                    path=c.get('path', '/'))
        elif isinstance(data, dict):
            for k, v in data.items():
                session.cookies.set(k, v)
        return
    except (json.JSONDecodeError, KeyError):
        pass
    # Plain  Name=Value; Name2=Value2
    for part in cookie_str.split(';'):
        part = part.strip()
        if '=' in part:
            k, _, v = part.partition('=')
            session.cookies.set(k.strip(), v.strip())


def _guess_login_url(base_url: str) -> str:
    for path in ('/login', '/signin', '/user/login', '/account/login',
                 '/auth/login', '/wp-login.php', '/admin/login'):
        return urljoin(base_url, path)
    return ''


# ═══════════════════════════════════════════════════════════════════════════════
# URL helpers
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_url(url: str) -> str:
    p = urlparse(url)
    return p._replace(fragment='').geturl().rstrip('/')


def classify_url(url: str) -> tuple[str, str]:
    """
    Returns (action, file_type):
      action:    'crawl' | 'download' | 'skip' | 'video'
      file_type: 'page' | 'document' | 'video' | 'audio' | 'image' | 'archive' | 'data'
    """
    p = urlparse(url)
    path_lower = p.path.lower()
    ext = os.path.splitext(path_lower)[1]

    if ext in SKIP_ALWAYS:
        return 'skip', ''
    for pat in SKIP_PATTERNS:
        if pat in url:
            return 'skip', ''
    if p.scheme not in ('http', 'https'):
        return 'skip', ''

    if ext in DOWNLOAD_TYPES:
        ft = DOWNLOAD_TYPES[ext]
        if ft == 'video':
            return 'video', 'video'
        return 'download', ft

    if ext in CRAWL_EXTENSIONS or not ext:
        return 'crawl', 'page'

    return 'skip', ''


def is_same_domain(url: str, base_netloc: str) -> bool:
    try:
        return urlparse(url).netloc == base_netloc
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# Fetching
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_html(url: str, session: requests.Session) -> tuple[str | None, str]:
    """Returns (html_text, final_url) or (None, url) on failure."""
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=20, allow_redirects=True)
            resp.raise_for_status()
            ct = resp.headers.get('Content-Type', '')
            # If the server returned a downloadable type, signal that
            for mime, ftype in CONTENT_TYPE_MAP.items():
                if mime in ct and ftype != 'page':
                    return None, resp.url   # caller handles as asset
            if 'text/html' not in ct and 'text/plain' not in ct:
                return None, resp.url
            return resp.text, resp.url
        except Exception as e:
            if attempt == 2:
                log.debug('Fetch failed (%s): %s', url, e)
                return None, url
            time.sleep(1.5 ** attempt)
    return None, url


def download_file(url: str, dest_path: str, session: requests.Session,
                  chunk_size: int = 1024 * 256) -> tuple[bool, int]:
    """
    Stream-download a file to dest_path.
    Returns (success, bytes_written).
    """
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    try:
        with session.get(url, stream=True, timeout=60, allow_redirects=True) as r:
            r.raise_for_status()
            size = 0
            with open(dest_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        size += len(chunk)
        return True, size
    except Exception as e:
        log.debug('Download failed (%s): %s', url, e)
        return False, 0


def download_video(url: str, dest_dir: str) -> tuple[bool, str]:
    """
    Download video using yt-dlp (handles YouTube, Vimeo, embedded players, etc.)
    Falls back to direct download for plain video files.
    Returns (success, saved_path).
    """
    os.makedirs(dest_dir, exist_ok=True)
    try:
        import yt_dlp
        opts = {
            'outtmpl':          os.path.join(dest_dir, '%(title)s.%(ext)s'),
            'quiet':            True,
            'no_warnings':      True,
            'noplaylist':       True,
            'format':           'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'merge_output_format': 'mp4',
            'ignoreerrors':     True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info:
                fname = ydl.prepare_filename(info)
                if os.path.exists(fname):
                    return True, fname
    except Exception as e:
        log.debug('yt-dlp failed for %s: %s', url, e)
    return False, ''


# ═══════════════════════════════════════════════════════════════════════════════
# Content analysis
# ═══════════════════════════════════════════════════════════════════════════════

def content_hash_html(html: str) -> str:
    soup = BeautifulSoup(html, 'lxml')
    for tag in soup.find_all(['script', 'style', 'nav', 'header', 'footer',
                               'aside', 'meta', 'link', 'noscript']):
        tag.decompose()
    main = (soup.find('main') or soup.find('article') or
            soup.find(id=re.compile(r'^(content|main|article|post)', re.I)) or
            soup.find(class_=re.compile(r'(content|main|article|post|entry)', re.I)) or
            soup.body)
    text = main.get_text(separator=' ', strip=True) if main else soup.get_text()
    return hashlib.sha256(' '.join(text.split()).encode()).hexdigest()


def content_hash_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
    except Exception:
        pass
    return h.hexdigest()


def page_title(html: str) -> str:
    soup = BeautifulSoup(html, 'lxml')
    og = soup.find('meta', property='og:title')
    if og and og.get('content'):
        return og['content'].strip()[:200]
    if soup.title and soup.title.string:
        return soup.title.string.strip()[:200]
    h1 = soup.find('h1')
    if h1:
        return h1.get_text().strip()[:200]
    return 'Untitled'


def extract_all_links(html: str, page_url: str, base_netloc: str) -> dict:
    """
    Returns {url: (action, file_type)} for every link/src/href found.
    Respects domain boundary for crawlable pages; assets can be same-domain only.
    """
    soup  = BeautifulSoup(html, 'lxml')
    found = {}

    selectors = [
        ('a',      'href'),
        ('link',   'href'),
        ('script', 'src'),
        ('img',    'src'),
        ('video',  'src'),
        ('audio',  'src'),
        ('source', 'src'),
        ('iframe', 'src'),
        ('embed',  'src'),
        ('object', 'data'),
    ]

    for tag, attr in selectors:
        for el in soup.find_all(tag, **{attr: True}):
            raw = el.get(attr, '').strip()
            if not raw:
                continue
            full = normalize_url(urljoin(page_url, raw))
            if not is_same_domain(full, base_netloc):
                continue
            action, ftype = classify_url(full)
            if action != 'skip':
                found[full] = (action, ftype)

    # Also find data-src (lazy-loaded images/videos)
    for el in soup.find_all(attrs={'data-src': True}):
        raw = el['data-src'].strip()
        full = normalize_url(urljoin(page_url, raw))
        if is_same_domain(full, base_netloc):
            action, ftype = classify_url(full)
            if action != 'skip':
                found[full] = (action, ftype)

    return found


# ═══════════════════════════════════════════════════════════════════════════════
# Download path builder
# ═══════════════════════════════════════════════════════════════════════════════

def make_download_path(base_url: str, asset_url: str, file_type: str,
                       ext_override: str = '') -> str:
    dl_base  = os.environ.get('CRAWLR_PDF_DIR', _DOWNLOADS_BASE)
    domain   = urlparse(base_url).netloc.replace('www.', '').replace(':', '_')
    p        = urlparse(asset_url)
    path_part = p.path.strip('/').replace('/', '__') or 'index'
    safe      = re.sub(r'[^\w\-_.]', '_', path_part)[:80]

    subdir = {
        'page':     'pages_pdf',
        'document': 'documents',
        'video':    'videos',
        'audio':    'audio',
        'image':    'images',
        'archive':  'archives',
        'data':     'data',
    }.get(file_type, 'misc')

    out_dir = os.path.join(dl_base, domain, subdir)
    os.makedirs(out_dir, exist_ok=True)

    # Preserve original extension if present
    _, orig_ext = os.path.splitext(safe)
    if not orig_ext and ext_override:
        safe = safe + ext_override

    return os.path.join(out_dir, safe)


# ═══════════════════════════════════════════════════════════════════════════════
# Main crawl
# ═══════════════════════════════════════════════════════════════════════════════

def crawl_site(base_url: str, session_id: int, broadcast=None,
               auth_override: dict | None = None):
    """
    Full crawl + download.
    auth_override: dict with keys matching AuthConfig fields (from API request body).
    Falls back to DB-stored auth if not provided.
    """

    def emit(etype, data: dict):
        data.setdefault('session_id', session_id)
        if broadcast:
            broadcast(etype, data)
        log_activity(session_id, etype, data.get('message', ''), data.get('url'))

    base_netloc = urlparse(base_url).netloc

    # ── Build auth config ─────────────────────────────────────────────────────
    auth_data = auth_override or get_site_auth(base_url) or {}
    auth = AuthConfig(
        auth_type        = auth_data.get('auth_type', 'none'),
        username         = auth_data.get('username', ''),
        password         = auth_data.get('password', ''),
        login_url        = auth_data.get('login_url', ''),
        login_user_field = auth_data.get('login_user_field', 'username'),
        login_pass_field = auth_data.get('login_pass_field', 'password'),
        extra_fields     = _parse_extra_fields(auth_data.get('extra_fields', '')),
        token            = auth_data.get('token', ''),
        cookies          = auth_data.get('cookies', ''),
    )

    http = build_session(auth)
    emit('crawl_started', {'url': base_url,
                            'message': f'Starting crawl for {base_url}'})
    log.info('Crawl started — session=%d  url=%s  auth=%s',
             session_id, base_url, auth.auth_type)

    # ── Login ─────────────────────────────────────────────────────────────────
    if auth.auth_type == 'form':
        emit('phase_change', {'phase': 'login',
                               'message': f'🔑 Logging in as {auth.username}…'})
        ok = login_to_site(http, auth, base_url)
        if ok:
            emit('phase_change', {'phase': 'login',
                                   'message': f'✅ Login successful as {auth.username}'})
        else:
            emit('phase_change', {'phase': 'login',
                                   'message': '⚠ Login may have failed — continuing anyway'})

    # ── Phase 1: Discovery ────────────────────────────────────────────────────
    emit('phase_change', {'phase': 'discovery',
                           'message': '🔍 Phase 1 — Discovering all pages and assets…'})

    visited:    set[str]         = set()
    queue_html: list[str]        = [normalize_url(base_url)]
    discovered: dict[str, tuple] = {}   # url → (action, file_type)

    while queue_html:
        url = queue_html.pop(0)
        if url in visited:
            continue
        visited.add(url)

        html, final_url = fetch_html(url, http)
        if not html:
            continue

        discovered[url] = ('crawl', 'page')
        emit('url_found', {
            'url': url, 'total': len(discovered),
            'message': f'Found page: {url}',
        })

        links = extract_all_links(html, final_url, base_netloc)
        for link_url, (action, ftype) in links.items():
            if link_url not in visited and link_url not in discovered:
                if action == 'crawl':
                    queue_html.append(link_url)
                else:
                    discovered[link_url] = (action, ftype)
                    emit('url_found', {
                        'url': link_url, 'total': len(discovered),
                        'message': f'Found {ftype}: {link_url}',
                    })

        time.sleep(0.3)

    emit('discovery_complete', {
        'total': len(discovered),
        'message': f'✅ Discovery complete — {len(discovered)} items found',
    })

    # ── Phase 2: Download everything ─────────────────────────────────────────
    emit('phase_change', {'phase': 'processing',
                           'message': f'⬇ Phase 2 — Downloading {len(discovered)} items…'})

    processed: set[str] = set()
    counts = dict(new=0, changed=0, unchanged=0, error=0)
    total  = len(discovered)

    for i, (url, (action, ftype)) in enumerate(discovered.items()):
        progress = int((i + 1) / total * 100)
        processed.add(url)

        # ── HTML page → PDF ───────────────────────────────────────────────────
        if action == 'crawl':
            html, _ = fetch_html(url, http)
            if not html:
                _record_error(base_url, url, emit, counts, progress)
                continue

            chash = content_hash_html(html)
            title = page_title(html)
            result = get_or_create_article(base_url, url, title, chash, 'page')

            if result['is_new']:
                dest = make_download_path(base_url, url, 'page', '.pdf')
                ok   = save_page_as_pdf(url, dest)
                update_article_status(base_url, url, 'downloaded', chash, title,
                                      pdf_path=dest if ok else None,
                                      download_path=dest if ok else None,
                                      file_type='page')
                counts['new'] += 1
                emit('article_downloaded', {
                    'url': url, 'title': title, 'status': 'downloaded',
                    'file_type': 'page', 'pdf': dest if ok else None,
                    'progress': progress, 'message': f'↓ Page: {title}',
                })
            elif result['hash_changed']:
                dest = make_download_path(base_url, url, 'page', '.pdf')
                ok   = save_page_as_pdf(url, dest)
                update_article_status(base_url, url, 'changed', chash, title,
                                      pdf_path=dest if ok else None,
                                      download_path=dest if ok else None,
                                      file_type='page', increment_revision=True)
                counts['changed'] += 1
                emit('article_changed', {
                    'url': url, 'title': title, 'status': 'changed',
                    'file_type': 'page', 'progress': progress,
                    'message': f'↻ Changed page: {title}',
                })
            else:
                update_article_status(base_url, url, 'unchanged', chash, title,
                                      file_type='page')
                counts['unchanged'] += 1
                emit('article_unchanged', {
                    'url': url, 'title': title, 'status': 'unchanged',
                    'file_type': 'page', 'progress': progress,
                    'message': f'✓ Unchanged: {title}',
                })

        # ── Direct file download ───────────────────────────────────────────────
        elif action == 'download':
            _, ext = os.path.splitext(urlparse(url).path.lower())
            dest  = make_download_path(base_url, url, ftype, ext)
            title = os.path.basename(urlparse(url).path) or url

            # Hash = hash of existing file (if any) for change detection
            old_hash  = ''
            if os.path.exists(dest):
                old_hash = content_hash_file(dest)

            result = get_or_create_article(base_url, url, title, old_hash, ftype)

            ok, size = download_file(url, dest, http)
            new_hash  = content_hash_file(dest) if ok else ''

            if result['is_new']:
                update_article_status(base_url, url, 'downloaded', new_hash, title,
                                      download_path=dest if ok else None,
                                      file_type=ftype, file_size=size)
                counts['new'] += 1
                emit('article_downloaded', {
                    'url': url, 'title': title, 'status': 'downloaded',
                    'file_type': ftype, 'file_size': _fmt_size(size),
                    'progress': progress,
                    'message': f'↓ {ftype.title()}: {title} ({_fmt_size(size)})',
                })
            elif new_hash and new_hash != old_hash:
                update_article_status(base_url, url, 'changed', new_hash, title,
                                      download_path=dest if ok else None,
                                      file_type=ftype, file_size=size,
                                      increment_revision=True)
                counts['changed'] += 1
                emit('article_changed', {
                    'url': url, 'title': title, 'status': 'changed',
                    'file_type': ftype, 'file_size': _fmt_size(size),
                    'progress': progress,
                    'message': f'↻ Updated {ftype}: {title}',
                })
            else:
                update_article_status(base_url, url, 'unchanged', new_hash, title,
                                      file_type=ftype)
                counts['unchanged'] += 1
                emit('article_unchanged', {
                    'url': url, 'title': title, 'status': 'unchanged',
                    'file_type': ftype, 'progress': progress,
                    'message': f'✓ Unchanged {ftype}: {title}',
                })

        # ── Video download via yt-dlp ─────────────────────────────────────────
        elif action == 'video':
            title    = os.path.basename(urlparse(url).path) or url
            dest_dir = make_download_path(base_url, url, 'video')
            dest_dir = os.path.dirname(dest_dir)   # use directory, yt-dlp picks filename
            result   = get_or_create_article(base_url, url, title, '', 'video')

            if result['is_new'] or result['hash_changed']:
                ok, saved_path = download_video(url, dest_dir)
                size = os.path.getsize(saved_path) if ok and saved_path else 0
                chash = content_hash_file(saved_path) if ok else ''
                status = 'downloaded' if result['is_new'] else 'changed'
                update_article_status(base_url, url, status, chash, title,
                                      download_path=saved_path if ok else None,
                                      file_type='video', file_size=size,
                                      increment_revision=not result['is_new'])
                if ok:
                    counts['new' if result['is_new'] else 'changed'] += 1
                    emit('article_downloaded' if result['is_new'] else 'article_changed', {
                        'url': url, 'title': title, 'status': status,
                        'file_type': 'video', 'file_size': _fmt_size(size),
                        'progress': progress,
                        'message': f'↓ Video: {title} ({_fmt_size(size)})',
                    })
                else:
                    _record_error(base_url, url, emit, counts, progress,
                                  note='Video download failed')
            else:
                counts['unchanged'] += 1
                emit('article_unchanged', {
                    'url': url, 'title': title, 'status': 'unchanged',
                    'file_type': 'video', 'progress': progress,
                    'message': f'✓ Video unchanged: {title}',
                })

        time.sleep(0.15)

    # ── Phase 3: Cleanup ──────────────────────────────────────────────────────
    deleted = mark_deleted_articles(base_url, processed)
    if deleted:
        emit('articles_deleted', {
            'count': deleted,
            'message': f'🗑 {deleted} item(s) removed from sitemap',
        })

    complete_session(session_id, total)
    log.info('Crawl done session=%d total=%d new=%d changed=%d del=%d err=%d',
             session_id, total, counts['new'], counts['changed'],
             deleted, counts['error'])
    emit('crawl_complete', {
        'total': total, 'new': counts['new'], 'changed': counts['changed'],
        'unchanged': counts['unchanged'], 'deleted': deleted,
        'errors': counts['error'],
        'message': (
            f'🏁 Done! {total} items · {counts["new"]} new · '
            f'{counts["changed"]} changed · {deleted} deleted · '
            f'{counts["error"]} errors'
        ),
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _record_error(base_url, url, emit, counts, progress, note='Fetch failed'):
    update_article_status(base_url, url, 'error', '', note)
    counts['error'] += 1
    emit('article_error', {
        'url': url, 'message': f'⚠ Error: {url}',
        'progress': progress,
    })


def _fmt_size(n: int) -> str:
    if n < 1024:       return f'{n} B'
    if n < 1048576:    return f'{n/1024:.1f} KB'
    if n < 1073741824: return f'{n/1048576:.1f} MB'
    return f'{n/1073741824:.2f} GB'


def _parse_extra_fields(val) -> dict:
    if not val:
        return {}
    if isinstance(val, dict):
        return val
    try:
        return json.loads(val)
    except Exception:
        result = {}
        for part in str(val).split('&'):
            if '=' in part:
                k, _, v = part.partition('=')
                result[k.strip()] = v.strip()
        return result
