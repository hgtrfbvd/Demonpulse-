# gunicorn.conf.py

# Safety net: restart the scheduler in the worker if it's not running.
# This handles cases where --preload is not used or the thread dies.

import threading


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
    # Signal to the app that it is running under gunicorn so app.py's startup()
    # skips starting the scheduler — post_fork handles that in each worker.
    import os
    os.environ["GUNICORN_MANAGED"] = "1"
    server.log.info("DemonPulse: gunicorn starting")
