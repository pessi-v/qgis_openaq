# OpenAQ Air Quality — QGIS Plugin

A QGIS plugin for querying, caching, and visualizing air quality data from the [OpenAQ v3 API](https://api.openaq.org/).

## Features

- **Spatial queries** — draw a bounding box on the map or use the current map extent
- **Pollutant selection** — PM2.5, PM10, NO₂, O₃, SO₂, CO
- **Time range & granularity** — raw, hourly, or daily aggregates with quick presets (last 24 h, 7 days, 30 days)
- **Reference-grade filter** — optionally restrict results to calibrated regulatory monitors
- **WHO threshold styling** — graduated colours based on WHO 2021 air quality guidelines
- **Local cache** — completed fetches are stored as GeoJSON so they can be reloaded without re-fetching
- **Temporal controller** — fetched layers include start/end timestamps; use the QGIS Temporal Controller to animate data over time
- **IDW interpolation** — run inverse distance weighting on any fetched layer to produce a raster surface; for time-series data a separate raster is created per time step, each with temporal properties set so the Temporal Controller can animate through them
- **Export** — save any layer as GeoPackage, GeoJSON, or CSV
- **Dockable panel** — the plugin window docks into the QGIS interface like a native panel

## Requirements

- QGIS 3.44 or later (also compatible with QGIS 4)
- An [OpenAQ API key](https://explore.openaq.org/register) (free)

## Installation

### From a GitHub release

1. Download `qgis_openaq_vX.Y.Z.zip` from the [Releases](https://github.com/pessi-v/qgis_openaq/releases) page
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**
3. Select the downloaded ZIP and click **Install Plugin**

### For development

```bash
# Symlink the plugin folder into QGIS's plugin directory
ln -s /path/to/qgis_openaq \
  ~/Library/Application\ Support/QGIS/QGIS3/profiles/default/python/plugins/qgis_openaq
```

Then enable the plugin in **Plugins → Manage and Install Plugins**.

## Usage

1. Open the panel via the toolbar button or **Web → OpenAQ Air Quality**
2. Click **Settings** and enter your OpenAQ API key
3. Click **Draw on Map** and drag a rectangle over the area of interest (press ESC to cancel)
4. Select pollutants and a time range
5. Click **Fetch Data** — results appear as a point layer styled by concentration
6. Optionally click **Run IDW Interpolation** to generate a raster surface

## Data note

IDW interpolation fills spatial gaps between monitoring stations using inverse distance weighting. It does not account for local pollution sources (traffic, industry) or atmospheric dispersion. Treat the output as a rough spatial overview, not a measurement.

## Icon attribution

[Air quality icon created by Aranagraphics](https://www.flaticon.com/free-icons/air-quality)

## License

MIT
