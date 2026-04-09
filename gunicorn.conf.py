# gunicorn.conf.py
#
# Safety net: restart the scheduler in the worker if it's not running.
# This handles cases where --preload is not used or the thread dies.


def post_fork(server, worker):
    """Called in the worker process after forking."""
    try:
        import scheduler
        if not scheduler._scheduler_thread or not scheduler._scheduler_thread.is_alive():
            scheduler.start_scheduler()
            server.log.info("DemonPulse: scheduler restarted in worker post-fork")
    except Exception as e:
        server.log.warning(f"DemonPulse: scheduler post-fork start failed: {e}")


def on_starting(server):
    server.log.info("DemonPulse: gunicorn starting")
