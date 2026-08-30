"""
Tests for the Kinopolis scraper.

Same approach as the app-side provider tests: the HTTP layer is mocked and
only the parsing/merging logic is exercised — nothing here touches the real
network, so these stay green whether or not iframe.ts.kinopolis.de is up.

Run with either `python3 -m unittest test_scrape_kinopolis` or `pytest`.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import scrape_kinopolis


class FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


CENTERS = [
    {
        "id": 6,
        "name": "Mathäser Filmpalast",
        "shortName": "mm",
        "cityName": "München",
        "winticketId": "20000000014TTMBFYG",
        "latitude": 48.13903,
        "longitude": 11.5643,
    },
    {
        "id": 11,
        "name": "KINOPOLIS Darmstadt",
        "shortName": "kp",
        "cityName": "Darmstadt",
        "winticketId": "20000000014SPADYMD",
        "latitude": 49.872496,
        "longitude": 8.632856,
    },
]

FILMS = [
    {
        "id": "F1",
        "title": "Minions + Monster",
        "performances": [
            {
                "id": "P1",
                "filmTitle": "Minions + Monster",
                "displayTitle": "3D: D-BOX - Minions 3 (Atmos)",
                "performanceDateTime": "2026-08-31T20:30:00+02:00",
                "cinemaDate": "2026-08-31",
                "languageInformation": {"language": "", "hasSubtitle": False, "subtitleLanguage": ""},
            },
            {
                "id": "P2",
                "filmTitle": "Minions + Monster",
                "performanceDateTime": "2026-09-01T17:00:00+02:00",
                "languageInformation": {"language": "OV", "hasSubtitle": False, "subtitleLanguage": ""},
            },
        ],
    },
    {
        "id": "F2",
        "title": "Toy Story 5",
        "performances": [
            # Missing performanceDateTime — must be skipped, not crash.
            {"id": "P3", "filmTitle": "Toy Story 5"},
            # Missing id — likewise.
            {"filmTitle": "Toy Story 5", "performanceDateTime": "2026-09-02T14:00:00+02:00"},
            # No filmTitle: falls back to the film-level title.
            {"id": "P4", "performanceDateTime": "2026-09-02T20:00:00+02:00"},
        ],
    },
    # A film with no performances at all in the window.
    {"id": "F3", "title": "Vaiana", "performances": []},
]


def make_router(centers_status=200, centers_body=CENTERS, session_status=200, films_status=200, films_body=FILMS):
    """Builds a fake requests.get that answers the three endpoints by path."""

    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/centers"):
            return FakeResponse(centers_body if centers_status == 200 else None, centers_status)
        if url.endswith("/session"):
            assert headers and headers.get("CENTER-OID"), "session request must carry CENTER-OID"
            body = {"sessionId": "sess-" + headers["CENTER-OID"], "expires": "2026-09-01T00:00:00Z"}
            return FakeResponse(body if session_status == 200 else None, session_status)
        if url.endswith("/films"):
            assert headers and headers.get("CENTER-OID"), "film request must carry CENTER-OID"
            assert headers.get("SESSION-ID"), "film request must carry SESSION-ID"
            assert params and params.get("cinemadate.from") and params.get("cinemadate.to")
            return FakeResponse(films_body if films_status == 200 else None, films_status)
        raise AssertionError(f"unexpected URL {url}")

    return fake_get


class FetchCinemasTests(unittest.TestCase):
    def test_maps_centers_to_the_shared_schema(self):
        with patch("scrape_kinopolis.requests.get", make_router()):
            cinemas = scrape_kinopolis.fetch_cinemas()
        self.assertEqual(len(cinemas), 2)
        first = cinemas[0]
        self.assertEqual(first["id"], "kinopolis:6")
        self.assertEqual(first["chain"], "kinopolis")
        self.assertEqual(first["name"], "Mathäser Filmpalast")
        self.assertEqual(first["city"], "München")
        self.assertAlmostEqual(first["lat"], 48.13903)
        self.assertAlmostEqual(first["lng"], 11.5643)
        self.assertEqual(first["_center_oid"], "20000000014TTMBFYG")

    def test_skips_a_center_without_a_ticketing_oid(self):
        body = CENTERS + [{"id": 99, "name": "Nowhere", "cityName": "X", "latitude": 1.0, "longitude": 2.0}]
        with patch("scrape_kinopolis.requests.get", make_router(centers_body=body)):
            cinemas = scrape_kinopolis.fetch_cinemas()
        self.assertEqual([c["id"] for c in cinemas], ["kinopolis:6", "kinopolis:11"])

    def test_returns_nothing_when_the_center_list_fails(self):
        with patch("scrape_kinopolis.requests.get", make_router(centers_status=503)):
            self.assertEqual(scrape_kinopolis.fetch_cinemas(), [])


class FetchKinopolisTests(unittest.TestCase):
    def test_parses_performances_into_screenings(self):
        with patch("scrape_kinopolis.requests.get", make_router()):
            cinemas, screenings = scrape_kinopolis.fetch_kinopolis()

        self.assertEqual(len(cinemas), 2)
        # Two cinemas x 3 usable performances each.
        self.assertEqual(len(screenings), 6)

        first = screenings[0]
        self.assertEqual(first["id"], "kinopolis:P1")
        self.assertEqual(first["cinemaId"], "kinopolis:6")
        self.assertEqual(first["startDate"], "2026-08-31T20:30:00+02:00")
        self.assertIsNone(first["language"])
        # The clean catalogue title wins over the marketing displayTitle.
        self.assertEqual(first["filmTitle"], "Minions + Monster")

        self.assertEqual(screenings[1]["language"], "OV")
        # Fell back to the film-level title.
        self.assertEqual(screenings[2]["id"], "kinopolis:P4")
        self.assertEqual(screenings[2]["filmTitle"], "Toy Story 5")

    def test_strips_internal_keys_from_the_returned_cinemas(self):
        with patch("scrape_kinopolis.requests.get", make_router()):
            cinemas, _ = scrape_kinopolis.fetch_kinopolis()
        for cinema in cinemas:
            self.assertFalse([k for k in cinema if k.startswith("_")], cinema)

    def test_one_cinema_failing_still_keeps_the_cinema_list(self):
        with patch("scrape_kinopolis.requests.get", make_router(films_status=401)):
            cinemas, screenings = scrape_kinopolis.fetch_kinopolis()
        self.assertEqual(len(cinemas), 2)
        self.assertEqual(screenings, [])

    def test_session_failure_skips_that_cinema_without_raising(self):
        with patch("scrape_kinopolis.requests.get", make_router(session_status=500)):
            cinemas, screenings = scrape_kinopolis.fetch_kinopolis()
        self.assertEqual(len(cinemas), 2)
        self.assertEqual(screenings, [])

    def test_no_centers_means_no_work(self):
        with patch("scrape_kinopolis.requests.get", make_router(centers_status=500)):
            self.assertEqual(scrape_kinopolis.fetch_kinopolis(), ([], []))


class LanguageTests(unittest.TestCase):
    def test_blank_and_missing_language_become_none(self):
        self.assertIsNone(scrape_kinopolis._language_of({}))
        self.assertIsNone(scrape_kinopolis._language_of({"languageInformation": None}))
        self.assertIsNone(scrape_kinopolis._language_of({"languageInformation": {"language": "  "}}))
        self.assertEqual(scrape_kinopolis._language_of({"languageInformation": {"language": "OmU"}}), "OmU")


if __name__ == "__main__":
    unittest.main()
