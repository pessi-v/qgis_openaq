"""
QgsMapTool for drawing rectangular or circular spatial filters on the map canvas.

Rectangular mode
----------------
Click and drag to define the bounding box corners.  On mouse release a
BboxFilter is emitted via the ``geometry_selected`` signal.

Circular mode
-------------
Click to set the centre, drag to set the radius.  The radius is computed as
the Euclidean distance between the centre and the release point in CRS units,
then converted to metres using the layer's map units.  A CircleFilter is
emitted on mouse release.

In both modes a QgsRubberBand provides live visual feedback.
"""
from __future__ import annotations

import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsPointXY,
    QgsProject,
    QgsWkbTypes,
)
from qgis.gui import QgsMapCanvas, QgsMapTool, QgsRubberBand

from ..compat.qt import QColor, QCursor, Qt, pyqtSignal

_RUBBER_COLOR = QColor(255, 100, 0, 120)
_CIRCLE_SEGMENTS = 64


class BboxTool(QgsMapTool):
    """Map tool that emits a spatial filter on completion."""

    # Emits either a BboxFilter or CircleFilter instance.
    geometry_selected = pyqtSignal(object)

    def __init__(self, canvas: QgsMapCanvas, circular: bool = False) -> None:
        super().__init__(canvas)
        self._circular = circular
        self._start: QgsPointXY | None = None
        self._rubber = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
        self._rubber.setColor(_RUBBER_COLOR)
        self._rubber.setWidth(2)

    def set_circular(self, circular: bool) -> None:
        self._circular = circular
        self._reset()

    # ------------------------------------------------------------------
    # QgsMapTool event handlers
    # ------------------------------------------------------------------

    def canvasPressEvent(self, event) -> None:
        self._start = self.toMapCoordinates(event.pos())
        self._rubber.reset(QgsWkbTypes.GeometryType.PolygonGeometry)  # clear previous selection on new draw

    def canvasMoveEvent(self, event) -> None:
        if self._start is None:
            return
        current = self.toMapCoordinates(event.pos())
        if self._circular:
            self._draw_circle(self._start, current)
        else:
            self._draw_rect(self._start, current)

    def canvasReleaseEvent(self, event) -> None:
        if self._start is None:
            return
        end = self.toMapCoordinates(event.pos())
        if self._circular:
            self._emit_circle(self._start, end)
        else:
            self._emit_bbox(self._start, end)
        self._start = None

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._reset()
            self.canvas().unsetMapTool(self)
        else:
            super().keyPressEvent(event)

    def deactivate(self) -> None:
        self._reset()
        super().deactivate()

    # ------------------------------------------------------------------
    # Drawing helpers
    # ------------------------------------------------------------------

    def _draw_rect(self, p1: QgsPointXY, p2: QgsPointXY) -> None:
        self._rubber.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        corners = [
            QgsPointXY(p1.x(), p1.y()),
            QgsPointXY(p2.x(), p1.y()),
            QgsPointXY(p2.x(), p2.y()),
            QgsPointXY(p1.x(), p2.y()),
        ]
        for pt in corners:
            self._rubber.addPoint(pt, False)
        self._rubber.addPoint(corners[0], True)  # close ring

    def _draw_circle(self, centre: QgsPointXY, edge: QgsPointXY) -> None:
        radius = math.hypot(edge.x() - centre.x(), edge.y() - centre.y())
        self._rubber.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        for i in range(_CIRCLE_SEGMENTS):
            angle = 2 * math.pi * i / _CIRCLE_SEGMENTS
            pt = QgsPointXY(
                centre.x() + radius * math.cos(angle),
                centre.y() + radius * math.sin(angle),
            )
            self._rubber.addPoint(pt, i == _CIRCLE_SEGMENTS - 1)

    # ------------------------------------------------------------------
    # Emit helpers
    # ------------------------------------------------------------------

    def _emit_bbox(self, p1: QgsPointXY, p2: QgsPointXY) -> None:
        from ..core.models import BboxFilter

        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = QgsCoordinateTransform(
            self.canvas().mapSettings().destinationCrs(), wgs84, QgsProject.instance()
        )
        pt1 = transform.transform(p1)
        pt2 = transform.transform(p2)

        # Normalise so min < max regardless of draw direction.
        min_lon = min(pt1.x(), pt2.x())
        max_lon = max(pt1.x(), pt2.x())
        min_lat = min(pt1.y(), pt2.y())
        max_lat = max(pt1.y(), pt2.y())

        self.geometry_selected.emit(BboxFilter(min_lon, min_lat, max_lon, max_lat))

    def _emit_circle(self, centre: QgsPointXY, edge: QgsPointXY) -> None:
        from ..core.models import CircleFilter

        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = QgsCoordinateTransform(
            self.canvas().mapSettings().destinationCrs(), wgs84, QgsProject.instance()
        )
        centre_wgs = transform.transform(centre)

        # Compute radius in metres using QGIS's geodesic distance calculator.
        edge_wgs = transform.transform(edge)
        da = QgsDistanceArea()
        da.setSourceCrs(wgs84, QgsProject.instance().transformContext())
        da.setEllipsoid("WGS84")
        radius_m = int(da.measureLine(centre_wgs, edge_wgs))

        # Clamp to API limits (1–25 000 m).
        radius_m = max(1, min(25000, radius_m))

        self.geometry_selected.emit(
            CircleFilter(lon=centre_wgs.x(), lat=centre_wgs.y(), radius_m=radius_m)
        )

    def _reset(self) -> None:
        self._start = None
        self._rubber.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
