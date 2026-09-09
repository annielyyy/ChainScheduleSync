"""
One-off (well, occasional) builder for `data/cinema_coords.json`.

WHY THIS EXISTS
Kinoheld's cinema search returns a street and a city for each cinema, plus a
server-computed distance from the search location — but no latitude or
longitude. That is fine for sorting results by distance, which is all the app
did until now, but it means we cannot draw a cinema on a map.

A cinema does not move. So rather than geocoding at runtime (slow, rate
limited, and repeated for every user on every search), this script does it
once, offline, and produces a static id -> coordinate table that ships inside
the app bundle. At runtime the provider does a dictionary lookup: zero network,
zero latency.

This is the same shape as `data/schedule.json` for the chains, except that
this file changes far more slowly — a rebuild every few months is plenty.

HOW IT WORKS
1. Ask Kinoheld's cinema search for every German city in CITIES below, with a
   generous radius, and union the results by cinema id.
2. Geocode each cinema's address with Nominatim (OpenStreetMap), which is free
   and needs no key, at the 1 request/second their usage policy asks for.
3. Write `data/cinema_coords.json`.

It is resumable: geocoding results are cached in `data/.geocode_cache.json`
after every lookup, so a Ctrl-C or a dropped connection costs you nothing.
Run it again and it picks up where it stopped.

USAGE
    cd ~/Downloads/ChainScheduleSync
    python3 -m pip install requests
    python3 tools/chain_schedule_scraper/build_cinema_coords.py

Expect roughly 20 minutes, almost all of it the deliberate 1 req/s geocoding
pause. Leave it running and do something else.

    --cities-only   stop after step 1 and just report what was found
    --limit N       geocode only the first N cinemas (for a quick smoke test)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

KINOHELD_ENDPOINT = "https://graph.kinoheld.de/graphql/v1/query"
NOMINATIM_ENDPOINT = "https://nominatim.openstreetmap.org/search"

# Nominatim's usage policy requires a genuine identifying User-Agent with a
# way to contact whoever is running the script. This is not evasion of
# anything — it is what they explicitly ask you to send.
USER_AGENT = "CineRadar-coordinate-builder/1.0 (julianhellwig04@gmail.com)"

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
OUTPUT_PATH = DATA_DIR / "cinema_coords.json"
CACHE_PATH = DATA_DIR / ".geocode_cache.json"

CINEMA_SEARCH_QUERY = """
query CinemaSearch($searchTerm: String, $location: String, $distance: Int, $limit: Int) {
  cinemas(search: $searchTerm, location: $location, distance: $distance, limit: $limit) {
    id
    name
    street
    city { name }
  }
}
"""

# Enough cities, at a 40 km radius each, to blanket Germany's populated area.
# Overlap is intentional and harmless: results are unioned by cinema id.
CITIES = [
    "Berlin", "Hamburg", "München", "Köln", "Frankfurt am Main", "Stuttgart",
    "Düsseldorf", "Leipzig", "Dortmund", "Essen", "Bremen", "Dresden",
    "Hannover", "Nürnberg", "Duisburg", "Bochum", "Wuppertal", "Bielefeld",
    "Bonn", "Münster", "Mannheim", "Karlsruhe", "Augsburg", "Wiesbaden",
    "Mönchengladbach", "Gelsenkirchen", "Braunschweig", "Kiel", "Chemnitz",
    "Aachen", "Halle (Saale)", "Magdeburg", "Freiburg im Breisgau", "Krefeld",
    "Lübeck", "Mainz", "Erfurt", "Oberhausen", "Rostock", "Kassel",
    "Hagen", "Potsdam", "Saarbrücken", "Hamm", "Ludwigshafen am Rhein",
    "Oldenburg", "Mülheim an der Ruhr", "Osnabrück", "Leverkusen",
    "Heidelberg", "Darmstadt", "Solingen", "Regensburg", "Herne", "Paderborn",
    "Neuss", "Ingolstadt", "Offenbach am Main", "Fürth", "Würzburg", "Ulm",
    "Heilbronn", "Pforzheim", "Wolfsburg", "Göttingen", "Bottrop", "Reutlingen",
    "Koblenz", "Bremerhaven", "Recklinghausen", "Bergisch Gladbach", "Jena",
    "Remscheid", "Erlangen", "Trier", "Salzgitter", "Siegen", "Moers",
    "Cottbus", "Hildesheim", "Gütersloh", "Kaiserslautern", "Schwerin",
    "Witten", "Gera", "Iserlohn", "Ludwigsburg", "Hanau", "Esslingen am Neckar",
    "Zwickau", "Düren", "Ratingen", "Tübingen", "Flensburg", "Gießen",
    "Villingen-Schwenningen", "Konstanz", "Worms", "Marburg", "Dessau-Roßlau",
    "Lüneburg", "Velbert", "Minden", "Neubrandenburg", "Delmenhorst",
    "Bamberg", "Viersen", "Rheine", "Gladbeck", "Detmold", "Troisdorf",
    "Bayreuth", "Fulda", "Landshut", "Aschaffenburg", "Kempten (Allgäu)",
    "Lünen", "Brandenburg an der Havel", "Bocholt", "Celle", "Aalen",
    "Plauen", "Neumünster", "Dinslaken", "Herford", "Rosenheim", "Görlitz",
    "Sindelfingen", "Friedrichshafen", "Offenburg", "Stralsund", "Greifswald",
    "Schweinfurt", "Garbsen", "Frankfurt (Oder)", "Wilhelmshaven", "Hof",
    "Passau", "Freiberg", "Amberg", "Emden", "Weimar", "Nordhorn", "Sankt Augustin",
]


def post_kinoheld(session: requests.Session, city: str, distance_km: int) -> list[dict[str, Any]]:
    body = {
        "operationName": "CinemaSearch",
        "query": CINEMA_SEARCH_QUERY,
        "variables": {
            "searchTerm": "",
            "location": city,
            "distance": distance_km,
            "limit": 100,
        },
    }
    response = session.post(KINOHELD_ENDPOINT, json=body, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise RuntimeError(f"{city}: {payload['errors'][0].get('message')}")
    return payload.get("data", {}).get("cinemas") or []


def collect_cinemas(distance_km: int) -> dict[str, dict[str, Any]]:
    """Union of every cinema Kinoheld reports near any city in CITIES."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Content-Type": "application/json"})

    cinemas: dict[str, dict[str, Any]] = {}
    for index, city in enumerate(CITIES, start=1):
        try:
            found = post_kinoheld(session, city, distance_km)
        except Exception as error:  # one bad city must not lose the whole run
            print(f"  [{index:3}/{len(CITIES)}] {city}: FAILED — {error}", file=sys.stderr)
            continue

        new_here = 0
        for cinema in found:
            cinema_id = str(cinema.get("id") or "")
            if not cinema_id or cinema_id in cinemas:
                continue
            cinemas[cinema_id] = {
                "id": cinema_id,
                "name": cinema.get("name") or "",
                "street": cinema.get("street") or "",
                "city": (cinema.get("city") or {}).get("name") or "",
            }
            new_here += 1
        print(f"  [{index:3}/{len(CITIES)}] {city}: {len(found)} found, {new_here} new "
              f"(total {len(cinemas)})")
        time.sleep(0.3)  # be a polite guest on someone else's API

    return cinemas


def load_cache() -> dict[str, Any]:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("  (cache file was corrupt, starting fresh)", file=sys.stderr)
    return {}


def geocode(session: requests.Session, query: str) -> tuple[float, float] | None:
    response = session.get(
        NOMINATIM_ENDPOINT,
        params={"q": query, "format": "json", "limit": 1, "countrycodes": "de"},
        timeout=30,
    )
    response.raise_for_status()
    results = response.json()
    if not results:
        return None
    return round(float(results[0]["lat"]), 5), round(float(results[0]["lon"]), 5)


def geocode_all(cinemas: dict[str, dict[str, Any]], limit: int | None) -> dict[str, Any]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    cache = load_cache()
    pending = [c for c in cinemas.values() if c["id"] not in cache]
    if limit is not None:
        pending = pending[:limit]

    print(f"\nGeocoding {len(pending)} cinemas ({len(cache)} already cached)…")
    for index, cinema in enumerate(pending, start=1):
        # Street first — it is far more precise. If the cinema has no street
        # on file, the name plus the city usually still lands, because most
        # German cinemas are named landmarks in OSM.
        attempts = []
        if cinema["street"] and cinema["city"]:
            attempts.append(f"{cinema['street']}, {cinema['city']}, Deutschland")
        if cinema["name"] and cinema["city"]:
            attempts.append(f"{cinema['name']}, {cinema['city']}, Deutschland")

        coordinate = None
        used = None
        for attempt in attempts:
            try:
                coordinate = geocode(session, attempt)
            except Exception as error:
                print(f"  [{index:4}/{len(pending)}] {cinema['name']}: error — {error}",
                      file=sys.stderr)
                coordinate = None
            time.sleep(1.1)  # Nominatim asks for max 1 request per second
            if coordinate:
                used = attempt
                break

        cache[cinema["id"]] = {
            "lat": coordinate[0] if coordinate else None,
            "lng": coordinate[1] if coordinate else None,
            "query": used,
        }
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

        status = f"{coordinate[0]}, {coordinate[1]}" if coordinate else "NOT FOUND"
        print(f"  [{index:4}/{len(pending)}] {cinema['name']}, {cinema['city']}: {status}")

    return cache


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cities-only", action="store_true",
                        help="stop after the Kinoheld sweep and just report")
    parser.add_argument("--limit", type=int, default=None,
                        help="geocode at most N cinemas (smoke test)")
    parser.add_argument("--distance", type=int, default=40,
                        help="search radius in km around each city (default 40)")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Sweeping {len(CITIES)} cities at {args.distance} km…")
    cinemas = collect_cinemas(args.distance)
    print(f"\n{len(cinemas)} distinct cinemas found.")

    with_street = sum(1 for c in cinemas.values() if c["street"])
    print(f"{with_street} have a street address; {len(cinemas) - with_street} do not.")

    if args.cities_only:
        return 0

    cache = geocode_all(cinemas, args.limit)

    located = {
        cinema_id: {"lat": entry["lat"], "lng": entry["lng"]}
        for cinema_id, entry in cache.items()
        if entry.get("lat") is not None and cinema_id in cinemas
    }

    output = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "Kinoheld cinema search, geocoded via Nominatim (OpenStreetMap)",
        "cinemas": {
            cinema_id: {
                "name": cinemas[cinema_id]["name"],
                "city": cinemas[cinema_id]["city"],
                "lat": coordinate["lat"],
                "lng": coordinate["lng"],
            }
            for cinema_id, coordinate in sorted(located.items())
        },
    }
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=1), encoding="utf-8")

    total = len(cinemas)
    hit = len(located)
    print(f"\nWrote {OUTPUT_PATH}")
    print(f"{hit}/{total} cinemas located ({hit * 100 // max(total, 1)}%).")
    if hit < total:
        print("The misses are listed in the cache file with a null lat — most are")
        print("cinemas with no street on file. They simply get no pin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
