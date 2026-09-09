"""
Read-only probe: what does CineStar's API actually tell us about a showing?

WHY
`scrape_cinestar.py` writes `"language": None` for every screening — hardcoded,
without ever looking at the showtime object it just received. So the bundled
schedule.json carries no format information whatsoever: no IMAX, no 3D, no OV,
no auditorium. Every chain screening in the app is therefore format-blind, and
the IMAX tag can never fire for CineStar or KINOPOLIS no matter how good the
detection is.

Before changing the scraper we need to know what fields exist. Guessing field
names and shipping a scraper that silently writes nothing would be the same
mistake twice. This script fetches one real cinema's schedule and prints the
structure — it writes nothing and changes nothing.

USAGE
    cd ~/Downloads/ChainScheduleSync
    python3 tools/chain_schedule_scraper/probe_cinestar_showtime.py

    --city Dortmund   which cinema to probe (default Dortmund, which has IMAX)

Paste the output back and I'll wire the real field names into the scraper.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

import requests

BASE_URL = "https://www.cinestar.de/api"
APP_VERSION = "9.9.9"


def get(path: str) -> tuple[Any, int]:
    response = requests.get(f"{BASE_URL}{path}", params={"appVersion": APP_VERSION}, timeout=20)
    try:
        return response.json(), response.status_code
    except ValueError:
        return None, response.status_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", default="Dortmund")
    args = parser.parse_args()

    cinemas, status = get("/cinema/")
    if status != 200 or not cinemas:
        print(f"cinema list failed: HTTP {status}")
        return 1

    match = None
    for cinema in cinemas:
        blob = json.dumps(cinema, ensure_ascii=False).lower()
        if args.city.lower() in blob:
            match = cinema
            break
    if match is None:
        print(f"no cinema matching {args.city!r}. Names available:")
        for cinema in cinemas[:40]:
            print("  ", cinema.get("name"), "/", cinema.get("city"))
        return 1

    print("=" * 70)
    print("CINEMA OBJECT — every key CineStar returns for a cinema")
    print("=" * 70)
    print(json.dumps(match, ensure_ascii=False, indent=1)[:2000])

    cinema_id = match.get("id")
    movies, status = get(f"/cinema/{cinema_id}/show/")
    if status != 200 or not movies:
        print(f"\nshow lookup failed: HTTP {status}")
        return 1

    print("\n" + "=" * 70)
    print(f"MOVIE OBJECT KEYS ({len(movies)} movies returned)")
    print("=" * 70)
    print(sorted(movies[0].keys()))

    # Every distinct key seen on any showtime, across every movie — a format
    # marker may only be present on the showings that have it.
    showtime_keys: set[str] = set()
    samples: list[dict] = []
    for movie in movies:
        for showtime in movie.get("showtimes", []) or []:
            showtime_keys.update(showtime.keys())
            if len(samples) < 3:
                samples.append({"_movie": movie.get("title"), **showtime})

    print("\n" + "=" * 70)
    print("EVERY KEY SEEN ON ANY SHOWTIME")
    print("=" * 70)
    print(sorted(showtime_keys))

    print("\n" + "=" * 70)
    print("THREE SAMPLE SHOWTIMES, IN FULL")
    print("=" * 70)
    for sample in samples:
        print(json.dumps(sample, ensure_ascii=False, indent=1))

    # The actual question: does the word IMAX appear anywhere, and if so, in
    # which field? This is what decides whether the scraper can be fixed at
    # all, or whether CineStar simply doesn't publish it here.
    print("\n" + "=" * 70)
    print("WHERE DOES 'IMAX' APPEAR?")
    print("=" * 70)

    def walk(node: Any, path: str = "") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, str) and "imax" in node.lower():
            print(f"  {path} = {node!r}")

    before = len(str(movies))
    walk(movies)
    print(f"  (searched {before} characters of response)")

    # Same for the other format words, so we learn the shape of the field
    # even if this particular cinema has no IMAX showing scheduled.
    print("\nOther format markers found (3D / OV / OmU / Dolby / 4DX / ScreenX):")
    for needle in ["3d", "ov", "omu", "dolby", "4dx", "screenx", "atmos"]:
        found: list[str] = []

        def collect(node: Any, path: str = "", needle: str = needle) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    collect(value, f"{path}.{key}" if path else key, needle)
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    collect(value, f"{path}[{index}]", needle)
            elif isinstance(node, str) and needle in node.lower() and len(found) < 3:
                found.append(f"{path} = {node!r}")

        collect(movies)
        if found:
            print(f"  {needle.upper()}:")
            for line in found:
                print(f"    {line}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
