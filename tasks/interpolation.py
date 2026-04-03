"""
IDW interpolation task.

Wraps QGIS's built-in qgis:idwinterpolation processing algorithm so the caller
doesn't need to deal with the parameter encoding directly.

The result is a GeoTIFF raster added to the current QGIS project.

IMPORTANT: This is an *estimate*.  IDW interpolation fills spatial gaps between
monitoring stations using inverse distance weighting and does not account for
local pollution sources (highways, industrial sites, etc.) or atmospheric
dispersion.  Treat the output as a rough spatial overview, not a measurement.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from qgis.core import (
    Qgis,
    QgsColorRampShader,
    QgsMessageLog,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProject,
    QgsRasterLayer,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
    QgsVectorLayer,
)

from ..compat.qt import QColor

import processing

_THRESHOLDS_PATH = Path(__file__).parent.parent / "resources" / "thresholds.json"


def run_idw(
    point_layer: QgsVectorLayer,
    value_field: str,
    pixel_size: float = 0.01,
    distance_coefficient: float = 2.0,
    output_path: str | None = None,
    layer_label: str | None = None,
    add_to_project: bool = True,
    mask_layer: QgsVectorLayer | None = None,
) -> QgsRasterLayer | None:
    """Run IDW interpolation and return the resulting raster layer.

    Parameters
    ----------
    point_layer:
        The source point layer (measurements).
    value_field:
        Name of the numeric field to interpolate (e.g. "value").
    pixel_size:
        Output raster pixel size in layer CRS units.  0.01 degrees ≈ 1 km at
        mid-latitudes — a reasonable default for a city-scale view.
    distance_coefficient:
        The IDW exponent (P).  Higher values give more weight to nearby points.
        Default 2.0 is standard.
    output_path:
        Optional path for the output GeoTIFF.  If None, a temporary file is
        created in the system temp directory.

    Returns
    -------
    QgsRasterLayer if successful, None otherwise.
    """
    if not point_layer or not point_layer.isValid():
        QgsMessageLog.logMessage("IDW: layer is None or invalid.", "OpenAQ", Qgis.MessageLevel.Warning)
        return None

    field_index = point_layer.fields().indexFromName(value_field)
    if field_index < 0:
        QgsMessageLog.logMessage(
            f"IDW: field '{value_field}' not found in layer. "
            f"Available fields: {[f.name() for f in point_layer.fields()]}",
            "OpenAQ", Qgis.MessageLevel.Warning,
        )
        return None

    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".tif", prefix="openaq_idw_")
        os.close(fd)

    # INTERPOLATION_DATA format (from IdwInterpolation.py source):
    #   Multiple layers separated by  ::|::
    #   Within each layer, fields separated by  ::~::
    #   Field order: source ::~:: valueSource ::~:: attributeIndex ::~:: sourceType
    #     valueSource  : 0 = Attribute (use field value), 1 = Z coordinate
    #     attributeIndex: 0-based field index to interpolate (-1 if using Z)
    #     sourceType   : 0 = Points, 1 = Structure Lines, 2 = Break Lines
    interp_data = f"{point_layer.source()}::~::0::~::{field_index}::~::0"

    context = QgsProcessingContext()
    context.setProject(QgsProject.instance())
    feedback = QgsProcessingFeedback()

    params = {
        "INTERPOLATION_DATA": interp_data,
        "DISTANCE_COEFFICIENT": distance_coefficient,
        "EXTENT": point_layer.extent(),
        "PIXEL_SIZE": pixel_size,
        "OUTPUT": output_path,
    }

    QgsMessageLog.logMessage(
        f"IDW params: {params}", "OpenAQ", Qgis.MessageLevel.Info
    )

    try:
        result = processing.run(
            "qgis:idwinterpolation", params, context=context, feedback=feedback
        )
    except Exception as exc:
        import traceback
        QgsMessageLog.logMessage(
            f"IDW processing.run failed:\n{traceback.format_exc()}",
            "OpenAQ", Qgis.MessageLevel.Critical,
        )
        return None

    raster_path = result.get("OUTPUT", output_path)

    # Optionally clip to a mask polygon (e.g. a circle drawn by the user).
    if mask_layer is not None and mask_layer.isValid():
        fd, clipped_path = tempfile.mkstemp(suffix=".tif", prefix="openaq_idw_clipped_")
        os.close(fd)
        clip_params = {
            "INPUT": raster_path,
            "MASK": mask_layer,
            "NODATA": None,
            "ALPHA_BAND": False,
            "CROP_TO_CUTLINE": True,
            "KEEP_RESOLUTION": True,
            "OUTPUT": clipped_path,
        }
        try:
            clip_result = processing.run(
                "gdal:cliprasterbymasklayer", clip_params,
                context=context, feedback=feedback,
            )
            raster_path = clip_result.get("OUTPUT", clipped_path)
        except Exception:
            import traceback
            QgsMessageLog.logMessage(
                f"IDW clip failed (using unclipped raster):\n{traceback.format_exc()}",
                "OpenAQ", Qgis.MessageLevel.Warning,
            )

    label = layer_label if layer_label is not None else f"IDW – {value_field}"
    layer = QgsRasterLayer(raster_path, label)
    if not layer.isValid():
        QgsMessageLog.logMessage(
            f"IDW: raster layer at '{raster_path}' is not valid.", "OpenAQ", Qgis.MessageLevel.Warning
        )
        return None

    # Detect parameter name from first feature of the source layer.
    param_name = None
    for feature in point_layer.getFeatures():
        param_name = feature["parameter_name"]
        break

    _apply_raster_color_ramp(layer, param_name)
    if add_to_project:
        QgsProject.instance().addMapLayer(layer)
    return layer


def run_idw_temporal(
    point_layer: QgsVectorLayer,
    value_field: str = "value",
    pixel_size: float = 0.01,
    distance_coefficient: float = 2.0,
    mask_layer: QgsVectorLayer | None = None,
) -> list:
    """Run IDW for every distinct time step in *point_layer*.

    Creates one raster per unique ``datetime_from`` value and sets
    ``QgsRasterLayerTemporalProperties`` on each so the QGIS temporal
    controller can animate through them.  All rasters are collected into a
    layer-tree group named after the source layer.

    Returns the list of created ``QgsRasterLayer`` objects (may be empty on
    failure).
    """
    from qgis.core import QgsVectorFileWriter

    if not point_layer or not point_layer.isValid():
        QgsMessageLog.logMessage(
            "IDW temporal: layer is None or invalid.", "OpenAQ", Qgis.MessageLevel.Warning
        )
        return []

    # ------------------------------------------------------------------
    # Group features by datetime_from.
    # OGR may return datetime field values as QDateTime objects or as ISO
    # strings depending on the detected field type.  _field_to_qdt handles
    # both cases and always returns a QDateTime in UTC epoch seconds.
    # We key the groups by epoch-second integer so sorting is unambiguous.
    # ------------------------------------------------------------------
    time_groups: dict = {}   # epoch_int → [QgsFeature, ...]
    dt_ranges: dict = {}     # epoch_int → (dt_from QDateTime, dt_to QDateTime)

    for feature in point_layer.getFeatures():
        dt_from_val = feature["datetime_from"]
        if dt_from_val is None:
            continue
        dt_from_qdt = _field_to_qdt(dt_from_val)
        if not dt_from_qdt.isValid():
            continue
        key = dt_from_qdt.toSecsSinceEpoch()
        if key not in time_groups:
            time_groups[key] = []
            dt_to_val = feature["datetime_to"]
            dt_to_qdt = _field_to_qdt(dt_to_val) if dt_to_val is not None else dt_from_qdt
            dt_ranges[key] = (dt_from_qdt, dt_to_qdt)
        time_groups[key].append(feature)

    if not time_groups:
        QgsMessageLog.logMessage(
            "IDW temporal: no valid datetime_from values found in layer.",
            "OpenAQ", Qgis.MessageLevel.Warning,
        )
        return []

    QgsMessageLog.logMessage(
        f"IDW temporal: {len(time_groups)} time step(s) found.",
        "OpenAQ", Qgis.MessageLevel.Info,
    )

    # ------------------------------------------------------------------
    # Create a layer-tree group to hold all rasters
    # ------------------------------------------------------------------
    group_name = f"IDW – {point_layer.name()}"
    root = QgsProject.instance().layerTreeRoot()
    group = root.insertGroup(0, group_name)

    rasters = []
    crs = point_layer.crs()

    for key, features in sorted(time_groups.items()):
        dt_from_qdt, dt_to_qdt = dt_ranges[key]

        # Write the time-step subset to a temporary GeoJSON so that the OGR
        # provider used by the IDW algorithm can open it as a real file.
        fd, temp_path = tempfile.mkstemp(suffix=".geojson", prefix="openaq_ts_")
        os.close(fd)

        mem_layer = QgsVectorLayer(
            f"Point?crs={crs.authid()}", "ts_subset", "memory"
        )
        dp = mem_layer.dataProvider()
        dp.addAttributes(point_layer.fields().toList())
        mem_layer.updateFields()
        dp.addFeatures(features)

        write_result = QgsVectorFileWriter.writeAsVectorFormat(
            mem_layer, temp_path, "UTF-8", crs, "GeoJSON"
        )
        err_code = write_result[0] if isinstance(write_result, tuple) else write_result
        if err_code != QgsVectorFileWriter.WriterError.NoError:
            QgsMessageLog.logMessage(
                f"IDW temporal: could not write temp file for epoch {key}.",
                "OpenAQ", Qgis.MessageLevel.Warning,
            )
            continue

        file_layer = QgsVectorLayer(temp_path, "ts", "ogr")
        if not file_layer.isValid():
            QgsMessageLog.logMessage(
                f"IDW temporal: temp layer invalid for epoch {key}.",
                "OpenAQ", Qgis.MessageLevel.Warning,
            )
            continue

        short_dt = dt_from_qdt.toString("yyyy-MM-dd HH:mm")
        raster = run_idw(
            file_layer,
            value_field,
            pixel_size,
            distance_coefficient,
            layer_label=f"IDW {short_dt}",
            add_to_project=False,
            mask_layer=mask_layer,
        )
        if raster is None:
            continue

        _apply_raster_temporal(raster, dt_from_qdt, dt_to_qdt)
        QgsProject.instance().addMapLayer(raster, False)
        group.addLayer(raster)
        rasters.append(raster)

    QgsMessageLog.logMessage(
        f"IDW temporal: created {len(rasters)} raster(s).",
        "OpenAQ", Qgis.MessageLevel.Info,
    )
    return rasters


def _field_to_qdt(val) -> "QDateTime":
    """Convert a QgsFeature datetime field value to QDateTime.

    OGR may return the value as a QDateTime (when it detects the field type
    as DateTime) or as an ISO 8601 string.  Both cases are handled here.
    """
    from datetime import datetime, timezone
    from ..compat.qt import QDateTime

    if val is None:
        return QDateTime()
    # Already a QDateTime — returned by OGR for detected DateTime fields.
    if isinstance(val, QDateTime):
        return val
    # String — parse as ISO 8601 and convert via epoch seconds so the result
    # is unambiguously in UTC.
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return QDateTime.fromSecsSinceEpoch(int(dt.timestamp()))
    except Exception:
        return QDateTime()


def _apply_raster_temporal(
    layer: QgsRasterLayer, dt_from: "QDateTime", dt_to: "QDateTime"
) -> None:
    """Set a fixed temporal range on *layer* so the temporal controller works."""
    try:
        from qgis.core import QgsDateTimeRange, QgsRasterLayerTemporalProperties
    except ImportError:
        QgsMessageLog.logMessage(
            "IDW temporal: QgsRasterLayerTemporalProperties not available.",
            "OpenAQ", Qgis.MessageLevel.Warning,
        )
        return

    props = layer.temporalProperties()
    props.setIsActive(True)

    # QGIS 3: enum members are direct class attributes.
    # QGIS 4 / PyQt6 scoped enums: they live under TemporalMode.
    mode = (
        getattr(QgsRasterLayerTemporalProperties, "ModeFixedTemporalRange", None)
        or getattr(
            getattr(QgsRasterLayerTemporalProperties, "TemporalMode", None),
            "ModeFixedTemporalRange", None,
        )
    )
    if mode is None:
        QgsMessageLog.logMessage(
            "IDW temporal: could not resolve ModeFixedTemporalRange enum.",
            "OpenAQ", Qgis.MessageLevel.Warning,
        )
        return

    props.setMode(mode)
    time_range = QgsDateTimeRange(dt_from, dt_to)
    props.setFixedTemporalRange(time_range)
    QgsMessageLog.logMessage(
        f"IDW temporal: range set {dt_from.toString('yyyy-MM-dd HH:mm')} "
        f"→ {dt_to.toString('yyyy-MM-dd HH:mm')}",
        "OpenAQ", Qgis.MessageLevel.Info,
    )


def _apply_raster_color_ramp(layer: QgsRasterLayer, parameter_name: str | None) -> None:
    """Apply a WHO-threshold colour ramp to a single-band raster layer."""
    color_items = _color_items_for_parameter(parameter_name)

    shader_fn = QgsColorRampShader()
    shader_fn.setColorRampType(QgsColorRampShader.Type.Interpolated)
    shader_fn.setColorRampItemList(color_items)

    raster_shader = QgsRasterShader()
    raster_shader.setRasterShaderFunction(shader_fn)

    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, raster_shader)
    layer.setRenderer(renderer)
    layer.triggerRepaint()

    # Replace the default legend with a filtered one that omits "Band 1 (Gray)".
    _apply_filtered_legend(layer)


def _apply_filtered_legend(layer: QgsRasterLayer) -> None:
    """Set a custom QgsMapLayerLegend that drops any node labelled '(gray)'."""
    try:
        from qgis.core import QgsMapLayerLegend

        inner = QgsMapLayerLegend.defaultRasterLegend(layer)

        class _NoGrayLegend(QgsMapLayerLegend):
            def createLayerTreeModelLegendNodes(self, node_layer):
                return [
                    n for n in inner.createLayerTreeModelLegendNodes(node_layer)
                    if "gray" not in str(n.data(0) or "").lower()
                ]

        layer.setLegend(_NoGrayLegend())
    except Exception:
        import traceback
        QgsMessageLog.logMessage(
            f"IDW: could not apply filtered legend:\n{traceback.format_exc()}",
            "OpenAQ", Qgis.MessageLevel.Warning,
        )


def _color_items_for_parameter(
    parameter_name: str | None,
) -> list:
    """Return QgsColorRampShader.ColorRampItem list for the given parameter.

    Falls back to a simple green→yellow→red ramp when the parameter is unknown.
    """
    if parameter_name and _THRESHOLDS_PATH.exists():
        try:
            thresholds = json.loads(_THRESHOLDS_PATH.read_text(encoding="utf-8"))
            key = parameter_name.lower().replace(".", "")
            bp_list = thresholds.get("parameters", {}).get(key, {}).get("breakpoints")
            if bp_list:
                items = []
                lower = 0.0
                for bp in bp_list:
                    upper = bp["max"] if bp["max"] < 1e8 else lower * 3 or 200.0
                    mid = (lower + upper) / 2
                    items.append(
                        QgsColorRampShader.ColorRampItem(mid, QColor(bp["color"]), bp["label"])
                    )
                    lower = upper
                return items
        except Exception:
            pass

    # Generic green → yellow → red fallback.
    return [
        QgsColorRampShader.ColorRampItem(0,   QColor("#00e400"), "Low"),
        QgsColorRampShader.ColorRampItem(50,  QColor("#ffff00"), "Moderate"),
        QgsColorRampShader.ColorRampItem(100, QColor("#ff7e00"), "High"),
        QgsColorRampShader.ColorRampItem(150, QColor("#ff0000"), "Very High"),
        QgsColorRampShader.ColorRampItem(200, QColor("#8f3f97"), "Hazardous"),
    ]
