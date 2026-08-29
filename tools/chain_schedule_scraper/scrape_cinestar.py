"""
Scrapes CineStar (cinestar.de) showtimes into the shared schedule schema.

CineStar's own API turned out to be the easy one: `/api/cinema/` lists
every cinema WITH real lat/lng already attached (no hardcoded city table
needed, unlike CinemaxX), and `/api/cinema/{id}/show/` returns that
cinema's entire published schedule in a single request — no per-date
looping, no embargo wall, confirmed to hold up fine under a burst of
concurrent requests during live testing. So this script is much simpler
than the CinemaxX one: two clean requests per cinema, no retry dance.
"""
from __future__ import annotations

from typing import Any

import requests

BASE_URL = "https://www.cinestar.de/api"
APP_VERSION = "1.5.3"


def _get(path: str) -> tuple[Any, int]:
    response = requests.get(f"{BASE_URL}{path}", params={"appVersion": APP_VERSION}, timeout=20)
    try:
        body = response.json()
    except ValueError:
        body = None
    return body, response.status_code


def fetch_cinemas() -> list[dict]:
    body, status = _get("/cinema/")
    if status != 200 or body is None:
        print(f"[cinestar] cinema list returned HTTP {status} — skipping CineStar entirely this run.")
        return []
    cinemas = []
    for cinema in body:
        cinemas.append(
            {
                "id": f"cinestar:{cinema['id']}",
                "chain": "cinestar",
                "name": cinema.get("name", cinema.get("shortName", "CineStar")),
                "city": cinema.get("city", ""),
                "lat": cinema["lat"],
                "lng": cinema["lng"],
                "_raw_cinema_id": cinema["id"],
            }
        )
    return cinemas


def fetch_shows(cinema_id: int) -> list[dict]:
    body, status = _get(f"/cinema/{cinema_id}/show/")
    if status != 200 or body is None:
        print(f"[cinestar] show lookup failed for cinema {cinema_id}: HTTP {status}")
        return []
    return body


def fetch_cinestar() -> tuple[list[dict], list[dict]]:
    cinemas = fetch_cinemas()
    if not cinemas:
        return [], []

    screenings: list[dict] = []
    failed_count = 0
    for cinema in cinemas:
        raw_id = cinema["_raw_cinema_id"]
        movies = fetch_shows(raw_id)
        if not movies:
            failed_count += 1
            continue
        for movie in movies:
            title = movie.get("title")
            if not title:
                continue
            for showtime in movie.get("showtimes", []):
                showtime_id = showtime.get("id")
                datetime_str = showtime.get("datetime")  # e.g. "2026-08-30 20:00 CEST"
                if showtime_id is None or not datetime_str:
                    continue
                screenings.append(
                    {
                        "id": f"cinestar:{showtime_id}",
                        "cinemaId": cinema["id"],
                        "filmTitle": title,
                        # Kept as CineStar's own "YYYY-MM-DD HH:MM TZID" format
                        # rather than reformatted here — the app side parses
                        # this shape explicitly since it isn't standard ISO-8601.
                        "startDate": datetime_str,
                        "language": None,
                    }
                )

    public_cinemas = [{k: v for k, v in c.items() if not k.startswith("_")} for c in cinemas]
    print(
        f"[cinestar] done: {len(public_cinemas)} cinema(s), {len(screenings)} screening(s) "
        f"({failed_count}/{len(cinemas)} cinema(s) failed)."
    )
    return public_cinemas, screenings
