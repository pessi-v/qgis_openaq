import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from ..core.client import AuthError, OpenAQClient, RateLimitError
from ..core.models import BboxFilter, CircleFilter, Granularity
from ..core.rate_limiter import RateLimiter


def _make_client():
    return OpenAQClient(api_key="test-key", rate_limiter=RateLimiter())


def _meta(found, page=1, limit=1000):
    return {"found": found, "page": page, "limit": limit}


class TestGetParameters(unittest.TestCase):
    @patch("requests.Session.get")
    def test_returns_parameters(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "meta": _meta(2),
                "results": [
                    {"id": 2, "name": "pm25", "units": "µg/m³", "displayName": "PM2.5"},
                    {"id": 5, "name": "no2",  "units": "µg/m³", "displayName": "NO₂"},
                ],
            },
        )
        client = _make_client()
        params = client.get_parameters()
        self.assertEqual(len(params), 2)
        self.assertEqual(params[0].name, "pm25")
        self.assertEqual(params[1].display_name, "NO₂")


class TestGetLocations(unittest.TestCase):
    @patch("requests.Session.get")
    def test_filters_sensors_by_parameter_id(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "meta": _meta(1),
                "results": [{
                    "id": 10,
                    "name": "Station A",
                    "coordinates": {"longitude": 2.35, "latitude": 48.85},
                    "isMonitor": True,
                    "sensors": [
                        {"id": 101, "name": "s1", "parameter": {"id": 2,  "name": "pm25", "units": "µg/m³", "displayName": "PM2.5"}},
                        {"id": 102, "name": "s2", "parameter": {"id": 99, "name": "temp",  "units": "K",    "displayName": "Temperature"}},
                    ],
                }],
            },
        )
        client = _make_client()
        locs = client.get_locations(BboxFilter(-1, 48, 3, 50), parameter_ids=[2])
        self.assertEqual(len(locs), 1)
        self.assertEqual(len(locs[0].sensors), 1)
        self.assertEqual(locs[0].sensors[0].id, 101)

    @patch("requests.Session.get")
    def test_location_excluded_when_no_matching_sensors(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "meta": _meta(1),
                "results": [{
                    "id": 10,
                    "name": "Station A",
                    "coordinates": {"longitude": 2.35, "latitude": 48.85},
                    "isMonitor": False,
                    "sensors": [
                        {"id": 101, "name": "s1", "parameter": {"id": 99, "name": "temp", "units": "K", "displayName": "Temp"}},
                    ],
                }],
            },
        )
        client = _make_client()
        locs = client.get_locations(BboxFilter(-1, 48, 3, 50), parameter_ids=[2])
        self.assertEqual(locs, [])

    @patch("requests.Session.get")
    def test_circle_filter_params(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"meta": _meta(0), "results": []},
        )
        client = _make_client()
        client.get_locations(CircleFilter(136.9, 35.1, 5000), parameter_ids=[2])
        call_params = mock_get.call_args[1]["params"]
        self.assertIn("coordinates", call_params)
        self.assertIn("radius", call_params)
        self.assertNotIn("bbox", call_params)


class TestGetMeasurements(unittest.TestCase):
    @patch("requests.Session.get")
    def test_returns_measurements(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "meta": _meta(1),
                "results": [{
                    "value": 12.5,
                    "parameter": {"id": 2, "name": "pm25", "units": "µg/m³", "displayName": "PM2.5"},
                    "period": {
                        "datetimeFrom": {"utc": "2026-01-01T00:00:00+00:00"},
                        "datetimeTo":   {"utc": "2026-01-01T01:00:00+00:00"},
                    },
                    "coordinates": {"longitude": 2.35, "latitude": 48.85},
                }],
            },
        )
        client = _make_client()
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        measurements = client.get_measurements(101, dt, dt, Granularity.HOURLY)
        self.assertEqual(len(measurements), 1)
        self.assertAlmostEqual(measurements[0].value, 12.5)
        self.assertEqual(measurements[0].sensor_id, 101)
        self.assertAlmostEqual(measurements[0].lon, 2.35)


class TestErrorHandling(unittest.TestCase):
    @patch("requests.Session.get")
    def test_401_raises_auth_error(self, mock_get):
        mock_get.return_value = MagicMock(status_code=401)
        client = _make_client()
        with self.assertRaises(AuthError):
            client.get_parameters()

    @patch("requests.Session.get")
    def test_429_retries_then_raises(self, mock_get):
        mock_get.return_value = MagicMock(status_code=429)
        client = _make_client()
        with patch("time.sleep"):  # don't actually sleep in tests
            with self.assertRaises(RateLimitError):
                client.get_parameters()

    def test_local_rate_limit_raises(self):
        rl = RateLimiter()
        # Fill the minute bucket.
        from datetime import datetime, timezone
        ts = datetime.now(tz=timezone.utc)
        for _ in range(RateLimiter.MINUTE_LIMIT):
            rl._timestamps.append(ts)
        client = OpenAQClient("key", rl)
        with self.assertRaises(RateLimitError):
            client.get_parameters()


if __name__ == "__main__":
    unittest.main()
