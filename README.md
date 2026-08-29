# ChainScheduleSync

A small, standalone scraper for Germany's big cinema chains (CinemaxX and
CineStar so far; Cineplex to follow once its data source is figured out),
meant to be pushed to its own GitHub repo. It has nothing to do with the
FilmScreeningTracker Xcode project directly — the app just downloads the
JSON file this produces.

## Why this exists

The FilmScreeningTracker iOS app tried calling these chains' own live APIs
directly from the device, but CinemaxX's endpoint has a hard, still
unexplained rate wall that no amount of on-device retrying got past for
anything beyond the next day or two. This scraper runs unattended, on a
schedule, from a clean environment — it can afford to be patient and just
skip whatever it can't get this run, since there's always a next run in
3 days. The app just reads whatever the most recent successful run
produced.

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
needed unless a chain changes its API shape (which, given CinemaxX's
history in this project, is a "when" not an "if" — the tester report in
the main app project documents the reverse-engineering process if that
happens again).

## What's in here

- `tools/chain_schedule_scraper/` — the actual Python scraper. `main.py`
  is the entry point; `scrape_cinemaxx.py` and `scrape_cinestar.py` are
  one module per chain. Adding a third chain later means adding
  `scrape_<chain>.py` with a `fetch_<chain>() -> (cinemas, screenings)`
  function and registering it in `main.py`'s `SCRAPERS` dict.
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
(CinemaxX: real ISO-8601 with offset; CineStar: `"yyyy-MM-dd HH:mm zzz"`,
e.g. `"2026-08-30 20:00 CEST"`) — the app's `ChainScheduleProvider` knows
how to parse both. A screening whose `cinemaId` doesn't match any entry in
`cinemas`, or whose date fails to parse, is skipped rather than crashing
anything downstream — same defensive pattern as the rest of this project.
