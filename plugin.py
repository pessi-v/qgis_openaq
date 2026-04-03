from __future__ import annotations

import os

from qgis.core import QgsApplication

from .compat.qt import QAction, QIcon


class OpenAQPlugin:
    def __init__(self, iface) -> None:
        self.iface = iface
        self._action: QAction | None = None
        self._dialog = None

    def initGui(self) -> None:
        icon_path = os.path.join(os.path.dirname(__file__), "resources", "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        self._action = QAction(icon, "OpenAQ Air Quality", self.iface.mainWindow())
        self._action.setToolTip("Query and visualize OpenAQ air quality data")
        self._action.triggered.connect(self._show_dialog)
        self.iface.addToolBarIcon(self._action)
        self.iface.addPluginToWebMenu("OpenAQ", self._action)

    def unload(self) -> None:
        self.iface.removePluginWebMenu("OpenAQ", self._action)
        self.iface.removeToolBarIcon(self._action)
        if self._dialog:
            self._dialog.close()
            self._dialog = None

    def _show_dialog(self) -> None:
        if self._dialog is None:
            from .gui.main_dialog import MainDialog
            self._dialog = MainDialog(self.iface, self.iface.mainWindow())
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()
