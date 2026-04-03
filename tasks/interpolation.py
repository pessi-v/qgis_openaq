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

import os
import tempfile

from qgis.core import (
    Qgis,
    QgsMessageLog,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)

import processing


def run_idw(
    point_layer: QgsVectorLayer,
    value_field: str,
    pixel_size: float = 0.01,
    distance_coefficient: float = 2.0,
    output_path: str | None = None,
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
    layer = QgsRasterLayer(raster_path, f"IDW – {value_field}")
    if layer.isValid():
        QgsProject.instance().addMapLayer(layer)
        return layer

    QgsMessageLog.logMessage(
        f"IDW: raster layer at '{raster_path}' is not valid.", "OpenAQ", Qgis.MessageLevel.Warning
    )
    return None
