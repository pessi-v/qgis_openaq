"""
Pure-Python data models for the OpenAQ plugin.
No QGIS or Qt imports — these must be testable without a running QGIS instance.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Union


class Granularity(Enum):
    RAW = "raw"
    HOURLY = "hourly"
    DAILY = "daily"

    def endpoint_suffix(self) -> str:
        return {
            Granularity.RAW: "/measurements",
            Granularity.HOURLY: "/measurements/hourly",
            Granularity.DAILY: "/measurements/daily",
        }[self]


@dataclass
class BboxFilter:
    """Rectangular spatial filter.

    NOTE: OpenAQ uses longitude-first ordering: minLon,minLat,maxLon,maxLat.
    This matches QGIS's (x, y) = (lon, lat) convention but is opposite the
    common lat/lon reading order — be careful when reading user-facing values.
    """
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def to_api_params(self) -> dict:
        return {"bbox": f"{self.min_lon},{self.min_lat},{self.max_lon},{self.max_lat}"}

    def description(self) -> str:
        return (
            f"Rect [{self.min_lon:.4f},{self.min_lat:.4f} → "
            f"{self.max_lon:.4f},{self.max_lat:.4f}]"
        )


@dataclass
class CircleFilter:
    """Circular spatial filter (centre + radius).

    NOTE: OpenAQ uses longitude-first: coordinates=lon,lat.
    Radius is in metres, clamped to 1–25 000 m by the API.
    """
    lon: float
    lat: float
    radius_m: int

    def to_api_params(self) -> dict:
        return {
            "coordinates": f"{self.lon},{self.lat}",
            "radius": max(1, min(25000, self.radius_m)),
        }

    def description(self) -> str:
        return f"Circle [{self.lon:.4f},{self.lat:.4f} r={self.radius_m}m]"


SpatialFilter = Union[BboxFilter, CircleFilter]


@dataclass
class Parameter:
    id: int
    name: str
    units: str
    display_name: str


@dataclass
class Sensor:
    id: int
    name: str
    parameter: Parameter


@dataclass
class Location:
    id: int
    name: str
    # Longitude-first, consistent with QGIS (x, y) convention.
    lon: float
    lat: float
    sensors: List[Sensor]
    is_monitor: bool = False


@dataclass
class Measurement:
    sensor_id: int
    location_id: Optional[int]
    location_name: Optional[str]
    # Coordinates may come from the measurement itself or be inherited from
    # the parent Location when the sensor response omits them.
    lon: Optional[float]
    lat: Optional[float]
    parameter: Parameter
    value: float
    datetime_from: Optional[datetime]
    datetime_to: Optional[datetime]


@dataclass
class QueryParams:
    spatial_filter: SpatialFilter
    datetime_from: datetime
    datetime_to: datetime
    parameter_ids: List[int]
    granularity: Granularity = Granularity.RAW
    monitor_only: bool = False

    def cache_key(self) -> str:
        """Stable hex key used for exact-match cache lookup."""
        payload = {
            "spatial": self.spatial_filter.to_api_params(),
            "from": self.datetime_from.isoformat(),
            "to": self.datetime_to.isoformat(),
            "params": sorted(self.parameter_ids),
            "granularity": self.granularity.value,
            "monitor_only": self.monitor_only,
        }
        blob = json.dumps(payload, sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()

    def human_label(self, parameter_names: List[str]) -> str:
        """Short human-readable label for display in the cache list."""
        params = "+".join(parameter_names) if parameter_names else "?"
        spatial = self.spatial_filter.description()
        dt_from = self.datetime_from.strftime("%Y-%m-%d %H:%M")
        dt_to = self.datetime_to.strftime("%Y-%m-%d %H:%M")
        return f"{params} | {spatial} | {dt_from} → {dt_to} [{self.granularity.value}]"
