"""
Background fetch task.

Runs in a QgsTask worker thread:
  1. Fetches matching locations from the OpenAQ API.
  2. For each sensor that matches the requested parameters, fetches measurements.
  3. Assembles a list of Measurement objects with location metadata attached.

On completion (main thread):
  - Stores results in the local cache.
  - Creates a QgsVectorLayer (memory provider) and adds it to the project.
  - Applies graduated styling from thresholds.json.
  - Sets QgsVectorLayerTemporalProperties so the QGIS temporal controller works.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional

from qgis.core import (
    Qgis,
    QgsGraduatedSymbolRenderer,
    QgsMarkerSymbol,
    QgsMessageLog,
    QgsProject,
    QgsRendererRange,
    QgsTask,
    QgsVectorLayer,
    QgsVectorLayerTemporalProperties,
)

from ..compat.qt import QColor, QDateTime, pyqtSignal
from ..core.cache import Cache
from ..core.client import OpenAQClient
from ..core.models import Measurement, Parameter, QueryParams


_THRESHOLDS_PATH = Path(__file__).parent.parent / "resources" / "thresholds.json"


class FetchTask(QgsTask):
    """Fetches OpenAQ data for a QueryParams and creates a styled vector layer."""

    # Emitted on the main thread with a message string (success or error).
    finished_message = pyqtSignal(str)

    def __init__(
        self,
        query: QueryParams,
        client: OpenAQClient,
        cache: Cache,
        parameter_map: Dict[int, Parameter],
        layer_label: str,
        on_layer_ready: Optional[Callable[[QgsVectorLayer], None]] = None,
    ) -> None:
        super().__init__(f"OpenAQ fetch: {layer_label}", QgsTask.Flag.CanCancel)
        self._query = query
        self._client = client
        self._cache = cache
        self._param_map = parameter_map
        self._label = layer_label
        self._on_layer_ready = on_layer_ready
        self._measurements: List[Measurement] = []
        self._error: Optional[str] = None

    # ------------------------------------------------------------------
    # QgsTask interface
    # ------------------------------------------------------------------

    def run(self) -> bool:
        """Executed in the background thread."""
        try:
            self._measurements = self._fetch()
            return True
        except Exception as exc:
            self._error = str(exc)
            return False

    def finished(self, result: bool) -> None:
        """Executed on the main thread after run() completes."""
        try:
            self._do_finished(result)
        except Exception as exc:
            import traceback
            msg = traceback.format_exc()
            QgsMessageLog.logMessage(msg, "OpenAQ", Qgis.MessageLevel.Critical)
            self.finished_message.emit(f"Internal error in finished(): {exc}")

    def _do_finished(self, result: bool) -> None:
        if not result:
            self.finished_message.emit(f"Fetch failed: {self._error}")
            return

        if not self._measurements:
            self.finished_message.emit("No measurements found for the selected query.")
            return

        QgsMessageLog.logMessage(
            f"Fetched {len(self._measurements)} measurements, writing to cache.",
            "OpenAQ", Qgis.MessageLevel.Info,
        )

        # Write to cache.
        key = self._query.cache_key()
        cache_path = self._cache.put(key, self._measurements, label=self._label)

        # Build vector layer from cached GeoJSON (OGR provider reads it directly).
        layer = QgsVectorLayer(str(cache_path), self._label, "ogr")
        if not layer.isValid():
            self.finished_message.emit(f"Layer creation failed (path: {cache_path}).")
            return

        _apply_temporal_properties(layer)
        _apply_styling(layer, self._query.parameter_ids, self._param_map)

        QgsProject.instance().addMapLayer(layer)

        if self._on_layer_ready:
            self._on_layer_ready(layer)

        self.finished_message.emit(
            f"Loaded {len(self._measurements)} measurements as '{self._label}'."
        )

    # ------------------------------------------------------------------
    # Internal fetch logic
    # ------------------------------------------------------------------

    def _fetch(self) -> List[Measurement]:
        # Step 1: Locations.
        self.setProgress(0)
        locations = self._client.get_locations(
            self._query.spatial_filter,
            self._query.parameter_ids,
            self._query.monitor_only,
        )
        if self.isCanceled():
            return []

        total_sensors = sum(len(loc.sensors) for loc in locations)
        QgsMessageLog.logMessage(
            f"Found {len(locations)} locations, {total_sensors} matching sensors. "
            f"Spatial filter: {self._query.spatial_filter.to_api_params()} "
            f"Parameter IDs: {self._query.parameter_ids}",
            "OpenAQ", Qgis.MessageLevel.Info,
        )
        if total_sensors == 0:
            return []

        # Step 2: Measurements per sensor.
        all_measurements: List[Measurement] = []
        done = 0
        for loc in locations:
            for sensor in loc.sensors:
                if self.isCanceled():
                    return all_measurements

                measurements = self._client.get_measurements(
                    sensor.id,
                    self._query.datetime_from,
                    self._query.datetime_to,
                    self._query.granularity,
                )
                QgsMessageLog.logMessage(
                    f"Sensor {sensor.id} ({sensor.parameter.name}): "
                    f"{len(measurements)} measurements "
                    f"[{self._query.datetime_from.isoformat()} → {self._query.datetime_to.isoformat()}]",
                    "OpenAQ", Qgis.MessageLevel.Info,
                )

                for m in measurements:
                    # Inherit location coordinates if the measurement has none.
                    if m.lon is None:
                        m.lon = loc.lon
                    if m.lat is None:
                        m.lat = loc.lat
                    m.location_id = loc.id
                    m.location_name = loc.name

                all_measurements.extend(measurements)
                done += 1
                self.setProgress(int(done / total_sensors * 100))

        return all_measurements


# ------------------------------------------------------------------
# Layer helpers
# ------------------------------------------------------------------

def _apply_temporal_properties(layer: QgsVectorLayer) -> None:
    props = layer.temporalProperties()
    props.setIsActive(True)
    # The enum member is ModeFeatureDateTimeStartAndEndFromFields.
    # In QGIS 4 / PyQt6 scoped enums it lives under TemporalMode;
    # in QGIS 3 it is a direct attribute of the class.
    mode = getattr(
        QgsVectorLayerTemporalProperties,
        "ModeFeatureDateTimeStartAndEndFromFields",
        None,
    )
    if mode is None:
        mode = QgsVectorLayerTemporalProperties.TemporalMode.ModeFeatureDateTimeStartAndEndFromFields
    props.setMode(mode)
    props.setStartField("datetime_from")
    props.setEndField("datetime_to")


def _apply_styling(
    layer: QgsVectorLayer,
    parameter_ids: List[int],
    param_map: Dict[int, Parameter],
) -> None:
    """Apply a graduated renderer for the first matching parameter in thresholds.json."""
    if not _THRESHOLDS_PATH.exists():
        return

    with open(_THRESHOLDS_PATH, encoding="utf-8") as fh:
        thresholds = json.load(fh)

    # Find the first requested parameter that has threshold definitions.
    param_key = None
    for pid in parameter_ids:
        if pid in param_map:
            name = param_map[pid].name.lower().replace(".", "")
            if name in thresholds.get("parameters", {}):
                param_key = name
                break

    if not param_key:
        return

    breakpoints = thresholds["parameters"][param_key]["breakpoints"]
    ranges = []
    lower = 0.0
    for bp in breakpoints:
        upper = bp["max"]
        color = QColor(bp["color"])
        symbol = QgsMarkerSymbol.createSimple({"color": bp["color"], "size": "4"})
        ranges.append(QgsRendererRange(lower, upper, symbol, bp["label"]))
        lower = upper

    renderer = QgsGraduatedSymbolRenderer("value", ranges)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
