"""
OpenAQ v3 API client.

No QGIS or Qt imports — fully testable with plain Python and mocked HTTP.

Fetch pattern
-------------
1. GET /v3/locations?bbox=...  (or coordinates+radius)
   Returns monitoring stations; each station includes a sensors[] array.

2. For each sensor matching the requested parameters:
   GET /v3/sensors/{id}/measurements[/hourly|/daily]

NOTE on coordinate ordering: OpenAQ v3 is inconsistent between endpoints.
  bbox        = minLon,minLat,maxLon,maxLat  (longitude-first)
  coordinates = lat,lon                      (geographic order, latitude-first)
See CircleFilter.to_api_params() for the circle-query encoding.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Callable, Dict, List, Optional, Union

import requests

from .models import (
    BboxFilter,
    CircleFilter,
    Granularity,
    Location,
    Measurement,
    Parameter,
    QueryParams,
    Sensor,
    SpatialFilter,
)
from .rate_limiter import RateLimiter


class OpenAQError(Exception):
    pass


class AuthError(OpenAQError):
    pass


class RateLimitError(OpenAQError):
    pass


ProgressCallback = Optional[Callable[[int, int], None]]


class OpenAQClient:
    BASE_URL = "https://api.openaq.org/v3"
    _MAX_RETRIES = 5
    _PAGE_SIZE = 1000

    def __init__(self, api_key: str, rate_limiter: RateLimiter) -> None:
        self._rate_limiter = rate_limiter
        self._session = requests.Session()
        self._session.headers.update({"X-API-Key": api_key})

    def update_api_key(self, api_key: str) -> None:
        self._session.headers.update({"X-API-Key": api_key})

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def get_parameters(self) -> List[Parameter]:
        """Fetch all pollutant parameters. Call once on plugin load."""
        results = self._paginate(
            "/parameters",
            {"parameter_type": "pollutant", "limit": self._PAGE_SIZE},
        )
        return [_parse_parameter(r) for r in results]

    def get_locations(
        self,
        spatial_filter: SpatialFilter,
        parameter_ids: List[int],
        monitor_only: bool = False,
        progress_cb: ProgressCallback = None,
    ) -> List[Location]:
        """Return stations within the spatial filter that carry any of the
        requested parameters."""
        params: dict = spatial_filter.to_api_params()
        if parameter_ids:
            # Pass as a list so requests repeats the key for each value:
            # ?parameters_id=2&parameters_id=7
            # A comma-joined string is NOT parsed correctly by the OpenAQ API.
            params["parameters_id"] = parameter_ids
        if monitor_only:
            params["monitor"] = "true"
        params["limit"] = self._PAGE_SIZE

        raw = self._paginate("/locations", params, progress_cb)
        locations = []
        param_id_set = set(parameter_ids)
        for r in raw:
            coords = r.get("coordinates") or {}
            # Keep sensors whose parameter ID is in our requested set, OR whose
            # parameter name matches (handles ID variants for the same pollutant).
            sensors = [
                _parse_sensor(s)
                for s in r.get("sensors", [])
                if s.get("parameter", {}).get("id") in param_id_set
            ]
            if not sensors:
                continue
            locations.append(Location(
                id=r["id"],
                name=r.get("name") or "",
                lon=coords.get("longitude", 0.0),
                lat=coords.get("latitude", 0.0),
                sensors=sensors,
                is_monitor=r.get("isMonitor", False),
            ))
        return locations

    def get_measurements(
        self,
        sensor_id: int,
        datetime_from: datetime,
        datetime_to: datetime,
        granularity: Granularity = Granularity.RAW,
        progress_cb: ProgressCallback = None,
    ) -> List[Measurement]:
        """Fetch measurements for a single sensor."""
        path = f"/sensors/{sensor_id}{granularity.endpoint_suffix()}"
        params = {
            "datetime_from": datetime_from.isoformat(),
            "datetime_to": datetime_to.isoformat(),
            "limit": self._PAGE_SIZE,
        }
        raw = self._paginate(path, params, progress_cb)
        return [_parse_measurement(r, sensor_id) for r in raw]

    def estimate_request_count(
        self,
        spatial_filter: SpatialFilter,
        parameter_ids: List[int],
        monitor_only: bool = False,
    ) -> int:
        """Return a rough upper-bound request count without fetching data.

        Makes one /locations request (with limit=1) to read meta.found, then
        estimates total pages × sensors. Used to warn the user before committing.
        """
        params: dict = spatial_filter.to_api_params()
        if parameter_ids:
            params["parameters_id"] = ",".join(str(i) for i in parameter_ids)
        if monitor_only:
            params["monitor"] = "true"
        params["limit"] = 1

        data = self._get("/locations", params)
        found = data.get("meta", {}).get("found", 0)
        # 1 request to get locations (will need ceil(found/1000) pages) +
        # roughly found × len(parameter_ids) sensor measurement requests.
        location_pages = max(1, (found + self._PAGE_SIZE - 1) // self._PAGE_SIZE)
        sensor_requests = found * len(parameter_ids)
        return location_pages + sensor_requests

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        """Single GET with rate-limit check and exponential backoff on 429."""
        if not self._rate_limiter.can_request():
            raise RateLimitError(
                "Local rate limit reached. Wait before making more requests."
            )

        backoff = 1.0
        for attempt in range(self._MAX_RETRIES):
            self._rate_limiter.record_request()
            try:
                resp = self._session.get(
                    f"{self.BASE_URL}{path}", params=params, timeout=30
                )
            except requests.RequestException as exc:
                raise OpenAQError(f"Network error: {exc}") from exc

            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            if resp.status_code == 401:
                raise AuthError("Invalid or missing API key.")
            resp.raise_for_status()

        raise RateLimitError(
            f"Received 429 on {self._MAX_RETRIES} consecutive attempts."
        )

    def _paginate(
        self,
        path: str,
        params: Optional[dict] = None,
        progress_cb: ProgressCallback = None,
    ) -> list:
        """Walk all pages and return a flat list of result objects."""
        params = dict(params or {})
        params.setdefault("limit", self._PAGE_SIZE)
        params["page"] = 1
        results: list = []

        while True:
            data = self._get(path, params)
            page_results = data.get("results", [])
            results.extend(page_results)

            found = data.get("meta", {}).get("found", 0)
            if progress_cb:
                progress_cb(len(results), found)

            if not page_results or len(results) >= found:
                break
            params["page"] += 1

        return results


# ------------------------------------------------------------------
# Response-parsing helpers
# ------------------------------------------------------------------

def _parse_parameter(raw: dict) -> Parameter:
    return Parameter(
        id=raw["id"],
        name=raw.get("name", ""),
        units=raw.get("units", ""),
        display_name=raw.get("displayName") or raw.get("name", ""),
    )


def _parse_sensor(raw: dict) -> Sensor:
    return Sensor(
        id=raw["id"],
        name=raw.get("name", ""),
        parameter=_parse_parameter(raw.get("parameter", {})),
    )


def _parse_measurement(raw: dict, sensor_id: int) -> Measurement:
    period = raw.get("period") or {}
    dt_from_raw = period.get("datetimeFrom") or {}
    dt_to_raw = period.get("datetimeTo") or {}
    coords = raw.get("coordinates") or {}

    return Measurement(
        sensor_id=sensor_id,
        location_id=None,       # filled in by FetchTask after joining with Location
        location_name=None,
        lon=coords.get("longitude"),
        lat=coords.get("latitude"),
        parameter=_parse_parameter(raw.get("parameter") or {}),
        value=raw.get("value", 0.0),
        datetime_from=_parse_dt(
            dt_from_raw.get("utc") if isinstance(dt_from_raw, dict) else dt_from_raw
        ),
        datetime_to=_parse_dt(
            dt_to_raw.get("utc") if isinstance(dt_to_raw, dict) else dt_to_raw
        ),
    )


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
