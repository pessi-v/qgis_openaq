import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ..core.cache import Cache
from ..core.models import Measurement, Parameter


def _make_measurement(lon=2.35, lat=48.85, value=12.5):
    return Measurement(
        sensor_id=1,
        location_id=100,
        location_name="Test Station",
        lon=lon,
        lat=lat,
        parameter=Parameter(id=2, name="pm25", units="µg/m³", display_name="PM2.5"),
        value=value,
        datetime_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime_to=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
    )


class TestCache(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.cache = Cache(self._tmpdir)

    def test_miss_returns_none(self):
        self.assertIsNone(self.cache.get("nonexistent_key"))

    def test_put_and_get(self):
        key = "abc123"
        measurements = [_make_measurement()]
        path = self.cache.put(key, measurements, label="Test")
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())
        result = self.cache.get(key)
        self.assertEqual(result, path)

    def test_geojson_structure(self):
        key = "xyz"
        self.cache.put(key, [_make_measurement(value=9.0)], label="GeoJSON test")
        path = self.cache.get(key)
        with open(path) as fh:
            data = json.load(fh)
        self.assertEqual(data["type"], "FeatureCollection")
        self.assertEqual(len(data["features"]), 1)
        feat = data["features"][0]
        self.assertEqual(feat["geometry"]["type"], "Point")
        self.assertAlmostEqual(feat["properties"]["value"], 9.0)

    def test_measurements_without_coords_excluded(self):
        key = "nocoords"
        m = _make_measurement()
        m.lon = None
        m.lat = None
        path = self.cache.put(key, [m], label="No coords")
        with open(path) as fh:
            data = json.load(fh)
        self.assertEqual(len(data["features"]), 0)

    def test_list_entries(self):
        self.cache.put("k1", [_make_measurement()], label="Entry 1")
        self.cache.put("k2", [_make_measurement()], label="Entry 2")
        entries = self.cache.list_entries()
        keys = {e["key"] for e in entries}
        self.assertIn("k1", keys)
        self.assertIn("k2", keys)

    def test_delete(self):
        key = "todelete"
        path = self.cache.put(key, [_make_measurement()], label="Delete me")
        self.cache.delete(key)
        self.assertIsNone(self.cache.get(key))
        self.assertFalse(path.exists())

    def test_stale_entry_cleaned_on_list(self):
        key = "stale"
        path = self.cache.put(key, [_make_measurement()], label="Stale")
        path.unlink()  # simulate deleted file
        entries = self.cache.list_entries()
        self.assertNotIn(key, {e["key"] for e in entries})

    def test_index_persists_across_instances(self):
        key = "persist"
        self.cache.put(key, [_make_measurement()], label="Persistent")
        cache2 = Cache(self._tmpdir)
        self.assertIsNotNone(cache2.get(key))


if __name__ == "__main__":
    unittest.main()
