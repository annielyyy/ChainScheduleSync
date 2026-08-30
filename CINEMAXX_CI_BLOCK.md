# CinemaxX: the 401 wall is the datacenter IP, not the requests

**Status: CinemaxX currently contributes 0 screenings from GitHub Actions.
Not a code bug. Needs a decision, not a fix.**

## What happened

First real GitHub Actions run (2026-08-30) produced:

```
[cinemaxx] showingDates HTTP 401 for cinema 1861 — skipping this cinema.
... (all 30 cinemas, identically)
[cinemaxx] done: 30 cinema(s), 0 screening(s).
[cinestar] done: 43 cinema(s), 5843 screening(s) (0/43 cinema(s) failed).
```

Every one of the 30 cinemas failed at `showingDates`, on the first request,
before any `/films` call was even attempted. Note `/cinemas` on the same
host with the same headers succeeded — that's how it got 30 cinemas to
iterate over in the first place.

## The test that settles it

Same two endpoints, same params, run from a residential IP (a browser on
the project owner's own Mac) minutes after that CI run:

- `GET /showingDates?cinemaId=1061` → **HTTP 200**, 15 dates with
  `hasShowings: true`, out to Nov 2026.
- `GET /cinemas/1061/films?showingDate=2026-08-30T00:00:00&minEmbargoLevel=3&includesSession=true&includeSessionAttributes=true`
  → **HTTP 200**, full session objects (`sessionId`, `startTime`,
  `showTimeWithTimeZone`, language attributes, booking URLs).

Cinema 1061 is one of the exact cinemas that 401'd in CI. Identical
request, different network origin, opposite result.

## What this rules out

- Not rate limiting — CI failed on request #1 per cinema, and residential
  handles bursts fine.
- Not the User-Agent — `scrape_cinemaxx.py` already sends an iOS Safari UA,
  and that same UA is what fails in CI.
- Not the date range / embargo level — `showingDates` takes neither, and it
  is what's failing.
- Not the old, never-explained per-`(cinema, date)` `/films` 401 documented
  in `CinemaxXShowtimeProvider.swift` and TESTER_REPORT.md rounds 2-4.
  That's a separate, narrower phenomenon seen from residential IPs. This is
  a blanket block one layer earlier.

The remaining explanation consistent with all of the above is that
CinemaxX (or a WAF in front of it) refuses this endpoint to datacenter /
cloud IP ranges, which is exactly what a GitHub Actions runner is.

## Why this isn't "fix it with a proxy"

Routing the scraper through a residential-proxy service to look like
home traffic would technically work and is deliberately **not** proposed
here. CinemaxX appears to have made a choice about automated access from
cloud infrastructure to an API they never documented for third parties.
Engineering around that specific control is a different kind of act than
reading an endpoint they leave open, and it isn't something this project
should do quietly in a cron job.

## Actual options

1. **Drop CinemaxX from the CI scraper.** Cleanest. Costs ~30 cinemas of
   coverage. Cineplex (~11.9k screenings) and Kinopolis (~2.9k), once
   pushed, more than replace the volume — though not the specific
   locations.
2. **Move CinemaxX back on-device.** `CinemaxXShowtimeProvider.swift` still
   exists in the app, unwired since the scraper pivot. A phone on home
   wifi/cellular is a residential IP, one person's own queries, no bulk
   collection — the normal way a person reads a cinema's public schedule.
   Costs: slower refreshes, and it reinherits the old far-out-date 401
   quirk (near-term dates, which matter most, worked reliably in live
   testing).
3. **Run the scraper somewhere residential.** e.g. a scheduled job on the
   owner's own Mac committing to the same repo. Only runs when that machine
   is awake, so schedule.json would refresh less predictably.

Options 1 and 2 combine well: let CI cover CineStar + Cineplex + Kinopolis,
and let the app ask CinemaxX directly the way it originally did.

## If you change nothing

The run still succeeds and still commits — CineStar alone wrote 5,843
screenings, and `main.py` only exits non-zero when *every* scraper fails.
CinemaxX just silently adds 30 cinemas with no screenings attached. The app
skips screenings whose cinema it can't match, and cinemas with no
screenings never surface in the UI, so nothing breaks. It's wasted requests
and misleading logs, not a malfunction.
