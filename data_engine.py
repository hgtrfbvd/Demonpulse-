import logging
from datetime import date

from connectors.formfav_connector import FormFavConnector

log = logging.getLogger(__name__)

_CONNECTORS = []
connector = None


# ------------------------------------------------------------
# CONNECTOR SETUP
# ------------------------------------------------------------
def load_connector():
    global connector

    if connector:
        return connector

    connector = FormFavConnector()

    if not connector.is_enabled():
        log.warning("FormFav connector not enabled (missing API key)")
    else:
        log.info("FormFav connector loaded")

    return connector


# ------------------------------------------------------------
# CORE FETCH (THIS DRIVES EVERYTHING)
# ------------------------------------------------------------
def fetch_race(target_date: str, track: str, race_num: int, code: str = "HORSE"):
    conn = load_connector()

    try:
        race, runners = conn.fetch_race_form(
            target_date=target_date,
            track=track,
            race_num=race_num,
            code=code,
        )

        return {
            "ok": True,
            "race": race.__dict__,
            "runners": [r.__dict__ for r in runners],
        }

    except Exception as e:
        log.error(f"fetch_race failed: {e}")
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------
# SIMPLE BOARD BUILDER (MANUAL FOR NOW)
# ------------------------------------------------------------
def build_board():
    """
    Temporary board builder until we add meeting discovery.
    You manually define tracks/races here.
    """

    today = date.today().isoformat()

    # 👉 EDIT THIS LIST (tracks you want)
    targets = [
        {"track": "flemington", "race": 1, "code": "HORSE"},
        {"track": "flemington", "race": 2, "code": "HORSE"},
        {"track": "albion-park", "race": 1, "code": "GREYHOUND"},
    ]

    board = []

    for t in targets:
        res = fetch_race(today, t["track"], t["race"], t["code"])

        if not res["ok"]:
            continue

        race = res["race"]

        board.append({
            "race_uid": race.get("race_uid"),
            "track": race.get("track"),
            "race_num": race.get("race_num"),
            "code": race.get("code"),
            "race_name": race.get("race_name"),
            "distance": race.get("distance"),
            "condition": race.get("condition"),
            "status": "upcoming",
        })

    return board


# ------------------------------------------------------------
# API HELPER (USED BY YOUR APP)
# ------------------------------------------------------------
def get_board():
    try:
        board = build_board()
        return {"ok": True, "items": board}
    except Exception as e:
        log.error(f"get_board failed: {e}")
        return {"ok": False, "items": []}
