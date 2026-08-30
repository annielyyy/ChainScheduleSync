"""
Scrapes KINOPOLIS (kinopolis.de) showtimes into the shared schedule schema.

kinopolis.de itself is server-rendered and has no JSON of its own, but every
Kinopolis site embeds a `cineOrderDomain = 'https://iframe.ts.kinopolis.de'`
config line pointing at the CineOrder ticketing webshop the whole group runs
on — and that webshop is a plain JSON REST API. `GET /api/centers` is public
and returns all 17 locations WITH real lat/lng (like CineStar, unlike
CinemaxX — no hardcoded city table needed), and
`GET /api/films?cinemadate.from=&cinemadate.to=` returns every film playing
at one center over an arbitrary date range with its performances nested
inside — the whole two-week window in a single request per cinema, no
per-date looping.

The one wrinkle: everything except `/api/centers`, `/api/versions` and
`/api/session` answers `401 {"errorMessage":"Unauthorized"}` without two
headers, `CENTER-OID` and `SESSION-ID`. That is not an auth wall — it is
just session plumbing: `GET /api/session` with only `CENTER-OID` set hands
out an anonymous session id (valid ~2 days), no login, no cookies, no token
exchange. So it is one extra request per cinema, not an obstacle. Confirmed
live against all 17 centers back to back (34 consecutive requests, zero
failures, no rate limiting) — nothing like the CinemaxX 401 saga.

The Munich flagship (Mathäser Filmpalast) and Gloria Palast run their own
domains but the same backend — mathaeser.de's config points at
`iframe.ts.mathaeser.de` with center OID `20000000014TTMBFYG`, which is
already center id 6 in this host's `/api/centers` response. So one host
covers the entire group; there is nothing extra to scrape for them.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import requests

BASE_URL = "https://iframe.ts.kinopolis.de/api"
LOCALE = "de"
WINDOW_DAYS = 14


def _get(path: str, params: dict | None = None, headers: dict | None = None) -> tuple[Any, int]:
    response = requests.get(f"{BASE_URL}{path}", params=params, headers=headers, timeout=20)
    try:
        body = response.json()
    except ValueError:
        body = None
    return body, response.status_code


def fetch_cinemas() -> list[dict]:
    body, status = _get("/centers")
    if status != 200 or body is None:
        print(f"[kinopolis] center list returned HTTP {status} — skipping Kinopolis entirely this run.")
        return []
    cinemas = []
    for center in body:
        center_oid = center.get("winticketId")
        if not center_oid:
            # No ticketing OID means there is no way to ask for its schedule.
            print(f"[kinopolis] center '{center.get('name')}' has no winticketId — skipping.")
            continue
        cinemas.append(
            {
                "id": f"kinopolis:{center['id']}",
                "chain": "kinopolis",
                "name": center.get("name", "KINOPOLIS"),
                "city": center.get("cityName", ""),
                "lat": center["latitude"],
                "lng": center["longitude"],
                "_center_oid": center_oid,
            }
        )
    return cinemas


def fetch_session(center_oid: str) -> str | None:
    """Anonymous session id for one center. Everything except /centers needs it."""
    body, status = _get("/session", headers={"CENTER-OID": center_oid})
    if status != 200 or not isinstance(body, dict):
        print(f"[kinopolis] session lookup failed for center {center_oid}: HTTP {status}")
        return None
    session_id = body.get("sessionId")
    if not session_id:
        print(f"[kinopolis] session response for center {center_oid} carried no sessionId.")
        return None
    return session_id


def fetch_films(center_oid: str, session_id: str, date_from: str, date_to: str) -> list[dict]:
    body, status = _get(
        "/films",
        params={
            "cinemadate.from": date_from,
            "cinemadate.to": date_to,
            "locale": LOCALE,
            "include.languageinformation": "true",
        },
        headers={"CENTER-OID": center_oid, "SESSION-ID": session_id},
    )
    if status != 200 or not isinstance(body, list):
        print(f"[kinopolis] film lookup failed for center {center_oid}: HTTP {status}")
        return []
    return body


def _language_of(performance: dict) -> str | None:
    # This field is always present but, across every center checked live, always
    # blank — Kinopolis encodes OV/OmU in the title instead. Read it anyway in
    # case they start filling it in, and fall back to null rather than "".
    info = performance.get("languageInformation") or {}
    language = (info.get("language") or "").strip()
    return language or None


def fetch_kinopolis() -> tuple[list[dict], list[dict]]:
    cinemas = fetch_cinemas()
    if not cinemas:
        return [], []

    today = datetime.now(timezone.utc).date()
    date_from = today.strftime("%Y-%m-%d")
    date_to = (today + timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")

    screenings: list[dict] = []
    failed_count = 0
    for cinema in cinemas:
        center_oid = cinema["_center_oid"]
        session_id = fetch_session(center_oid)
        if session_id is None:
            failed_count += 1
            continue
        films = fetch_films(center_oid, session_id, date_from, date_to)
        if not films:
            failed_count += 1
            continue
        for film in films:
            for performance in film.get("performances") or []:
                # `filmTitle` is the clean catalogue title ("Minions + Monster");
                # `displayTitle` is the marketing/format variant ("3D: D-BOX -
                # Minions 3 (Atmos)"), which would never match a watchlist entry.
                title = performance.get("filmTitle") or film.get("title")
                performance_id = performance.get("id")
                start = performance.get("performanceDateTime")
                if not title or not performance_id or not start:
                    continue
                screenings.append(
                    {
                        "id": f"kinopolis:{performance_id}",
                        "cinemaId": cinema["id"],
                        "filmTitle": title,
                        # Real ISO-8601 with offset, e.g. "2026-08-31T20:30:00+02:00"
                        # — the same shape CinemaxX produces, so the app side
                        # already parses it without a new format.
                        "startDate": start,
                        "language": _language_of(performance),
                    }
                )

    public_cinemas = [{k: v for k, v in c.items() if not k.startswith("_")} for c in cinemas]
    print(
        f"[kinopolis] done: {len(public_cinemas)} cinema(s), {len(screenings)} screening(s) "
        f"({failed_count}/{len(cinemas)} cinema(s) failed)."
    )
    return public_cinemas, screenings
