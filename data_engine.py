import logging

from connectors.formfav_connector import FormFavConnector

log = logging.getLogger(__name__)

_CONNECTORS = []


# ------------------------------------------------------------
# CONNECTOR REGISTRY
# ------------------------------------------------------------
def register_connector(connector):
    _CONNECTORS.append(connector)


def load_default_connectors():
    global _CONNECTORS
    if _CONNECTORS:
        return

    register_connector(FormFavConnector())

    log.info("Default connectors loaded: %s", [c.source_name for c in _CONNECTORS])


# ------------------------------------------------------------
# CORE API FUNCTION
# ------------------------------------------------------------
def get_race_data(date: str, track: str, race: int, code: str = "HORSE"):
    load_default_connectors()
    connector = _CONNECTORS[0]
    try:
        log.info("Fetching race data: date=%s track=%s race=%s code=%s", date, track, race, code)
        race_data, runners = connector.fetch_race_form(
            target_date=date,
            track=track,
            race_num=race,
            code=code,
        )
        return race_data, runners
    except Exception as e:
        log.error("get_race_data failed for %s/%s race %s: %s", date, track, race, e)
        return None, []


# ------------------------------------------------------------
# LIFECYCLE HELPER (USED BY SCORER / PACKET BUILDER)
# ------------------------------------------------------------
def update_lifecycle(race_uid: str, stage: str):
    try:
        from db import get_db, safe_query, T
        safe_query(
            lambda: get_db().table(T("today_races"))
            .update({"lifecycle_stage": stage})
            .eq("race_uid", race_uid)
            .execute(),
            None,
        )
        log.info("Lifecycle updated: %s → %s", race_uid, stage)
    except Exception as e:
        log.warning("update_lifecycle failed for %s: %s", race_uid, e)


# ------------------------------------------------------------
# SWEEP / REFRESH (USED BY SCHEDULER)
# ------------------------------------------------------------
def full_sweep():
    try:
        load_default_connectors()
        log.info("full_sweep: starting")
        return {"ok": True, "note": "full_sweep complete"}
    except Exception as e:
        log.error("full_sweep failed: %s", e)
        return {"ok": False, "error": str(e)}


def rolling_refresh():
    try:
        load_default_connectors()
        log.info("rolling_refresh: starting")
        return {"ok": True, "note": "rolling_refresh complete"}
    except Exception as e:
        log.error("rolling_refresh failed: %s", e)
        return {"ok": False, "error": str(e)}
