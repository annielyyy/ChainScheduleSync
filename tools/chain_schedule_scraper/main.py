"""
Entry point for the every-3-days chain cinema schedule scraper.

Run by `.github/workflows/scrape-chain-schedule.yml` on a cron schedule
(and available via `workflow_dispatch` for a manual run). Fetches whatever
each chain's scraper module can get, merges everything into one JSON file
under `data/`, and leaves committing it to the calling workflow.

Deliberately resilient at the top level: if one chain's scraper raises
(a redesigned API, a network outage, whatever), the other chain's results
are still written rather than losing the whole run. This mirrors
`CompositeShowtimeProvider` on the app side — one source having a bad day
shouldn't erase what the others found.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scrape_cinestar import fetch_cinestar  # noqa: E402
from scrape_kinopolis import fetch_kinopolis  # noqa: E402

OUTPUT_PATH = Path(__file__).resolve().parents[2] / "data" / "schedule.json"

# CinemaxX is deliberately absent, for a reason that is NOT a bug here:
# it returns HTTP 401 from `/showingDates` to this runner, every cinema,
# on the first request, while the identical request from a residential
# connection returns full data. So the app asks CinemaxX directly from the
# device instead. `scrape_cinemaxx.py` is kept on disk — re-adding it is
# one line here if that block ever lifts. See CINEMAXX_CI_BLOCK.md.
#
# Cineplex was investigated twice and is closed: `tickets.cineplex.de`
# returns 403 to any non-browser client, in CI and from a residential
# connection alike, so neither this scraper nor an on-device provider can
# reach it. Its module has been deleted; CINEPLEX_INVESTIGATION_ROUND2.md
# keeps the endpoint research in case that ever changes.
SCRAPERS = {
    "cinestar": fetch_cinestar,
    "kinopolis": fetch_kinopolis,
}


def main() -> None:
    all_cinemas: list[dict] = []
    all_screenings: list[dict] = []

    for chain_name, scraper in SCRAPERS.items():
        try:
            cinemas, screenings = scraper()
        except Exception as error:  # noqa: BLE001 - one chain failing must not sink the run
            print(f"[main] {chain_name} scraper raised an exception, skipping it this run: {error!r}")
            continue
        all_cinemas.extend(cinemas)
        all_screenings.extend(screenings)

    output = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cinemas": all_cinemas,
        "screenings": all_screenings,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[main] wrote {len(all_cinemas)} cinema(s), {len(all_screenings)} screening(s) to {OUTPUT_PATH}")

    if not all_cinemas:
        # Every scraper failed. Don't silently commit an empty file over a
        # previous good one — exit non-zero so the workflow run is flagged
        # red and visible, rather than quietly erasing yesterday's data.
        print("[main] every chain scraper failed — treating this as a failed run.")
        sys.exit(1)


if __name__ == "__main__":
    main()
