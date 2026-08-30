# Cineplex, second look — found it

**Verdict: viable, and built.** Cineplex's showtimes are behind a plain,
public, unauthenticated JSON REST API — just not on `cineplex.de`. It
lives on `tickets.cineplex.de`, the separate booking app you land in when
you click "Tickets" on a film. `scrape_cineplex.py` now uses it, and it is
registered in `main.py`'s `SCRAPERS`.

The first investigation was looking in the right building at the wrong
floor: it kept trying to get showtimes out of the Next.js programme page's
RSC flight payload, which genuinely does not contain them in any usable
form. Nobody had followed the funnel one click further.

## What the round-2 brief asked me to try, and what happened

### Angle 2 — a white-label booking platform deeper in the funnel — WORKED

This is the one that paid off, and it took about ten minutes.

On a film page (`/leipzig/film/spiderman-brand-new-day-8BF31000012FWBXJYB`)
every showtime button is an `<a href>` to
`https://tickets.cineplex.de/checkout/356/<performanceId>` — a different
host, a different app. That app is not Next.js; it is a small Vite/React
SPA (`/assets/index-*.js`) that talks to a REST API at
`/api/ticketing/...`. Loading one checkout page and reading its network
traffic exposed the whole surface immediately:

```
GET /api/ticketing/cinemaCenter/356/performance/<perfId>/init
GET /api/ticketing/cinemaCenter/356/performance/<perfId>/performances
GET /api/ticketing/cinemaCenter/356/performance/<perfId>/seatingPlan
GET /api/ticketing/cinemaCenter/356/products
GET /api/ticketing/cinemaCenter/356/textSnippets
```

Probing around those gave the two endpoints that actually matter:

- **`GET https://tickets.cineplex.de/api/ticketing/cinemaCenters`** —
  every Cineplex location as `{"id": 356, "name": "Cineplex Leipzig"}`.
  96 entries.
- **`GET https://tickets.cineplex.de/api/ticketing/cinemaCenter/{id}/films`**
  — that location's *entire* published programme in one request: every
  film with `displayTitle`, `id`, and a full `performances` array of
  `{id, title, performanceDateTime, auditorium}`.

`performanceDateTime` is real ISO-8601 with an explicit offset
(`"2026-08-30T10:45:00+02:00"`) — the same shape CinemaxX gives, so the
app's existing `ISO8601DateFormatter` path parses it with no new code.

This is the CineStar situation, not the CinemaxX situation: **one request
per cinema is the whole job.** No per-date loop, no `showingDates`
pre-check, no embargo wall, no retry dance. Leipzig alone returned 74
films / 189 performances with dates running out to May 2027.

Access checks, all confirmed live:

- No `Authorization` header. The SPA's own `POST /api/oauth/token` returns
  **401** and the data endpoints keep answering 200 regardless.
- No cookie needed, and no `Referer`/`Origin` gate: fetched cross-origin
  from `www.cineplex.de` with default (credential-less) `fetch` → 200.
- No CORS header at all on the response (`Access-Control-Allow-Origin` is
  absent), which is irrelevant to a server-side `requests.get` and was
  only ever a browser-side concern.

So a bare `requests.get` from a GitHub Action makes the same request the
site itself makes. Nothing is being circumvented and no HTML is parsed.

**Full-chain dry run** (the scraper's exact logic, replayed against the
live API for all 96 centers): **0 request failures, 90 centers with a
programme, 6 empty, 11,939 screenings.** The 6 empties are open-air venues
out of season and the two locations the API itself labels
`[geschlossen]` — they return `[]`, not an error.

### Angle 1 — a separate mobile-app backend — not needed, and no evidence of one

Checked before angle 2 paid off, so this got a short pass rather than an
exhaustive one. The site footer links a real Cineplex app on both stores
(`apps.apple.com/de/app/cineplex-kinoprogramm/id361227953`,
`play.google.com/store/apps/details?id=de.cineplex.androidapp`). There is
also an `auth.cineplex.de` OAuth server (the site's login flow uses
`client_id=94359494-…`), which is a customer-account service, not a
programme feed. I found no public docs, partner API, or blog post
describing a Cineplex programme API. Given that the ticketing API answers
everything needed *without* auth, chasing the app's backend would have
been work for no additional data, so I stopped. If someone later needs
something the ticketing API doesn't expose, this is the thread to pull.

### Angle 3 — overlap with Kinoheld — not investigated in the end

Angle 2 landed first and gave a complete, first-party source covering all
96 locations, which is strictly better than partial Kinoheld coverage
would have been. I did not run the city-by-city Kinoheld comparison. Worth
knowing: some of the 96 "Cineplex" centers are independently-branded
arthouse houses in the Cineplex co-op (Schlosstheater Münster, Adria
Filmtheater Berlin, Scharfrichter Kino Passau, …), so a handful of them
may well also appear via Kinoheld and produce duplicate screenings in the
app. That is a de-duplication question for the app side, not a reason to
change anything here.

### Angle 4 — one more pass at the RSC payload — not needed, and confirmed unnecessary

I did spend a couple of minutes on `cineplex.de` itself, mostly to look
for a cinema list with coordinates (see below), and the earlier finding
holds: no `ld+json`, no `latitude`/`longitude`, no `geoCoordinates`, no
`addressLocality`, and no time-shaped strings anywhere accessible in the
1.35MB `/leipzig/…` page. Its `/api/*` routes are 404 apart from
`/api/health` ("Health checked") and `/api/auth` (a redirect to
`auth.cineplex.de`). There is no data API on the Next.js site. **The
earlier investigation's conclusion about that site was correct** — it just
wasn't the whole map.

## The one thing that isn't clean: coordinates

`/cinemaCenters` returns `id` and `name` and nothing else. No address, no
city, no lat/lng. Nothing anywhere on `tickets.cineplex.de` has geo —
I checked `init`, `textSnippets` (which contains the imprint, addresses
buried in HTML) and the performance payloads.

So `scrape_cineplex.py` carries a hardcoded `CINEMA_LOCATIONS` table,
exactly like `CITY_COORDINATES` in `scrape_cinemaxx.py` and for exactly
the same reason. It was built once, by hand, from two real sources:

1. **center id → city.** `https://www.cineplex.de/sitemap.xml` lists one
   city sitemap per location (74 city slugs). Fetching each city's
   `/programm` page and reading the `tickets.cineplex.de/checkout/<id>/…`
   links in it gives that city's center id(s). Several cities map to more
   than one center (Münster has 4, Passau 3, Aachen 3). Two ambiguous
   towns were confirmed against the cinema's own printed address:
   `Cineplex Neustadt` is **67433 Neustadt an der Weinstraße**, and
   `Cineplex Penzing` is **86929 Penzing** (Landkreis Landsberg am Lech).
2. **city → lat/lng.** OpenStreetMap Nominatim, one query per town, each
   result checked against its expected federal state before being kept
   (this caught `Alsdorf` resolving to the Eifel village rather than the
   one next to Aachen).

Two caveats, both deliberate and both documented in the module:

- The coordinates are **town centres, not the cinemas' street addresses**.
  Fine for the app's "within N km of me" radius filter; not fine for
  navigation. Nobody should treat them as the venue's real position.
- A Cineplex location opened *after* this table was written gets **skipped
  with a log line**, not emitted without coordinates. Same failure mode
  CinemaxX already has. `[cineplex] N center(s) not in CINEMA_LOCATIONS`
  in the Action's log is the signal that the table needs a line adding.

The alternative — geocoding at scrape time — was rejected: it would put a
third-party service (with its own rate limits and usage terms) in the
critical path of every run, to solve a problem that is static data.

## Files changed

- **`tools/chain_schedule_scraper/scrape_cineplex.py`** — new.
  `fetch_cineplex() -> (cinemas, screenings)`, mirroring
  `scrape_cinestar.py`'s structure (which is the right model here, since
  Cineplex's API is the easy kind, not the CinemaxX kind).
- **`tools/chain_schedule_scraper/test_scrape_cineplex.py`** — new. 12
  tests, network-free, plain `unittest` so they run under either
  `python -m unittest discover tools/chain_schedule_scraper` or `pytest`
  with no new dependency. All pass. They cover the language-tag parsing
  (including the real-world case-mismatch and the "film actually named
  OV Kids" false positive), the schema shape, both failure modes, and a
  bounding-box sanity check on all 96 hardcoded coordinates.
- **`tools/chain_schedule_scraper/main.py`** — `"cineplex": fetch_cineplex`
  added to `SCRAPERS`. Nothing else — the existing top-level try/except
  already keeps a Cineplex failure from sinking CinemaxX and CineStar.
- **`README.md`** — Cineplex is no longer "to follow", plus a note on what
  `screenings[].language` does and doesn't mean per chain.

`python3 -m py_compile` clean on all three Python files.

## A note on `language`

Cineplex has no language field. What it has is a release-type tag glued
onto the end of each performance's `title` — the performance title is the
film's `displayTitle` plus one of `2D`, `3D`, `2D OV`, `2D OmU`,
`2D OmeU`, `2D UKR`, `2D Cinity`, `3D HDR`, `2D OV ScreenX`, `EVENT` and
similar. Only `OV` / `OmU` / `OmeU` say anything about language, so only
those three are reported; everything else yields `null` rather than an
invented `"Deutsch"`, because the API never claims that. Across the
11,939-screening dry run that came out as 337 `OV`, 509 `OmU`, 70 `OmeU`,
11,023 `null`.

Only the part of the performance title *after* the film's display title is
searched, so a film whose actual name contains "OV" isn't mislabelled.

## What could break this

- The table above going stale as Cineplex opens/closes locations. Visible
  in the log; a one-line fix.
- `tickets.cineplex.de` starting to require the OAuth token its own SPA
  already (pointlessly) requests. Would show up as a wall of `HTTP 401`
  in `[cineplex] film lookup failed for center …`.
- The `films` endpoint gaining pagination or a required date parameter.
  Would show up as suspiciously small screening counts rather than as an
  error — worth an eyeball on the total in the Action log now and then.
  The dry-run baseline to compare against is ~12,000 screenings across 90
  centers.
