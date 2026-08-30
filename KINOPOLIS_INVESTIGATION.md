# Kinopolis: investigation and result

**Verdict: found something real.** Kinopolis has a clean, unauthenticated
JSON REST API — closer to CineStar's quality than to CinemaxX's mess — and
`tools/chain_schedule_scraper/scrape_kinopolis.py` now uses it. All 17
locations, including the Mathäser Filmpalast in Munich, are covered by a
single host.

## What was tried

Loaded `kinopolis.de/su/programm` (Main-Taunus) in a real browser and
watched the network. Nothing useful: the programme page is fully
server-rendered PHP, and the only cross-domain requests are ad and consent
scripts (`adspirit.de`, `consentmanager.net`) plus a trailer player. No
XHR/fetch carrying showtimes, so no endpoint to lift from the page itself.
Switching dates just navigates to another server-rendered page
(`/su/programm/tagesprogramm-<date>`). That would have been the
HTML-scraping dead end this project keeps refusing.

The lead was in an inline `<script>` on every Kinopolis site:

```js
centerid          = '20000000014KYVFAJM';
centercode        = 'su';
cloudticketDomain = 'https://shop.kinopolis.de/ct'
cineOrderDomain   = 'https://iframe.ts.kinopolis.de';
```

`iframe.ts.kinopolis.de` is a **CineOrder** ticketing webshop — a
white-label backend, and a JSON API. (Same product family Cineplex's
`tickets.cineplex.de` runs on, and the 18-character Salesforce-shaped IDs
are the giveaway on both.) Clicking any showtime on kinopolis.de lands on
`iframe.ts.kinopolis.de/landingpage?center=<oid>&page=seatingplan&performance=<oid>`,
which is a React app talking to `/api/*`.

`mathaeser.de` was checked separately, as asked. It is the **same
platform**: its config line reads `cineOrderDomain =
'https://iframe.ts.mathaeser.de'` with `centerid = '20000000014TTMBFYG'` —
and that OID is already center id 6 (`Mathäser Filmpalast`) in
`iframe.ts.kinopolis.de`'s own center list. So one host covers the group;
mathaeser.de needs nothing of its own. Same for Gloria Palast (id 16).

**Kinoheld does not carry Kinopolis**, so this is not redundant work. Ran
the app's own `CinemaSearch` GraphQL query against
`graph.kinoheld.de/graphql/v1/query` for München, Darmstadt and Koblenz:
13 / 1 / 2 cinemas returned respectively, all indie houses (Rio Filmpalast,
Museum Lichtspiele, Kommunales Kino Weiterstadt, Schauburg Neuwied …), and
zero Kinopolis, Mathäser, Gloria Palast or Citydome entries in any of them.

## The endpoints

Base: `https://iframe.ts.kinopolis.de/api`

**1. `GET /api/centers`** — public, no headers needed.

```json
[{"id":6,"name":"Mathäser Filmpalast","shortName":"mm","timezone":"Europe/Berlin",
  "cityName":"München","winticketId":"20000000014TTMBFYG",
  "longitude":11.5643,"latitude":48.13903,"currencyCode":"EUR","countryCode":"DEU"}]
```

17 centers, each with **real lat/lng** — so no hardcoded city-coordinate
table like CinemaxX needs. `winticketId` is the center OID the other two
endpoints want; `id` is the small integer used for our `kinopolis:<id>`
cinema ids.

**2. `GET /api/session`** with header `CENTER-OID: <winticketId>`.

```json
{"expires":"2026-09-01T00:39:00.937841Z","sessionId":"658062307332419897b27fffe369b5c4"}
```

Anonymous. No login, no cookies, no token exchange — just ask and you get
one, valid about two days.

**3. `GET /api/films?cinemadate.from=YYYY-MM-DD&cinemadate.to=YYYY-MM-DD&locale=de&include.languageinformation=true`**
with headers `CENTER-OID: <winticketId>` and `SESSION-ID: <sessionId>`.

Returns every film playing at that center in the range, with its showtimes
nested inside — the **whole 14-day window in one request per cinema**, no
per-date looping:

```json
[{"id":"F1","title":"Minions + Monster","lengthInMinutes":95,"genres":[...],
  "performances":[
    {"id":"1F4C8000023BWDJVCF",
     "filmTitle":"Minions + Monster",
     "displayTitle":"3D: D-BOX - Minions 3 (Atmos)",
     "performanceDateTime":"2026-08-31T20:30:00+02:00",
     "performanceEndDateTime":"2026-08-31T22:30:00+02:00",
     "cinemaDate":"2026-08-31",
     "auditoriumName":"Kino 13","is3D":false,
     "languageInformation":{"language":"","hasSubtitle":false,"subtitleLanguage":""}}]}]
```

`startDate` therefore comes out as real ISO-8601 with offset — the same
shape CinemaxX already produces, so **the app side needs no new date
format**.

Two parsing notes worth keeping:

- Use `performances[].filmTitle`, not `displayTitle`. `filmTitle` is the
  clean catalogue title (`"Minions + Monster"`); `displayTitle` is the
  format/marketing variant (`"3D: D-BOX - Minions 3 (Atmos)"`,
  `"D-BOX: Toy Story 5"`) and would never match a watchlist entry.
- `languageInformation` exists on every performance and is **blank on every
  one of them**, at every center checked. OV/OmU is encoded in the title
  instead. The scraper reads the field anyway (in case they start filling
  it) and otherwise emits `null` rather than guessing.

## The one wrinkle, and why it isn't a CinemaxX repeat

Everything except `/centers`, `/versions` and `/session` answers
`401 {"errorMessage":"Unauthorized"}` if the `CENTER-OID` and `SESSION-ID`
headers are missing. That looks alarming given this project's history, but
it is plain session plumbing, not a wall: the session is handed out
anonymously on request, and once the two headers are set every call
succeeds. Nothing here resembles CinemaxX's `minEmbargoLevel` gating.

The header names were confirmed by reading the webshop bundle
(`/assets/index-*.js`), which declares them literally as `SESSION-ID` and
`CENTER-OID`.

## Robustness testing

Ran the real production shape live: for **all 17 centers**, back to back,
`GET /session` then `GET /films` over a 14-day window — 34 consecutive
requests. **All 17 returned HTTP 200**, ~2,855 performances total, with
plausibly different data per location (Mathäser 443, Koblenz 245, HafenCity
280, Gloria Palast 23, Freiberg 77 …). No 401s, no throttling, no
slowdown; single requests came back in roughly 0.4–1.5s. A 60-day range
also worked in one request (data currently thins out around 8 weeks
ahead), so the 14-day window is our choice, not a limit of theirs.

## What was built

- `tools/chain_schedule_scraper/scrape_kinopolis.py` — mirrors
  `scrape_cinestar.py`'s structure: `fetch_cinemas()`, `fetch_session()`,
  `fetch_films()`, `fetch_kinopolis() -> (cinemas, screenings)`. Same
  defensive posture as everything else here: a center that fails its
  session or film lookup is logged and skipped, the rest of the run
  continues, and a failed `/centers` call skips Kinopolis entirely rather
  than raising.
- `tools/chain_schedule_scraper/test_scrape_kinopolis.py` — 9 tests,
  network-free (`requests.get` is patched with a fake router built from
  real response shapes). Covers the schema mapping, the
  `filmTitle`-over-`displayTitle` rule, the blank-language-to-`null` rule,
  malformed performances being skipped, and each failure mode leaving the
  cinema list intact. All 9 pass under
  `python3 -m unittest test_scrape_kinopolis` (run from the scraper
  folder). `pytest` is not installed on this machine, so unittest was used;
  the tests work under either.
- `main.py` — `"kinopolis": fetch_kinopolis` registered in `SCRAPERS`.
- `README.md` — chain list, module list, test list, and the schema notes
  for Kinopolis's date format and language field.

## Next steps

1. Nothing is committed — as always, that's yours to do in GitHub Desktop.
2. The first workflow run after that commit should print a
   `[kinopolis] done: 17 cinema(s), ~2800 screening(s) (0/17 failed)` line.
   A non-zero failure count on the very first run would be new information
   (nothing failed in live testing) and worth looking at before assuming
   it's transient.
3. No app-side change is needed. `kinopolis:` ids and ISO-8601 dates flow
   through `ChainScheduleProvider` exactly like CinemaxX's already do.
4. Not verified end to end from CI: this machine has no outbound network
   for scripts, so the scraper itself was never executed against the live
   API — every request in it was exercised by hand from a browser session
   first, with the exact same paths, params and headers. The first real
   run is in the Action.
5. `Rex Darmstadt` is not in `/api/centers` (Citydome Darmstadt and
   KINOPOLIS Darmstadt are). If a Rex screening is ever expected and
   missing, that's the reason — it isn't on this backend.
