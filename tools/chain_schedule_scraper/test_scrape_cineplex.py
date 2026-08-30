"""
Tests for `scrape_cineplex`.

Written as plain `unittest` cases so they run with either
`python -m unittest discover tools/chain_schedule_scraper` or `pytest`,
without adding a test dependency to `requirements.txt`.

Nothing here touches the network: `scrape_cineplex._get` is replaced with a
stub returning canned payloads whose shapes were copied from real live
responses (Cineplex Leipzig, center 356) rather than invented.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import scrape_cineplex  # noqa: E402


CENTERS = [
    {"id": 356, "name": "Cineplex Leipzig"},
    {"id": 321, "name": "Cinema Berlin [geschlossen]"},
    {"id": 999999, "name": "Cineplex Somewhere New"},
]

FILMS = [
    {
        "displayTitle": "Spider-Man: Brand New Day",
        "id": "8BF31000012FWBXJYB",
        "performances": [
            {
                "id": "0DFAEB00023FWBXJYB",
                "title": "Spider-Man: Brand New Day 2D OV",
                "performanceDateTime": "2026-08-30T10:45:00+02:00",
                "auditorium": {"name": "Kino 2", "number": 2},
            },
            {
                "id": "0CFAEB00023FWBXJYB",
                "title": "Spider-Man: Brand New Day 3D",
                "performanceDateTime": "2026-08-30T13:45:00+02:00",
                "auditorium": {"name": "Kino 1", "number": 1},
            },
            # Real responses do contain films with no performances at all;
            # a performance missing its id or datetime is defensive cover.
            {"id": None, "title": "broken", "performanceDateTime": "2026-08-30T20:00:00+02:00"},
        ],
    },
    {"displayTitle": "André Rieu's 2026 Sommerkonzert", "id": "01241000012FWBXJYB", "performances": []},
    {"displayTitle": None, "id": "X", "performances": [{"id": "Y", "performanceDateTime": "2026-09-01T18:00:00+02:00"}]},
]


class StubAPI:
    """Stands in for `scrape_cineplex._get`."""

    def __init__(self, centers_status=200, films_status=200, centers=CENTERS, films=FILMS):
        self.centers_status = centers_status
        self.films_status = films_status
        self.centers = centers
        self.films = films
        self.paths: list[str] = []

    def __call__(self, path: str):
        self.paths.append(path)
        if path == "/cinemaCenters":
            return (self.centers if self.centers_status == 200 else None), self.centers_status
        if path.endswith("/films"):
            return (self.films if self.films_status == 200 else None), self.films_status
        raise AssertionError(f"unexpected path {path}")


class LanguageForTests(unittest.TestCase):
    def test_reports_ov_from_the_suffix(self):
        self.assertEqual(
            scrape_cineplex.language_for("Die Odyssee 2D OV", "Die Odyssee"), "OV"
        )

    def test_reports_omu_and_omeu_distinctly(self):
        self.assertEqual(scrape_cineplex.language_for("Mutiny 2D OmU", "Mutiny"), "OmU")
        self.assertEqual(scrape_cineplex.language_for("Mutiny 2D OmeU", "Mutiny"), "OmeU")

    def test_plain_and_premium_formats_report_no_language(self):
        for suffix in ("2D", "3D", "2D Cinity", "3D HDR", "2D ScreenX", "EVENT"):
            with self.subTest(suffix=suffix):
                self.assertIsNone(scrape_cineplex.language_for(f"Cars {suffix}", "Cars"))

    def test_case_differing_display_title_still_matches(self):
        # Seen live: film "Paw Patrol: Der Dino Film" vs performance
        # "PAW Patrol: Der Dino Film 2D OV".
        self.assertEqual(
            scrape_cineplex.language_for("PAW Patrol: Der Dino Film 2D OV", "Paw Patrol: Der Dino Film"),
            "OV",
        )

    def test_ov_inside_the_film_title_is_not_treated_as_a_language(self):
        self.assertIsNone(scrape_cineplex.language_for("OV Kids 2D", "OV Kids"))

    def test_unrelated_titles_report_nothing_rather_than_guessing(self):
        self.assertIsNone(scrape_cineplex.language_for("Something Else 2D OV", "Die Odyssee"))
        self.assertIsNone(scrape_cineplex.language_for(None, "Die Odyssee"))


class FetchCineplexTests(unittest.TestCase):
    def setUp(self):
        self._real_get = scrape_cineplex._get

    def tearDown(self):
        scrape_cineplex._get = self._real_get

    def test_maps_known_centers_and_skips_unknown_ones(self):
        scrape_cineplex._get = StubAPI()
        cinemas, _ = scrape_cineplex.fetch_cineplex()
        self.assertEqual([c["id"] for c in cinemas], ["cineplex:356"])
        self.assertEqual(
            cinemas[0],
            {
                "id": "cineplex:356",
                "chain": "cineplex",
                "name": "Cineplex Leipzig",
                "city": "Leipzig",
                "lat": 51.3406,
                "lng": 12.3747,
            },
        )

    def test_builds_screenings_in_the_shared_schema(self):
        scrape_cineplex._get = StubAPI()
        _, screenings = scrape_cineplex.fetch_cineplex()
        self.assertEqual(
            screenings[0],
            {
                "id": "cineplex:0DFAEB00023FWBXJYB",
                "cinemaId": "cineplex:356",
                "filmTitle": "Spider-Man: Brand New Day",
                "startDate": "2026-08-30T10:45:00+02:00",
                "language": "OV",
            },
        )
        self.assertEqual(screenings[1]["language"], None)

    def test_skips_films_and_performances_missing_required_fields(self):
        scrape_cineplex._get = StubAPI()
        _, screenings = scrape_cineplex.fetch_cineplex()
        # 3 performances offered, but one has no id and one film has no title.
        self.assertEqual(len(screenings), 2)

    def test_center_list_failure_yields_nothing_rather_than_raising(self):
        stub = StubAPI(centers_status=503)
        scrape_cineplex._get = stub
        self.assertEqual(scrape_cineplex.fetch_cineplex(), ([], []))
        self.assertEqual(stub.paths, ["/cinemaCenters"])

    def test_film_request_failure_keeps_the_cinema_but_drops_its_screenings(self):
        scrape_cineplex._get = StubAPI(films_status=500)
        cinemas, screenings = scrape_cineplex.fetch_cineplex()
        self.assertEqual(len(cinemas), 1)
        self.assertEqual(screenings, [])

    def test_every_hardcoded_location_looks_sane(self):
        for center_id, (city, lat, lng) in scrape_cineplex.CINEMA_LOCATIONS.items():
            with self.subTest(center=center_id):
                self.assertIsInstance(center_id, int)
                self.assertTrue(city)
                # Germany's bounding box, roughly.
                self.assertTrue(47.2 <= lat <= 55.1, f"{city} latitude {lat} is outside Germany")
                self.assertTrue(5.8 <= lng <= 15.1, f"{city} longitude {lng} is outside Germany")


if __name__ == "__main__":
    unittest.main()
