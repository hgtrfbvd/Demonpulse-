import threading
import time

_scheduler_running = False

def start_scheduler(data_engine):
    global _scheduler_running

    if _scheduler_running:
        print("[SCHEDULER] Already running, skipping duplicate start")
        return

    _scheduler_running = True

    def loop():
        print("[SCHEDULER] Started")

        while True:
            try:
                data_engine.refresh_data()
            except Exception as e:
                print("[SCHEDULER ERROR]", e)

            time.sleep(120)  # 2 min cycle

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
