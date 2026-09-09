"""
Precision check for `data/cinema_coords.json`.

WHY THIS EXISTS
Nominatim does not fail loudly. Ask it for a street that it does not know
and it will happily hand back the centre of the town instead, with no error
and no obvious tell — the coordinate looks exactly as reasonable as a real
one. Banking that gives us a cinema pinned to the market square of a town it
is nowhere near, which is worse than showing no pin at all: a missing pin
reads as "we don't know", a wrong pin reads as a fact.

The builder script cannot detect this on its own, because from its point of
view the lookup succeeded. So this pass re-asks Nominatim for each address it
already resolved, this time requesting `addressdetails`, and reads the
`addresstype` field off the answer. That field says what KIND of thing was
matched. A building, a house number, a road or a named amenity means the
address really was found. A city, town, village or postcode means the street
was not found and we are looking at a settlement centroid.

Anything in the second group is dropped. Those cinemas then simply have no
coordinate, which the app already handles — it is the same state every
cinema was in before this table existed.

USAGE
    cd ~/Downloads/ChainScheduleSync
    python3 tools/chain_schedule_scraper/verify_cinema_coords.py

Roughly 7 minutes, nearly all of it the 1 request/second courtesy pause.
Resumable: results are cached after every lookup, so Ctrl-C is free.

    --report-only   print the verdict without rewriting the JSON
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests

NOMINATIM_ENDPOINT = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "CineRadar-coordinate-builder/1.0 (julianhellwig04@gmail.com)"

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
COORDS_PATH = DATA_DIR / "cinema_coords.json"
GEOCODE_CACHE_PATH = DATA_DIR / ".geocode_cache.json"
VERIFY_CACHE_PATH = DATA_DIR / ".verify_cache.json"

# An answer of one of these types means Nominatim matched a settlement, not an
# address — i.e. it never found the street and fell back to the town centre.
SETTLEMENT_TYPES = {
    "city", "town", "village", "hamlet", "municipality", "borough",
    "suburb", "quarter", "neighbourhood", "county", "state", "province",
    "region", "postcode", "country", "district", "administrative",
}


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def lookup_type(session: requests.Session, query: str) -> str | None:
    """The `addresstype` of Nominatim's best match for `query`."""
    response = session.get(
        NOMINATIM_ENDPOINT,
        params={
            "q": query,
            "format": "json",
            "limit": 1,
            "countrycodes": "de",
            "addressdetails": 1,
        },
        timeout=30,
    )
    response.raise_for_status()
    results = response.json()
    if not results:
        return None
    best = results[0]
    return best.get("addresstype") or best.get("type") or best.get("class")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-only", action="store_true",
                        help="print the verdict without rewriting the JSON")
    args = parser.parse_args()

    coords = load_json(COORDS_PATH, None)
    if coords is None:
        print(f"No {COORDS_PATH}. Run build_cinema_coords.py first.")
        return 1

    geocode_cache = load_json(GEOCODE_CACHE_PATH, {})
    verify_cache = load_json(VERIFY_CACHE_PATH, {})

    cinemas = coords["cinemas"]
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    pending = [cid for cid in cinemas if cid not in verify_cache]
    print(f"Checking {len(pending)} cinemas ({len(verify_cache)} already checked)…\n")

    for index, cinema_id in enumerate(pending, start=1):
        cinema = cinemas[cinema_id]
        query = (geocode_cache.get(cinema_id) or {}).get("query")
        if not query:
            # Shouldn't happen, but a missing query means we can't re-ask, so
            # treat it as unverifiable rather than silently trusting it.
            verify_cache[cinema_id] = {"addresstype": None, "query": None}
        else:
            try:
                address_type = lookup_type(session, query)
            except Exception as error:
                print(f"  [{index:4}/{len(pending)}] {cinema['name']}: error — {error}")
                time.sleep(1.1)
                continue
            verify_cache[cinema_id] = {"addresstype": address_type, "query": query}
            time.sleep(1.1)

        VERIFY_CACHE_PATH.write_text(
            json.dumps(verify_cache, ensure_ascii=False, indent=1), encoding="utf-8"
        )

        entry = verify_cache[cinema_id]
        verdict = "settlement — DROP" if (entry["addresstype"] or "") in SETTLEMENT_TYPES else "ok"
        print(f"  [{index:4}/{len(pending)}] {cinema['name']}, {cinema['city']}: "
              f"{entry['addresstype']} — {verdict}")

    # Verdict
    bad = {
        cinema_id
        for cinema_id in cinemas
        if (verify_cache.get(cinema_id, {}).get("addresstype") or "") in SETTLEMENT_TYPES
        or verify_cache.get(cinema_id, {}).get("addresstype") is None
    }
    good = {cinema_id for cinema_id in cinemas if cinema_id not in bad}

    print(f"\n{'=' * 60}")
    print(f"{len(good)} precise, {len(bad)} imprecise (settlement centroid).")
    if bad:
        print("\nDropping these — they would have been pinned to a town centre:")
        for cinema_id in sorted(bad, key=lambda i: cinemas[i]["name"]):
            entry = verify_cache.get(cinema_id, {})
            print(f"  {cinemas[cinema_id]['name']}, {cinemas[cinema_id]['city']}"
                  f"  ({entry.get('addresstype')})")

    if args.report_only:
        print("\n--report-only: nothing written.")
        return 0

    coords["cinemas"] = {cid: cinemas[cid] for cid in sorted(good)}
    coords["verifiedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    coords["note"] = (
        "Only address-level geocodes are kept. Cinemas whose street did not "
        "resolve were dropped rather than pinned to a town centre."
    )
    COORDS_PATH.write_text(json.dumps(coords, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nRewrote {COORDS_PATH} with {len(good)} cinemas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
