"""
Scrapes Cineplex showtimes into the shared schedule schema.

Cineplex's own website (cineplex.de) is a Next.js App Router SSR app whose
programme pages carry their data inside a ~500KB RSC flight payload with no
usable showtime keys — an earlier investigation burned hours there and got
nowhere (see CINEPLEX_INVESTIGATION_ROUND2.md at the repo root for the full
account). The data source used here is a completely different one: the
*booking* funnel. Clicking through to "Tickets" on any film leaves
cineplex.de entirely for `tickets.cineplex.de`, a plain SPA that talks to a
plain JSON REST API, and that API is what this module calls. Nothing here
touches the Next.js site or parses any HTML.

Two endpoints, both confirmed live against real responses:

- `GET /api/ticketing/cinemaCenters` — every Cineplex location, as
  `{"id": <int>, "name": "<str>"}`. No address and no coordinates, hence
  the hardcoded `CINEMA_LOCATIONS` table below (same trade-off, for the
  same reason, as `CITY_COORDINATES` in `scrape_cinemaxx.py`).
- `GET /api/ticketing/cinemaCenter/{id}/films` — that location's ENTIRE
  published programme in one request: every film, each with its full
  `performances` array. Like CineStar and unlike CinemaxX, there is no
  per-date looping and no embargo wall — one request per cinema is the
  whole job. Live testing saw ~50-85 films and ~140-215 performances per
  cinema, with performance dates running months out.

Both endpoints answered HTTP 200 with no auth header, no cookie and from a
foreign origin during testing (the SPA's own `POST /api/oauth/token` call
returns 401 and it keeps working anyway), so a bare `requests.get` from CI
is the same request the site itself makes.
"""
from __future__ import annotations

import re
from typing import Any

import requests

BASE_URL = "https://tickets.cineplex.de/api/ticketing"
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

# `/cinemaCenters` gives id and name only — no city, no coordinates — so
# this table supplies both, exactly like `CITY_COORDINATES` does for
# CinemaxX. It was built once, by hand, from two sources: cineplex.de's
# own `sitemap.xml` (which lists one city sitemap per location) mapped to
# center ids by reading the `tickets.cineplex.de/checkout/<centerId>/...`
# links on each city's `/programm` page, and OpenStreetMap Nominatim for
# the coordinates. The coordinates are the TOWN CENTRE, not the cinema's
# own street address — good enough for the app's "within N km of me"
# filter, not good enough to navigate by.
#
# Consequence of hardcoding: a Cineplex location added after this table
# was written is skipped (with a log line) rather than appearing without
# coordinates. Two ids the API still returns are deliberately absent
# because they are dead — 321 "Cinema Berlin [geschlossen]" and 388
# "OAK CP Freyung - [geschlossen]"; both currently return zero films.
CINEMA_LOCATIONS: dict[int, tuple[str, float, float]] = {
    314: ("Kassel", 51.3158, 9.4978),
    315: ("Baunatal", 51.2551, 9.4119),
    316: ("Vilsbiburg", 48.4485, 12.3558),
    317: ("Erding", 48.3064, 11.9077),
    318: ("Neufahrn bei Freising", 48.3333, 11.6514),
    319: ("Berlin", 52.4573, 13.3223),
    320: ("Berlin", 52.5348, 13.1956),
    322: ("Berlin", 52.4573, 13.3223),
    323: ("Berlin", 52.5174, 13.3951),
    324: ("Berlin", 52.4811, 13.4354),
    325: ("Falkensee", 52.5606, 13.0882),
    326: ("Dresden", 51.0493, 13.7381),
    327: ("Neckarsulm", 49.1917, 9.2249),
    329: ("Alsdorf", 50.8772, 6.1621),
    330: ("Aachen", 50.7764, 6.0839),
    331: ("Aachen", 50.7764, 6.0839),
    332: ("Aachen", 50.7764, 6.0839),
    333: ("Aachen", 50.7764, 6.0839),
    334: ("Bad Hersfeld", 50.8604, 9.6768),
    335: ("Naumburg (Saale)", 51.1526, 11.8099),
    336: ("Suhl", 50.6087, 10.6926),
    337: ("Eisenach", 50.9747, 10.3194),
    338: ("Eschwege", 51.1734, 10.0681),
    340: ("Limburg an der Lahn", 50.3880, 8.0635),
    341: ("Gotha", 50.9495, 10.7014),
    342: ("Wiesbaden", 50.0820, 8.2417),
    343: ("Wiesbaden", 50.0820, 8.2417),
    344: ("Wiesbaden", 50.0820, 8.2417),
    345: ("Siegburg", 50.7928, 7.2071),
    346: ("Troisdorf", 50.8153, 7.1593),
    347: ("Leverkusen", 51.0325, 6.9881),
    348: ("Euskirchen", 50.6613, 6.7871),
    349: ("Bergisch Gladbach", 50.9929, 7.1277),
    350: ("Olpe", 51.0295, 7.8435),
    351: ("Köln", 50.9384, 6.9600),
    352: ("Paderborn", 51.7177, 8.7527),
    353: ("Paderborn", 51.7177, 8.7527),
    354: ("Königsbrunn", 48.2680, 10.8884),
    355: ("Aichach", 48.4591, 11.1310),
    356: ("Leipzig", 51.3406, 12.3747),
    357: ("Memmingen", 47.9816, 10.1687),
    358: ("Penzing", 48.0752, 10.9275),
    359: ("Germering", 48.1374, 11.3614),
    360: ("Meitingen", 48.5455, 10.8523),
    361: ("Fürth", 49.4886, 10.9587),
    362: ("Arnsberg", 51.4002, 8.0606),
    363: ("Lippstadt", 51.6747, 8.3472),
    364: ("Lippstadt", 51.6747, 8.3472),
    365: ("Kulmbach", 50.1071, 11.4582),
    366: ("Rudolstadt", 50.7206, 11.3402),
    367: ("Saalfeld/Saale", 50.6479, 11.3610),
    368: ("Bayreuth", 49.9446, 11.5744),
    369: ("Amberg", 49.4544, 11.8474),
    370: ("Neumarkt in der Oberpfalz", 49.2801, 11.4585),
    371: ("Marburg", 50.8090, 8.7705),
    372: ("Marburg", 50.8090, 8.7705),
    373: ("Marburg", 50.8090, 8.7705),
    374: ("Münster", 51.9625, 7.6252),
    375: ("Münster", 51.9625, 7.6252),
    376: ("Münster", 51.9625, 7.6252),
    377: ("Münster", 51.9625, 7.6252),
    378: ("Friedrichshafen", 47.6500, 9.4801),
    379: ("Reutlingen", 48.4920, 9.2114),
    380: ("Singen (Hohentwiel)", 47.7618, 8.8349),
    381: ("Dettelbach", 49.8132, 10.1332),
    382: ("Warburg", 51.4887, 9.1488),
    383: ("Brilon", 51.3956, 8.5678),
    384: ("Passau", 48.5748, 13.4610),
    386: ("Freyung", 48.8307, 13.5494),
    387: ("Passau", 48.5748, 13.4610),
    389: ("Passau", 48.5748, 13.4610),
    391: ("Mannheim", 49.4893, 8.4673),
    392: ("Bruchsal", 49.1241, 8.5980),
    393: ("Neustadt an der Weinstraße", 49.3536, 8.1360),
    395: ("Pforzheim", 48.8909, 8.7026),
    396: ("Elmshorn", 53.7532, 9.6525),
    397: ("Neu-Ulm", 48.3953, 10.0005),
    398: ("Ulm", 48.3985, 9.9912),
    399: ("Ulm", 48.3985, 9.9912),
    401: ("Bad Kreuznach", 49.8153, 7.9125),
    402: ("Goslar", 51.9060, 10.4266),
    404: ("Lörrach", 47.6121, 7.6607),
    406: ("Baden-Baden", 48.7611, 8.2400),
    408: ("Bremen", 53.0758, 8.8072),
    409: ("Bayreuth", 49.9446, 11.5744),
    410: ("Ulm", 48.3985, 9.9912),
    411: ("Passau", 48.5748, 13.4610),
    412: ("Hamm", 51.6813, 7.8191),
    413: ("Pfaffenhofen an der Ilm", 48.5297, 11.5085),
    418: ("Biberach an der Riß", 48.0984, 9.7900),
    419: ("Fritzlar", 51.1537, 9.2645),
    420: ("Nürnberg", 49.4539, 11.0773),
    421: ("Waldkraiburg", 48.2062, 12.4022),
    422: ("Dresden", 51.0493, 13.7381),
}

# Cineplex has no language field at all. What it has is a release-type tag
# glued onto the end of each performance's `title` — the performance title
# is literally the film's `displayTitle` plus something like "2D", "3D",
# "2D OV", "2D OmU", "2D OmeU", "3D Cinity", "2D OV ScreenX", "2D UKR" or
# "EVENT". Only the subtitling/dubbing tokens say anything about language,
# so only those are reported; everything else (a plain "2D"/"3D"/premium-
# format screening) yields None rather than an invented "Deutsch", since
# the API never actually claims that.
LANGUAGE_TAGS = ("OmeU", "OmU", "OV")


def _get(path: str) -> tuple[Any, int]:
    response = requests.get(f"{BASE_URL}{path}", headers=HEADERS, timeout=20)
    try:
        body = response.json()
    except ValueError:
        body = None
    return body, response.status_code


def language_for(performance_title: str | None, display_title: str | None) -> str | None:
    """Pull the OV/OmU/OmeU tag (if any) out of a performance's title.

    Only the part of the performance title AFTER the film's own display
    title is considered, so a film whose actual name contains "OV" as a
    word doesn't get mislabelled. If the performance title doesn't start
    with the display title (Cineplex's two title fields sometimes differ
    in capitalisation, e.g. "Paw Patrol" vs "PAW Patrol"), the comparison
    falls back to case-insensitive; if that fails too, no language is
    reported rather than guessing from the whole string.
    """
    if not performance_title:
        return None
    suffix = None
    if display_title:
        if performance_title.startswith(display_title):
            suffix = performance_title[len(display_title):]
        elif performance_title.lower().startswith(display_title.lower()):
            suffix = performance_title[len(display_title):]
    if suffix is None:
        return None
    for tag in LANGUAGE_TAGS:
        if re.search(rf"\b{tag}\b", suffix):
            return tag
    return None


def fetch_cinemas() -> list[dict]:
    body, status = _get("/cinemaCenters")
    if status != 200 or not isinstance(body, list):
        print(f"[cineplex] cinemaCenters returned HTTP {status} — skipping Cineplex entirely this run.")
        return []

    cinemas = []
    unknown: list[str] = []
    for center in body:
        center_id = center.get("id")
        location = CINEMA_LOCATIONS.get(center_id)
        if location is None:
            unknown.append(f"{center_id}={center.get('name')!r}")
            continue
        city, lat, lng = location
        cinemas.append(
            {
                "id": f"cineplex:{center_id}",
                "chain": "cineplex",
                "name": center.get("name") or f"Cineplex {city}",
                "city": city,
                "lat": lat,
                "lng": lng,
                "_raw_cinema_id": center_id,
            }
        )
    if unknown:
        # Not a failure — closed locations live in this list forever, and a
        # newly opened one just needs a line adding to CINEMA_LOCATIONS.
        print(f"[cineplex] {len(unknown)} center(s) not in CINEMA_LOCATIONS, skipped: {', '.join(unknown)}")
    return cinemas


def fetch_films(cinema_id: int) -> list[dict]:
    body, status = _get(f"/cinemaCenter/{cinema_id}/films")
    if status != 200 or not isinstance(body, list):
        print(f"[cineplex] film lookup failed for center {cinema_id}: HTTP {status}")
        return []
    return body


def fetch_cineplex() -> tuple[list[dict], list[dict]]:
    cinemas = fetch_cinemas()
    if not cinemas:
        return [], []

    screenings: list[dict] = []
    failed_count = 0
    for cinema in cinemas:
        raw_id = cinema["_raw_cinema_id"]
        films = fetch_films(raw_id)
        if not films:
            # Could be a genuine empty programme (open-air venues out of
            # season return an empty list) or a failed request — `fetch_films`
            # already logged the difference, so don't double-report it here.
            failed_count += 1
            continue
        for film in films:
            title = film.get("displayTitle")
            if not title:
                continue
            for performance in film.get("performances") or []:
                performance_id = performance.get("id")
                start_date = performance.get("performanceDateTime")
                if not performance_id or not start_date:
                    continue
                screenings.append(
                    {
                        "id": f"cineplex:{performance_id}",
                        "cinemaId": cinema["id"],
                        "filmTitle": title,
                        # Already real ISO-8601 with an explicit offset
                        # ("2026-08-30T20:00:00+02:00"), same shape as
                        # CinemaxX's — no reformatting needed, and the
                        # app's ISO8601DateFormatter path parses it.
                        "startDate": start_date,
                        "language": language_for(performance.get("title"), title),
                    }
                )

    public_cinemas = [{k: v for k, v in c.items() if not k.startswith("_")} for c in cinemas]
    print(
        f"[cineplex] done: {len(public_cinemas)} cinema(s), {len(screenings)} screening(s) "
        f"({failed_count}/{len(cinemas)} cinema(s) returned nothing)."
    )
    return public_cinemas, screenings
