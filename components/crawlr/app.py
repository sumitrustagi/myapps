"""
Site Crawler — Flask backend
"""
import os, json, uuid, queue, logging, threading
from pathlib import Path
from flask import Flask, render_template, request, jsonify, Response, send_from_directory
from database import (
    init_db, create_session, get_sessions, get_session,
    get_articles, get_stats, get_activity_log,
    get_scheduled_sites, add_scheduled_site,
    save_site_auth, get_site_auth, delete_site_auth,
)

PDF_DIR  = os.environ.get('CRAWLR_PDF_DIR', os.path.abspath('pdfs'))
LOG_FILE = os.environ.get('CRAWLR_LOG', '')
APP_HOST = os.environ.get('CRAWLR_HOST', '0.0.0.0')
APP_PORT = int(os.environ.get('CRAWLR_PORT', '5000'))

_handlers = [logging.StreamHandler()]
if LOG_FILE:
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    _handlers.append(logging.FileHandler(LOG_FILE, encoding='utf-8'))
logging.basicConfig(level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(name)s — %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S', handlers=_handlers)
log = logging.getLogger('crawlr.app')

app = Flask(__name__)

# ── SSE ───────────────────────────────────────────────────────────────────────
_clients: dict[str, queue.Queue] = {}
_clients_lock = threading.Lock()

def broadcast(event_type: str, data: dict):
    msg = json.dumps({'type': event_type, 'data': data})
    with _clients_lock:
        dead = []
        for cid, q in _clients.items():
            try:    q.put_nowait(msg)
            except queue.Full: dead.append(cid)
        for cid in dead: del _clients[cid]

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/crawl/start', methods=['POST'])
def api_start_crawl():
    from crawler import crawl_site
    body = request.get_json(silent=True) or {}
    url  = body.get('url', '').strip()
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    # Auth fields may come inline with the request OR be stored in DB
    auth_payload = None
    if body.get('auth_type') and body['auth_type'] != 'none':
        auth_payload = {
            'auth_type':         body.get('auth_type', 'none'),
            'username':          body.get('username', ''),
            'password':          body.get('password', ''),
            'login_url':         body.get('login_url', ''),
            'login_user_field':  body.get('login_user_field', 'username'),
            'login_pass_field':  body.get('login_pass_field', 'password'),
            'extra_fields':      body.get('extra_fields', ''),
            'token':             body.get('token', ''),
            'cookies':           body.get('cookies', ''),
        }
        # Persist auth for future scheduled runs
        save_site_auth(url, **{k: v for k, v in auth_payload.items()
                               if k != 'auth_type'}, auth_type=auth_payload['auth_type'])

    sid = create_session(url)
    log.info('Crawl started — session=%d url=%s', sid, url)
    t = threading.Thread(target=crawl_site,
                         args=(url, sid, broadcast, auth_payload), daemon=True)
    t.start()
    return jsonify({'session_id': sid})

@app.route('/api/sessions')
def api_sessions():
    return jsonify(get_sessions())

@app.route('/api/sessions/<int:sid>')
def api_session(sid):
    s = get_session(sid)
    return jsonify(s) if s else ('Not found', 404)

@app.route('/api/sessions/<int:sid>/articles')
def api_articles(sid):
    s = get_session(sid)
    if not s: return jsonify([])
    status = request.args.get('status') or None
    return jsonify(get_articles(s['base_url'], status))

@app.route('/api/sessions/<int:sid>/stats')
def api_stats(sid):
    s = get_session(sid)
    return jsonify(get_stats(s['base_url'])) if s else jsonify({})

@app.route('/api/sessions/<int:sid>/log')
def api_log(sid):
    limit = min(int(request.args.get('limit', 200)), 500)
    return jsonify(get_activity_log(sid, limit))

# Auth CRUD
@app.route('/api/auth/<path:base_url>', methods=['GET'])
def api_get_auth(base_url):
    a = get_site_auth(base_url)
    if a:
        a.pop('password', None); a.pop('token', None); a.pop('cookies', None)
    return jsonify(a or {})

@app.route('/api/auth', methods=['POST'])
def api_save_auth():
    body = request.get_json(silent=True) or {}
    url  = body.get('base_url', '').strip()
    if not url: return jsonify({'error': 'base_url required'}), 400
    save_site_auth(
        base_url         = url,
        auth_type        = body.get('auth_type', 'none'),
        username         = body.get('username', ''),
        password         = body.get('password', ''),
        login_url        = body.get('login_url', ''),
        login_user_field = body.get('login_user_field', 'username'),
        login_pass_field = body.get('login_pass_field', 'password'),
        extra_fields     = body.get('extra_fields', ''),
        token            = body.get('token', ''),
        cookies          = body.get('cookies', ''),
    )
    return jsonify({'ok': True})

@app.route('/api/auth/delete', methods=['POST'])
def api_delete_auth():
    body = request.get_json(silent=True) or {}
    url  = body.get('base_url', '').strip()
    if url: delete_site_auth(url)
    return jsonify({'ok': True})

@app.route('/api/schedule/sites')
def api_schedule_list():
    return jsonify(get_scheduled_sites())

@app.route('/api/schedule/add', methods=['POST'])
def api_schedule_add():
    body = request.get_json(silent=True) or {}
    url  = body.get('url', '').strip()
    name = body.get('name', '').strip()
    if not url: return jsonify({'error': 'URL required'}), 400
    if not url.startswith(('http://', 'https://')): url = 'https://' + url
    return jsonify({'id': add_scheduled_site(url, name or None)})

@app.route('/api/schedule/remove', methods=['POST'])
def api_schedule_remove():
    from database import get_conn
    body = request.get_json(silent=True) or {}
    conn = get_conn()
    conn.execute('UPDATE scheduled_sites SET enabled=0 WHERE url=?', (body.get('url',''),))
    conn.commit(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/schedule/run-now', methods=['POST'])
def api_run_now():
    from scheduler_jobs import trigger_now
    body = request.get_json(silent=True) or {}
    url  = body.get('url', '').strip()
    if not url: return jsonify({'error': 'URL required'}), 400
    sid = trigger_now(url, broadcast)
    return jsonify({'session_id': sid})

@app.route('/api/scheduler/status')
def api_scheduler_status():
    from scheduler_jobs import get_status
    return jsonify(get_status())

@app.route('/api/stream')
def api_stream():
    cid = str(uuid.uuid4())
    q: queue.Queue = queue.Queue(maxsize=200)
    with _clients_lock: _clients[cid] = q
    def generate():
        yield f"data: {json.dumps({'type':'connected','data':{'client_id':cid}})}\n\n"
        try:
            while True:
                try:   msg = q.get(timeout=28); yield f"data: {msg}\n\n"
                except queue.Empty: yield 'data: {"type":"ping","data":{}}\n\n'
        finally:
            with _clients_lock: _clients.pop(cid, None)
    return Response(generate(), content_type='text/event-stream',
                    headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no',
                              'Connection':'keep-alive'})

@app.route('/pdfs/<path:filename>')
def serve_pdf(filename):
    return send_from_directory(PDF_DIR, filename)

if __name__ == '__main__':
    os.makedirs(PDF_DIR, exist_ok=True)
    os.makedirs('templates', exist_ok=True)
    init_db()
    from scheduler_jobs import start_scheduler
    start_scheduler(broadcast)
    display_host = APP_HOST if APP_HOST != '0.0.0.0' else 'localhost'
    log.info('CRAWLR starting — %s:%d  |  Downloads → %s', APP_HOST, APP_PORT, PDF_DIR)
    print(f"\n{'═'*50}\n  🕷️  CRAWLR\n  http://{display_host}:{APP_PORT}\n{'═'*50}\n")
    app.run(host=APP_HOST, port=APP_PORT, debug=False, threaded=True)
