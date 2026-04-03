"""
Local GeoJSON-based query cache.

Each completed fetch is stored as a GeoJSON FeatureCollection file.  An index
JSON file maps cache keys (SHA-256 of QueryParams) to file metadata so
previously fetched datasets can be listed and reloaded without re-fetching.

Cache lookup is exact-match only — no spatial/temporal overlap detection.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .models import Measurement


class Cache:
    _INDEX_FILE = "openaq_cache_index.json"

    def __init__(self, cache_dir: str) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / self._INDEX_FILE
        self._index: Dict[str, dict] = self._load_index()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[Path]:
        """Return the path to the cached GeoJSON file, or None on a miss."""
        entry = self._index.get(key)
        if not entry:
            return None
        path = self._dir / entry["file"]
        if path.exists():
            return path
        # Stale index entry — clean up.
        del self._index[key]
        self._save_index()
        return None

    def put(self, key: str, measurements: List[Measurement], label: str) -> Path:
        """Write measurements to a GeoJSON file and record it in the index."""
        filename = f"openaq_{key[:16]}.geojson"
        path = self._dir / filename

        features = []
        for m in measurements:
            if m.lon is None or m.lat is None:
                continue
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [m.lon, m.lat]},
                "properties": {
                    "sensor_id": m.sensor_id,
                    "location_id": m.location_id,
                    "location_name": m.location_name,
                    "parameter_id": m.parameter.id,
                    "parameter_name": m.parameter.name,
                    "parameter_display": m.parameter.display_name,
                    "units": m.parameter.units,
                    "value": m.value,
                    "datetime_from": (
                        m.datetime_from.isoformat() if m.datetime_from else None
                    ),
                    "datetime_to": (
                        m.datetime_to.isoformat() if m.datetime_to else None
                    ),
                },
            })

        geojson = {"type": "FeatureCollection", "features": features}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(geojson, fh)

        self._index[key] = {
            "file": filename,
            "label": label,
            "cached_at": datetime.now(tz=timezone.utc).isoformat(),
            "feature_count": len(features),
        }
        self._save_index()
        return path

    def list_entries(self) -> List[dict]:
        """Return all valid cache entries (stale files filtered out)."""
        valid = []
        stale_keys = []
        for key, meta in self._index.items():
            if (self._dir / meta["file"]).exists():
                valid.append({"key": key, **meta})
            else:
                stale_keys.append(key)
        if stale_keys:
            for k in stale_keys:
                del self._index[k]
            self._save_index()
        return valid

    def delete(self, key: str) -> None:
        """Remove a cache entry and its file."""
        entry = self._index.pop(key, None)
        if entry:
            path = self._dir / entry["file"]
            if path.exists():
                path.unlink()
            self._save_index()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_index(self) -> Dict[str, dict]:
        if self._index_path.exists():
            with open(self._index_path, encoding="utf-8") as fh:
                return json.load(fh)
        return {}

    def _save_index(self) -> None:
        with open(self._index_path, "w", encoding="utf-8") as fh:
            json.dump(self._index, fh, indent=2)
