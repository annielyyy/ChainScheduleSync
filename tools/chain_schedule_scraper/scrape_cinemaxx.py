"""
Scrapes CinemaxX (cinemaxx.de) showtimes into the shared schedule schema.

Unlike the live on-device provider (which has to answer inside one app
launch), this script runs unattended in a scheduled GitHub Action every few
days, so it can afford to be patient and simply skip whatever it can't get
rather than chase it. CinemaxX's `/films` endpoint is known (from extensive
live testing against the real API — see CinemaxXShowtimeProvider.swift's
doc comments in the iOS app) to hard-block some (cinema, date) requests
with HTTP 401 for reasons that were never fully pinned down. Running this
every 3 days from a clean CI environment, and only ever asking for whatever
`showingDates` says is actually on sale, is the mitigation: whatever is
blocked this run may simply not be blocked next run, and near-term dates
(which is most of what matters for "what's showing soon") have reliably
worked every time this was tested live.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

BASE_URL = "https://www.cinemaxx.de/api/microservice/showings"
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)
HEADERS = {"User-Agent": USER_AGENT}

# CinemaxX's own API gives no coordinates for its ~29 locations — this
# mirrors the hardcoded table in CinemaxXShowtimeProvider.swift, keyed by
# CinemaxX's own (already ASCII-stripped) `itemName` field.
CITY_COORDINATES: dict[str, tuple[float, float]] = {
    "Augsburg": (48.3705, 10.8978),
    "Berlin": (52.5200, 13.4050),
    "Bielefeld": (52.0302, 8.5325),
    "Bremen": (53.0793, 8.8017),
    "Dresden": (51.0504, 13.7373),
    "Essen": (51.4556, 7.0116),
    "Freiburg": (47.9990, 7.8421),
    "Gottingen": (51.5413, 9.9158),
    "Halle": (51.4825, 11.9699),
    "Hamburg-Dammtor": (53.5578, 9.9903),
    "Hamburg-Harburg": (53.4592, 9.9836),
    "Hamburg-Wandsbek": (53.5723, 10.0797),
    "HOLI Hamburg": (53.5511, 9.9937),
    "Hannover": (52.3759, 9.7320),
    "Heilbronn": (49.1427, 9.2109),
    "Kiel": (54.3233, 10.1228),
    "Krefeld": (51.3388, 6.5853),
    "Magdeburg": (52.1205, 11.6276),
    "Mulheim": (51.4266, 6.8827),
    "Munchen": (48.1351, 11.5820),
    "Offenbach": (50.0956, 8.7761),
    "Oldenburg": (53.1435, 8.2146),
    "Regensburg": (49.0134, 12.1016),
    "Sindelfingen": (48.7124, 9.0011),
    "Stuttgart Liederhalle": (48.7823, 9.1770),
    "Stuttgart SI-Centrum": (48.8064, 9.1959),
    "Trier": (49.7596, 6.6441),
    "Wolfsburg": (52.4227, 10.7865),
    "Wuppertal": (51.2562, 7.1508),
    "Wurzburg": (49.7913, 9.9534),
}

WINDOW_DAYS = 14
# One retry only, same reasoning as the Swift provider: repeated retries
# essentially never recovered a blocked (cinema, date) pair in live
# testing, so a bigger budget just burns CI minutes.
RETRY_DELAY_SECONDS = 5
PACING_SECONDS = 0.4


def _get(url: str, params: dict | None = None) -> tuple[Any, int]:
    response = requests.get(url, params=params, headers=HEADERS, timeout=20)
    try:
        body = response.json()
    except ValueError:
        body = None
    return body, response.status_code


def fetch_cinemas() -> list[dict]:
    body, status = _get(f"{BASE_URL}/cinemas")
    if status != 200 or body is None:
        print(f"[cinemaxx] cinema list returned HTTP {status} — skipping CinemaxX entirely this run.")
        return []
    cinemas = []
    for group in body.get("result", []):
        for cinema in group.get("cinemas", []):
            item_name = cinema.get("itemName")
            coords = CITY_COORDINATES.get(item_name)
            if coords is None:
                print(f"[cinemaxx] no known coordinate for '{item_name}' ({cinema.get('fullName')}) — skipping.")
                continue
            cinemas.append(
                {
                    "id": f"cinemaxx:{cinema['cinemaId']}",
                    "chain": "cinemaxx",
                    "name": cinema.get("fullName", item_name),
                    "city": item_name,
                    "lat": coords[0],
                    "lng": coords[1],
                    "_raw_cinema_id": cinema["cinemaId"],
                }
            )
    return cinemas


def fetch_showing_dates(cinema_id: str, today: datetime, end: datetime) -> list[str]:
    body, status = _get(f"{BASE_URL}/showingDates", params={"cinemaId": cinema_id})
    if status != 200 or body is None:
        print(f"[cinemaxx] showingDates HTTP {status} for cinema {cinema_id} — skipping this cinema.")
        return []
    dates = []
    for entry in body.get("result", []):
        if not entry.get("hasShowings"):
            continue
        try:
            date = datetime.strptime(entry["showingDate"], "%Y-%m-%dT%H:%M:%S")
        except (KeyError, ValueError):
            continue
        if today <= date <= end:
            dates.append(entry["showingDate"])
    return dates


def fetch_films(cinema_id: str, date: str) -> list[dict] | None:
    """Returns None (not []) if the request is blocked, so the caller can
    tell "genuinely no films that day" apart from "couldn't ask"."""
    params = {
        "showingDate": date,
        "minEmbargoLevel": 3,
        "includesSession": "true",
        "includeSessionAttributes": "true",
    }
    for attempt in range(2):  # first try + one retry
        body, status = _get(f"{BASE_URL}/cinemas/{cinema_id}/films", params=params)
        if status == 200 and body is not None:
            return body.get("result", [])
        if status == 401 and attempt == 0:
            print(f"[cinemaxx] HTTP 401 for cinema {cinema_id} date={date} — retrying once in {RETRY_DELAY_SECONDS}s...")
            time.sleep(RETRY_DELAY_SECONDS)
            continue
        print(f"[cinemaxx] films lookup failed for cinema {cinema_id} date={date}: HTTP {status}")
        return None
    return None


def fetch_cinemaxx() -> tuple[list[dict], list[dict]]:
    cinemas = fetch_cinemas()
    if not cinemas:
        return [], []

    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    end = today + timedelta(days=WINDOW_DAYS)

    screenings: list[dict] = []
    for cinema in cinemas:
        raw_id = cinema["_raw_cinema_id"]
        dates = fetch_showing_dates(raw_id, today, end)
        blocked = False
        for date in dates:
            if blocked:
                continue
            time.sleep(PACING_SECONDS)
            films = fetch_films(raw_id, date)
            if films is None:
                # Same containment strategy as the Swift provider: once one
                # date for this cinema is blocked, later dates almost
                # always are too — stop wasting CI time on it this run.
                print(f"[cinemaxx] cinema {raw_id} looks blocked — skipping its remaining date(s) this run.")
                blocked = True
                continue
            for film in films:
                title = film.get("filmTitle")
                if not title:
                    continue
                for group in film.get("showingGroups", []):
                    for session in group.get("sessions", []):
                        start = session.get("showTimeWithTimeZone")
                        session_id = session.get("sessionId")
                        if not start or not session_id:
                            continue
                        language = None
                        for attr in session.get("attributes") or []:
                            if attr.get("attributeType") == "Language":
                                language = attr.get("name")
                                break
                        screenings.append(
                            {
                                "id": f"cinemaxx:{session_id}",
                                "cinemaId": cinema["id"],
                                "filmTitle": title,
                                "startDate": start,
                                "language": language,
                            }
                        )

    public_cinemas = [{k: v for k, v in c.items() if not k.startswith("_")} for c in cinemas]
    print(f"[cinemaxx] done: {len(public_cinemas)} cinema(s), {len(screenings)} screening(s).")
    return public_cinemas, screenings
