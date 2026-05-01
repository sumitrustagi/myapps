"""Weekly scheduler — re-crawls all enabled sites every Sunday at 02:00."""
import threading
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

_scheduler: BackgroundScheduler | None = None
_broadcast = None

log = logging.getLogger('scheduler')


def start_scheduler(broadcast_fn):
    global _scheduler, _broadcast
    _broadcast = broadcast_fn

    _scheduler = BackgroundScheduler(timezone='UTC')
    _scheduler.add_job(
        func=_run_all_scheduled,
        trigger=CronTrigger(day_of_week='sun', hour=2, minute=0, timezone='UTC'),
        id='weekly_crawl',
        name='Weekly site crawl',
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    logging.getLogger('apscheduler').setLevel(logging.WARNING)
    print("[Scheduler] Started — weekly crawls every Sunday 02:00 UTC")


def _run_all_scheduled():
    from database import get_scheduled_sites, create_session, update_scheduled_site_run
    from crawler import crawl_site

    sites = [s for s in get_scheduled_sites() if s.get('enabled', 1)]
    log.info("Weekly crawl triggered for %d site(s)", len(sites))

    for site in sites:
        url = site['url']
        sid = create_session(url, f"Scheduled — {url}")
        update_scheduled_site_run(url)
        t = threading.Thread(target=crawl_site, args=(url, sid, _broadcast), daemon=True)
        t.start()


def trigger_now(url: str, broadcast_fn=None):
    from database import create_session
    from crawler import crawl_site

    fn = broadcast_fn or _broadcast
    sid = create_session(url, f"Manual — {url}")
    t = threading.Thread(target=crawl_site, args=(url, sid, fn), daemon=True)
    t.start()
    return sid


def get_status() -> dict:
    if not _scheduler:
        return {'running': False, 'jobs': []}
    jobs = []
    for j in _scheduler.get_jobs():
        jobs.append({
            'id': j.id,
            'name': j.name,
            'next_run': j.next_run_time.isoformat() if j.next_run_time else None,
        })
    return {'running': _scheduler.running, 'jobs': jobs}
