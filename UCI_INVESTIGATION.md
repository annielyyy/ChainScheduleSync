# UCI Kinowelt (uci-kinowelt.de): investigated, not viable — no scraper written

Investigated on 2026-08-30, live against the real site in a browser, the
same way CinemaxX's 401 saga and CineStar's `/api/cinema/` were worked out.

**Verdict: no usable data source found.** No `scrape_uci.py` was written,
and `main.py` / `README.md` were deliberately left untouched. Everything
below is what was actually tried, so the next person doesn't have to redo it.

## What was tried

1. `https://www.uci-kinowelt.de/kinoprogramm` — the chain-wide programme
   page. It is only a cinema picker (23 locations, grouped
   Berlin/Potsdam, Hamburg/Nord, Ost, Süd, West). Each entry is a plain
   `<a href="/kinoprogramm/<slug>">` (e.g. `/kinoprogramm/duesseldorf`,
   `/kinoprogramm/hh-wandsbek-smart-city`). No cinema list endpoint, no
   coordinates anywhere on the page.
2. `https://www.uci-kinowelt.de/kinoprogramm/duesseldorf` — a single
   cinema's programme. Full network trace on load: **not one XHR/fetch to
   any data endpoint**, on this or any other UCI page visited. The only
   JSON the site ever requests is
   `https://www.uci-kinowelt.de/dist-react/envVars.json`, which is Vite
   build config (OAuth client id, SSO cookie domain, an OAuth2 public
   key) and contains no API base URL. Everything else in the trace is
   CSS, fonts, images, and `assets/js/*.js`.
3. Date switching. The date picker is a `<form data-schedule-filters-form>`
   of checkboxes, e.g.
   `<input id="fp-datum-0" type="checkbox" value="= 20260830" data-filter-type="date">`.
   Toggling a date fires **no network request** — it filters markup that
   is already in the document. So the schedule is fully server-rendered
   into the HTML and then filtered client-side. Same shape as the
   Cineplex dead end already documented in TESTER_REPORT.md: the data
   exists in the page, but only as page.
4. Grepped the two real JS bundles for any endpoint string:
   - `/cache-buster-1787741872/assets/js/uci-kinowelt.js` (313 KB): the
     only path literal in the whole file is
     `` `/snippets/film-slider/${p}/${f}` ``, which returns HTML, and
     404s on the id pairs taken from the live page anyway.
   - `/dist-react/assets/main-Db9lf_or.js` (675 KB): the React app here is
     just the header login widget. Its only endpoints are
     `/oauth2/authorize`, `/oauth2/token`, `/oauth2/revoke`, `/login`,
     `/gateway/auth/users/custom/registration`, `/gateway/auth/customer`,
     `/gateway/auth/customer/newsletters` — all on
     `https://mein.uci-kinowelt.de` (per `VITE_URL_UCI_AUTH`). That is
     the customer-account gateway, not a showtime service.
   No `/api/`, `/rest/`, `/graphql/` string appears in either bundle.
5. Blind-probed the obvious candidates on `www.uci-kinowelt.de`:
   `/api/`, `/api/cinema`, `/api/cinemas`, `/sitemap.xml` — all HTTP 404,
   returning the site's HTML 404 page (`content-type: text/html`).
   `/robots.txt` is `User-agent: * / Disallow:` (nothing disallowed —
   not a permission question, just no endpoints to find).

## The one real lead, and why it's closed

Ticket links on the programme page look like

```
https://www.uci-kinowelt.de/kino-buchung/performanceId/577D3000023YWUNWOD/siteId/50/915114
```

`siteId/50` is Düsseldorf, and `577D3000023YWUNWOD` is exactly the
performance-id shape used by the white-label ticketing backend that also
sits behind Cineplex's booking flow (`tickets.cineplex.de`, which exposes
a genuinely clean `/api/ticketing/cinemaCenter/{siteId}/performance/{id}/performances`).
That was the promising hypothesis: UCI on the same vendor, with `siteId`
as the cinema key.

It does not pan out, for a reason nothing on our side can fix:

- `/kino-buchung/...` redirects to **`https://buchung.uci-kinowelt.de`**,
  which serves a **Cloudflare "Wir überprüfen, ob deine Verbindung sicher
  ist" interstitial with a Turnstile human-verification checkbox**. It
  never self-resolved — waited it out three separate times, ~28s, ~10s and
  ~10s, and the page stayed on "Just a moment...".
- The block is host-wide, not page-specific: requesting an API-shaped path
  directly (`https://buchung.uci-kinowelt.de/api/ticketing/cinemaCenter/50/textSnippets`)
  gets the same interstitial rather than any JSON.

Even if that challenge were solved by hand once in a browser, it would be
worthless here: this scraper runs unattended from a GitHub Actions runner
on a datacentre IP, which is precisely the traffic that interstitial
exists to stop. This is categorically different from CinemaxX's 401 —
that was an undocumented wall we could at least sometimes get past by
being patient; this is an explicit, deliberate bot check.

## Why no scraper was written

The schedule *is* extractable from `/kinoprogramm/<slug>` HTML — the
markup carries film titles, performance ids, site ids and dates. That was
not done on purpose:

- Regex/BeautifulSoup over a chain's marketing HTML is exactly the
  brittle approach this project has avoided everywhere else. CinemaxX and
  CineStar are real JSON APIs; Cineplex was left out rather than scraped
  out of a server-rendered payload. UCI is the same call as Cineplex.
- UCI publishes **no coordinates at all** — not in the page, not in
  JSON-LD (there is none), not on `/kinoinformation`. So even a working
  HTML parse would need a hand-maintained 23-entry lat/lng table like
  CinemaxX's `CITY_COORDINATES`, i.e. the fragile part on top of the
  fragile part.

## If someone wants to reopen this

Worth a look, in rough order of promise:

1. **The UCI/Odeon mobile app.** UCI is part of Odeon Cinemas Group; a
   native app would have to talk to a real backend, and that backend
   would not be behind a browser Turnstile check. Nothing about it is
   discoverable from the website, so this needs traffic capture from an
   actual device, not more browsing.
2. **Whether `buchung.uci-kinowelt.de` ever drops the challenge** for
   plain non-browser clients with a normal User-Agent. Cheap to re-test:
   one `curl` against
   `https://buchung.uci-kinowelt.de/api/ticketing/cinemaCenter/50/...`. If
   it ever returns JSON instead of the interstitial, the Cineplex-vendor
   hypothesis above becomes live again and `siteId` (50 = Düsseldorf) is
   already the cinema key.
3. Re-check after any UCI site redesign — the current site is a
   server-rendered Symfony app with a bolt-on React login widget, and a
   move to a real front-end would likely expose a JSON schedule endpoint.

Until one of those changes, UCI stays out of `SCRAPERS`, same as Cineplex.
