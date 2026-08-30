# ChainScheduleSync

A small, standalone scraper for Germany's big cinema chains (CineStar,
Cineplex and Kinopolis), meant to be pushed to its own GitHub repo. It has
nothing to do with the FilmScreeningTracker Xcode project directly — the
app just downloads the JSON file this produces.

## Why this exists

The FilmScreeningTracker iOS app tried calling every chain's own live API
directly from the device, one screen refresh at a time, and that doesn't
hold up: a single app launch can't patiently work through dozens of
cinemas across a two-week window, and a chain having a bad minute meant
an empty screen. This scraper runs unattended, on a schedule, from a
clean environment — it can afford to be patient and just skip whatever it
can't get this run, since there's always a next run in 3 days. The app
reads whatever the most recent successful run produced.

Not every chain belongs here, though. CinemaxX blocks these endpoints
from datacenter IPs entirely, so it stays on-device where a residential
connection reaches it normally (see `CINEMAXX_CI_BLOCK.md`), and the
indie/arthouse cinemas are covered live by Kinoheld's own proximity
search in the app. This repo is specifically for big chains whose APIs
answer happily from CI.

## One-time setup

1. Open GitHub Desktop, and add this `ChainScheduleSync` folder as a local
   repository (File → Add Local Repository, point it at this folder).
2. Publish it to GitHub (Publish Repository button). Either public or
   private works — public is simpler because the app can then fetch the
   data file with a plain HTTP request and no token; if you make it
   private, the app would need a way to authenticate that isn't wired up
   yet.
3. Commit and push everything in this folder (GitHub Desktop will show all
   the files as new — commit them and push).
4. Once pushed, the schedule file will live at:
   ```
   https://raw.githubusercontent.com/<your-username>/<repo-name>/main/data/schedule.json
   ```
   (It won't exist until the workflow runs once — see below.)
5. Open the GitHub repo's page → Actions tab → "Refresh chain cinema
   schedule" → Run workflow, to trigger the first run manually rather than
   waiting up to 3 days for the cron schedule. After it finishes (a
   minute or two), `data/schedule.json` should exist in the repo.
6. Paste the raw URL from step 4 into `ChainScheduleConfig.swift` in the
   Xcode project (replacing `REPLACE_WITH_YOUR_RAW_GITHUB_SCHEDULE_URL`),
   then rebuild the app.

After that, it runs itself: the Action fires every 3 days, and the app
just downloads whatever the latest commit produced. No further steps
needed unless a chain changes its API shape (a "when" not an "if" in this
project's experience — the per-chain investigation notes in this repo, and
the tester report in the main app project, document how each API was found
in the first place if it ever has to be redone).

## What's in here

- `tools/chain_schedule_scraper/` — the actual Python scraper. `main.py`
  is the entry point; `scrape_cinestar.py`, `scrape_cineplex.py` and
  `scrape_kinopolis.py` are one module per chain, and are the three that
  actually run. Adding another chain means adding `scrape_<chain>.py` with
  a `fetch_<chain>() -> (cinemas, screenings)` function and registering it
  in `main.py`'s `SCRAPERS` dict.
- `tools/chain_schedule_scraper/scrape_cinemaxx.py` — present but NOT
  registered in `SCRAPERS`, on purpose. CinemaxX blocks `/showingDates`
  from datacenter IPs, so it returns nothing at all from a GitHub Actions
  runner; the iOS app asks CinemaxX directly from the device instead. See
  `CINEMAXX_CI_BLOCK.md`. Kept on disk so re-enabling it is one line if
  that ever changes.
- `tools/chain_schedule_scraper/test_scrape_cineplex.py` and
  `test_scrape_kinopolis.py` — tests for those two modules, network-free
  (the HTTP layer is stubbed with shapes copied from real responses). Run
  them with
  `python -m unittest discover tools/chain_schedule_scraper` or `pytest`;
  they need nothing beyond the standard library.
- `CINEPLEX_INVESTIGATION_ROUND2.md` — how Cineplex's data source was
  eventually found, after the obvious approach (its Next.js website) had
  already failed once. Worth reading before touching `scrape_cineplex.py`.
- `KINOPOLIS_INVESTIGATION.md` — how Kinopolis's data source was found
  (the CineOrder ticketing webshop the whole group runs on, not
  kinopolis.de itself), including the two headers its API needs. Worth
  reading before touching `scrape_kinopolis.py`.
- `.github/workflows/scrape-chain-schedule.yml` — the schedule (every 3
  days) and the commit-if-changed logic.
- `data/schedule.json` — the output. Not committed yet until the workflow
  runs once; don't hand-edit it, it gets overwritten every run.

## Schema

```json
{
  "generatedAt": "2026-08-29T21:00:00Z",
  "cinemas": [
    {"id": "cinemaxx:1061", "chain": "cinemaxx", "name": "CinemaxX Halle", "city": "Halle", "lat": 51.4825, "lng": 11.9699}
  ],
  "screenings": [
    {"id": "cinemaxx:28984", "cinemaId": "cinemaxx:1061", "filmTitle": "Spider-Man: Brand New Day", "startDate": "2026-08-30T17:30:00+02:00", "language": "Deutsch"}
  ]
}
```

`screenings[].startDate` is in whichever shape that chain's own API uses
(CinemaxX, Cineplex and Kinopolis: real ISO-8601 with offset, e.g.
Kinopolis's `"2026-08-31T20:30:00+02:00"`; CineStar:
`"yyyy-MM-dd HH:mm zzz"`, e.g. `"2026-08-30 20:00 CEST"`) — the app's
`ChainScheduleProvider` knows how to parse both. Kinopolis needs no new
format on the app side: it emits exactly the same ISO-8601-with-offset
shape CinemaxX already does.

`screenings[].language` is only ever populated when the chain actually
says something about language. CinemaxX gives a real language field;
CineStar gives none at all (always `null`); Cineplex has no language field
either, but tags individual performances `OV` / `OmU` / `OmeU` in their
title, and those three tags are passed through as the language. A plain
2D/3D Cineplex screening reports `null` rather than an assumed "Deutsch".
Kinopolis has a real `languageInformation` field but leaves it blank on
every performance checked live, so it reports `null` too rather than
guessing from the OV/OmU hints in its display titles.

A screening whose `cinemaId` doesn't match any entry in
`cinemas`, or whose date fails to parse, is skipped rather than crashing
anything downstream — same defensive pattern as the rest of this project.
