"""
Primary plugin dialog.

Layout (top to bottom)
-----------------------
1. Query Parameters group
   - Spatial filter (draw mode selector + draw/use-extent buttons + current filter display)
   - Time range widget
   - Pollutant checkboxes
   - Reference-grade filter
2. Status bar (rate limit widget + request estimate)
3. Progress bar
4. Action buttons (Fetch, Settings)
5. Cache list (previously fetched queries)
6. Layer actions (IDW, Export, unit toggle)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from qgis.core import (
    QgsApplication,
    QgsMapLayerProxyModel,
    QgsProject,
    QgsSingleSymbolRenderer,
    QgsTask,
    QgsVectorLayer,
)
from qgis.gui import QgsMapLayerComboBox

from ..compat.qt import (
    QButtonGroup, QCheckBox, QColor, QDialog, QDialogButtonBox,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QProgressBar, QPushButton, QRadioButton, QScrollArea,
    QSettings, QSizePolicy, QVBoxLayout, QWidget, pyqtSignal,
)
from ..core.cache import Cache
from ..core.client import OpenAQClient, AuthError, OpenAQError
from ..core.models import BboxFilter, CircleFilter, Parameter, QueryParams, SpatialFilter
from ..core.rate_limiter import RateLimiter
from .bbox_tool import BboxTool
from .rate_limit_widget import RateLimitWidget
from .settings_dialog import SettingsDialog
from .time_range_widget import TimeRangeWidget

_SETTINGS_PREFIX = "openaq/"

# Pollutants shown by default in the UI (name must match thresholds.json keys).
_DEFAULT_POLLUTANTS = ["pm25", "pm10", "no2", "o3", "so2", "co"]


class MainDialog(QDialog):
    def __init__(self, iface, parent=None) -> None:
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("OpenAQ Air Quality")
        self.setMinimumWidth(520)

        self._rate_limiter = RateLimiter()
        self._client: Optional[OpenAQClient] = None
        self._cache: Optional[Cache] = None
        self._param_map: Dict[int, Parameter] = {}         # id → Parameter
        self._name_to_ids: Dict[str, List[int]] = {}    # lowercase name → all IDs
        self._current_filter: Optional[SpatialFilter] = None
        self._bbox_tool: Optional[BboxTool] = None
        self._prev_tool = None
        # Keep a Python-level reference to the active task so it is not garbage
        # collected while the C++ task manager is running it (PyQGIS pitfall).
        self._active_task = None

        self._build_ui()
        self._try_init_client()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # --- Query parameters group ---
        query_group = QGroupBox("Query Parameters")
        query_layout = QVBoxLayout(query_group)

        # Spatial filter row
        spatial_row = QHBoxLayout()
        spatial_row.addWidget(QLabel("Mode:"))
        self._mode_group = QButtonGroup(self)
        self._rect_rb = QRadioButton("Rectangle")
        self._circle_rb = QRadioButton("Circle")
        self._rect_rb.setChecked(True)
        self._mode_group.addButton(self._rect_rb)
        self._mode_group.addButton(self._circle_rb)
        spatial_row.addWidget(self._rect_rb)
        spatial_row.addWidget(self._circle_rb)
        self._draw_btn = QPushButton("Draw on Map")
        self._draw_btn.clicked.connect(self._start_drawing)
        self._extent_btn = QPushButton("Use Map Extent")
        self._extent_btn.clicked.connect(self._use_map_extent)
        spatial_row.addWidget(self._draw_btn)
        spatial_row.addWidget(self._extent_btn)
        spatial_row.addStretch()
        query_layout.addLayout(spatial_row)

        self._filter_label = QLabel("No area selected")
        self._filter_label.setStyleSheet("color: grey; font-style: italic;")
        query_layout.addWidget(self._filter_label)

        # Separator
        query_layout.addWidget(_hline())

        # Time range widget
        self._time_widget = TimeRangeWidget()
        query_layout.addWidget(self._time_widget)

        query_layout.addWidget(_hline())

        # Pollutant checkboxes
        poll_row = QHBoxLayout()
        poll_row.addWidget(QLabel("Pollutants:"))
        self._pollutant_checks: Dict[str, QCheckBox] = {}
        for name in _DEFAULT_POLLUTANTS:
            cb = QCheckBox(name.upper().replace("25", "2.5"))
            cb.setProperty("param_name", name)
            cb.setChecked(name in ("pm25", "no2"))
            self._pollutant_checks[name] = cb
            poll_row.addWidget(cb)
        poll_row.addStretch()
        query_layout.addLayout(poll_row)

        # Reference-grade checkbox
        self._monitor_cb = QCheckBox("Reference-grade monitors only")
        query_layout.addWidget(self._monitor_cb)

        root.addWidget(query_group)

        # --- Status row ---
        status_row = QHBoxLayout()
        self._rate_widget = RateLimitWidget(self._rate_limiter)
        status_row.addWidget(self._rate_widget)
        root.addLayout(status_row)

        # --- Progress bar ---
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        # --- Action buttons ---
        btn_row = QHBoxLayout()
        self._fetch_btn = QPushButton("Fetch Data")
        self._fetch_btn.setDefault(True)
        self._fetch_btn.clicked.connect(self._on_fetch)
        self._settings_btn = QPushButton("Settings…")
        self._settings_btn.clicked.connect(self._open_settings)
        btn_row.addWidget(self._fetch_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._settings_btn)
        root.addLayout(btn_row)

        root.addWidget(_hline())

        # --- Cache list ---
        cache_group = QGroupBox("Previously Fetched")
        cache_layout = QVBoxLayout(cache_group)
        self._cache_list = QListWidget()
        self._cache_list.setMaximumHeight(140)
        cache_btn_row = QHBoxLayout()
        load_btn = QPushButton("Load Selected")
        load_btn.clicked.connect(self._load_from_cache)
        delete_btn = QPushButton("Delete Selected")
        delete_btn.clicked.connect(self._delete_from_cache)
        cache_btn_row.addWidget(load_btn)
        cache_btn_row.addWidget(delete_btn)
        cache_btn_row.addStretch()
        cache_layout.addWidget(self._cache_list)
        cache_layout.addLayout(cache_btn_row)
        root.addWidget(cache_group)

        root.addWidget(_hline())

        # --- Layer actions ---
        layer_group = QGroupBox("Layer Actions")
        layer_layout = QVBoxLayout(layer_group)

        layer_select_row = QHBoxLayout()
        layer_select_row.addWidget(QLabel("Active layer:"))
        self._layer_combo = QgsMapLayerComboBox()
        self._layer_combo.setFilters(QgsMapLayerProxyModel.Filter.VectorLayer)
        layer_select_row.addWidget(self._layer_combo)
        layer_layout.addLayout(layer_select_row)

        action_row = QHBoxLayout()
        self._idw_btn = QPushButton("Run IDW Interpolation")
        self._idw_btn.setToolTip(
            "Estimate: IDW fills gaps between stations but does not account for "
            "local pollution sources such as highways or industrial sites."
        )
        self._idw_btn.clicked.connect(self._run_idw)
        action_row.addWidget(self._idw_btn)
        action_row.addStretch()
        layer_layout.addLayout(action_row)

        export_row = QHBoxLayout()
        export_row.addWidget(QLabel("Export:"))
        for fmt in ("GeoPackage", "GeoJSON", "CSV"):
            btn = QPushButton(fmt)
            btn.clicked.connect(lambda _checked, f=fmt: self._export(f))
            export_row.addWidget(btn)
        export_row.addStretch()
        layer_layout.addLayout(export_row)

        root.addWidget(layer_group)

    # ------------------------------------------------------------------
    # Client / cache initialisation
    # ------------------------------------------------------------------

    def _try_init_client(self) -> None:
        api_key = SettingsDialog.saved_api_key()
        if not api_key:
            return
        self._client = OpenAQClient(api_key, self._rate_limiter)
        cache_dir = QSettings().value(
            _SETTINGS_PREFIX + "cache_dir",
            str(Path.home() / ".openaq_cache"),
        )
        self._cache = Cache(cache_dir)
        self._load_parameters()
        self._refresh_cache_list()

    def _load_parameters(self) -> None:
        if not self._client:
            return
        try:
            params = self._client.get_parameters()
            self._param_map = {p.id: p for p in params}
            # Collect ALL IDs per name — OpenAQ v3 has many variants (different
            # units, manufacturer-specific subtypes) sharing the same base name.
            # Sending all of them ensures we don't miss stations.
            self._name_to_ids = {}
            for p in params:
                self._name_to_ids.setdefault(p.name.lower(), []).append(p.id)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "OpenAQ",
                f"Could not load pollutant list from OpenAQ:\n{exc}\n\n"
                "Check your API key in Settings.",
            )

    # ------------------------------------------------------------------
    # Spatial filter
    # ------------------------------------------------------------------

    def _start_drawing(self) -> None:
        canvas = self.iface.mapCanvas()
        circular = self._circle_rb.isChecked()
        if self._bbox_tool is None:
            self._bbox_tool = BboxTool(canvas, circular=circular)
            self._bbox_tool.geometry_selected.connect(self._on_filter_drawn)
        else:
            self._bbox_tool.set_circular(circular)

        self._prev_tool = canvas.mapTool()
        canvas.setMapTool(self._bbox_tool)
        self.hide()  # step aside so the canvas is accessible

    def _on_filter_drawn(self, spatial_filter: SpatialFilter) -> None:
        self._current_filter = spatial_filter
        self._filter_label.setText(spatial_filter.description())
        self._filter_label.setStyleSheet("")
        # Restore previous tool and show dialog again.
        if self._prev_tool:
            self.iface.mapCanvas().setMapTool(self._prev_tool)
        self.show()
        self.raise_()

    def _use_map_extent(self) -> None:
        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform
        canvas = self.iface.mapCanvas()
        extent = canvas.extent()
        src_crs = canvas.mapSettings().destinationCrs()
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = QgsCoordinateTransform(src_crs, wgs84, QgsProject.instance())
        rect = transform.transformBoundingBox(extent)
        self._current_filter = BboxFilter(
            rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum()
        )
        self._filter_label.setText(self._current_filter.description())
        self._filter_label.setStyleSheet("")

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    def _on_fetch(self) -> None:
        if not self._ensure_client():
            return
        if not self._current_filter:
            QMessageBox.warning(self, "No Area", "Please draw an area or use the map extent.")
            return

        param_ids = self._selected_parameter_ids()
        if not param_ids:
            QMessageBox.warning(self, "No Pollutants", "Please select at least one pollutant.")
            return

        query = QueryParams(
            spatial_filter=self._current_filter,
            datetime_from=self._time_widget.datetime_from(),
            datetime_to=self._time_widget.datetime_to(),
            parameter_ids=param_ids,
            granularity=self._time_widget.granularity(),
            monitor_only=self._monitor_cb.isChecked(),
        )

        # One display name per checked pollutant (not one per ID variant).
        param_names = [
            self._param_map[self._name_to_ids[name][0]].display_name
            for name, cb in self._pollutant_checks.items()
            if cb.isChecked() and self._name_to_ids.get(name)
        ]
        label = query.human_label(param_names)

        from ..tasks.fetch_task import FetchTask
        task = FetchTask(
            query=query,
            client=self._client,
            cache=self._cache,
            parameter_map=self._param_map,
            layer_label=label,
            on_layer_ready=self._on_layer_ready,
        )
        task.finished_message.connect(self._on_task_finished)
        task.progressChanged.connect(self._on_progress)

        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._fetch_btn.setEnabled(False)

        # Hold a Python reference so the task is not garbage-collected while
        # the C++ task manager is still running it (common PyQGIS pitfall).
        self._active_task = task
        QgsApplication.taskManager().addTask(task)

    def _on_task_finished(self, message: str) -> None:
        self._active_task = None
        self._progress.setVisible(False)
        self._fetch_btn.setEnabled(True)
        self._refresh_cache_list()
        is_error = any(w in message.lower() for w in ("failed", "error", "internal"))
        if is_error:
            QMessageBox.warning(self, "OpenAQ", message)
        else:
            # Use the QGIS message bar so the result is always visible without
            # interrupting the workflow with a modal dialog.
            from qgis.core import Qgis
            self.iface.messageBar().pushMessage(
                "OpenAQ", message, Qgis.MessageLevel.Info, 5
            )

    def _on_progress(self, progress: float) -> None:
        self._progress.setValue(int(progress))

    def _on_layer_ready(self, layer: QgsVectorLayer) -> None:
        self._layer_combo.setLayer(layer)

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _refresh_cache_list(self) -> None:
        self._cache_list.clear()
        if not self._cache:
            return
        for entry in sorted(self._cache.list_entries(), key=lambda e: e["cached_at"], reverse=True):
            item = QListWidgetItem(
                f"{entry['label']}  ({entry['feature_count']} features)"
            )
            item.setData(256, entry["key"])   # Qt.UserRole = 256
            self._cache_list.addItem(item)

    def _load_from_cache(self) -> None:
        item = self._cache_list.currentItem()
        if not item or not self._cache:
            return
        key = item.data(256)
        path = self._cache.get(key)
        if not path:
            QMessageBox.warning(self, "Cache", "Cached file not found.")
            self._refresh_cache_list()
            return
        label = item.text().split("  (")[0]
        layer = QgsVectorLayer(str(path), label, "ogr")
        if layer.isValid():
            from ..tasks.fetch_task import _apply_temporal_properties, _apply_styling
            _apply_temporal_properties(layer)
            _apply_styling(layer, [], self._param_map)
            QgsProject.instance().addMapLayer(layer)
            self._layer_combo.setLayer(layer)
        else:
            QMessageBox.warning(self, "Cache", "Could not load layer from cached file.")

    def _delete_from_cache(self) -> None:
        item = self._cache_list.currentItem()
        if not item or not self._cache:
            return
        self._cache.delete(item.data(256))
        self._refresh_cache_list()

    # ------------------------------------------------------------------
    # Layer actions
    # ------------------------------------------------------------------

    def _run_idw(self) -> None:
        layer = self._layer_combo.currentLayer()
        if not isinstance(layer, QgsVectorLayer):
            QMessageBox.warning(self, "IDW", "Please select a point vector layer.")
            return
        from ..tasks.interpolation import run_idw
        result = run_idw(layer, value_field="value")
        if result is None:
            QMessageBox.warning(self, "IDW", "Interpolation failed or returned no output.")

    def _export(self, fmt: str) -> None:
        from ..compat.qt import QApplication
        layer = self._layer_combo.currentLayer()
        if not isinstance(layer, QgsVectorLayer):
            QMessageBox.warning(self, "Export", "Please select a layer to export.")
            return

        from qgis.core import QgsVectorFileWriter
        ext_map = {"GeoPackage": "gpkg", "GeoJSON": "geojson", "CSV": "csv"}
        driver_map = {"GeoPackage": "GPKG", "GeoJSON": "GeoJSON", "CSV": "CSV"}
        ext = ext_map[fmt]
        driver = driver_map[fmt]

        from ..compat.qt import QFileDialog

        path, _ = QFileDialog.getSaveFileName(
            self, f"Export as {fmt}", "", f"{fmt} (*.{ext})"
        )
        if not path:
            return

        error = QgsVectorFileWriter.writeAsVectorFormat(
            layer, path, "UTF-8", layer.crs(), driver
        )
        if error[0] == QgsVectorFileWriter.WriterError.NoError:
            QMessageBox.information(self, "Export", f"Exported to {path}")
        else:
            QMessageBox.warning(self, "Export", f"Export failed: {error[1]}")

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._try_init_client()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _selected_parameter_ids(self) -> List[int]:
        seen: set = set()
        ids: List[int] = []
        for name, cb in self._pollutant_checks.items():
            if cb.isChecked():
                for pid in self._name_to_ids.get(name, []):
                    if pid not in seen:
                        seen.add(pid)
                        ids.append(pid)
        return ids

    def _ensure_client(self) -> bool:
        if self._client:
            return True
        QMessageBox.warning(
            self,
            "API Key Required",
            "Please enter your OpenAQ API key in Settings before fetching data.",
        )
        self._open_settings()
        return self._client is not None


def _hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line
