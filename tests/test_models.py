import unittest
from datetime import datetime, timezone

from ..core.models import (
    BboxFilter, CircleFilter, Granularity, QueryParams,
)


class TestBboxFilter(unittest.TestCase):
    def test_to_api_params(self):
        f = BboxFilter(-74.1, 40.6, -73.8, 40.9)
        self.assertEqual(f.to_api_params(), {"bbox": "-74.1,40.6,-73.8,40.9"})

    def test_description(self):
        f = BboxFilter(-74.1, 40.6, -73.8, 40.9)
        desc = f.description()
        self.assertIn("Rect", desc)
        self.assertIn("-74.1000", desc)


class TestCircleFilter(unittest.TestCase):
    def test_to_api_params(self):
        f = CircleFilter(136.906, 35.149, 12000)
        p = f.to_api_params()
        self.assertEqual(p["coordinates"], "136.906,35.149")
        self.assertEqual(p["radius"], 12000)

    def test_radius_clamped(self):
        self.assertEqual(CircleFilter(0, 0, 0).to_api_params()["radius"], 1)
        self.assertEqual(CircleFilter(0, 0, 99999).to_api_params()["radius"], 25000)

    def test_description(self):
        f = CircleFilter(136.906, 35.149, 5000)
        self.assertIn("Circle", f.description())
        self.assertIn("5000", f.description())


class TestQueryParams(unittest.TestCase):
    def _make(self, **kwargs):
        defaults = dict(
            spatial_filter=BboxFilter(-1.0, 51.0, 0.0, 52.0),
            datetime_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime_to=datetime(2026, 1, 2, tzinfo=timezone.utc),
            parameter_ids=[2, 5],
            granularity=Granularity.HOURLY,
            monitor_only=False,
        )
        defaults.update(kwargs)
        return QueryParams(**defaults)

    def test_cache_key_is_stable(self):
        q1 = self._make()
        q2 = self._make()
        self.assertEqual(q1.cache_key(), q2.cache_key())

    def test_cache_key_differs_on_params(self):
        q1 = self._make(parameter_ids=[2])
        q2 = self._make(parameter_ids=[5])
        self.assertNotEqual(q1.cache_key(), q2.cache_key())

    def test_cache_key_param_order_independent(self):
        q1 = self._make(parameter_ids=[2, 5])
        q2 = self._make(parameter_ids=[5, 2])
        self.assertEqual(q1.cache_key(), q2.cache_key())

    def test_cache_key_differs_on_granularity(self):
        q1 = self._make(granularity=Granularity.RAW)
        q2 = self._make(granularity=Granularity.DAILY)
        self.assertNotEqual(q1.cache_key(), q2.cache_key())

    def test_human_label(self):
        q = self._make()
        label = q.human_label(["PM2.5", "NO2"])
        self.assertIn("PM2.5", label)
        self.assertIn("NO2", label)
        self.assertIn("hourly", label)


if __name__ == "__main__":
    unittest.main()
