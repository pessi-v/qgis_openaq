from __future__ import annotations

import os

from qgis.core import QgsApplication

from .compat.qt import QAction, QDockWidget, QIcon, Qt


class OpenAQPlugin:
    def __init__(self, iface) -> None:
        self.iface = iface
        self._action: QAction | None = None
        self._dock: QDockWidget | None = None

    def initGui(self) -> None:
        icon_path = os.path.join(os.path.dirname(__file__), "resources", "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        self._action = QAction(icon, "OpenAQ Air Quality", self.iface.mainWindow())
        self._action.setToolTip("Query and visualize OpenAQ air quality data")
        self._action.setCheckable(True)
        self._action.triggered.connect(self._toggle_dock)
        self.iface.addToolBarIcon(self._action)
        self.iface.addPluginToWebMenu("OpenAQ", self._action)
        self._create_dock()

    def unload(self) -> None:
        self.iface.removePluginWebMenu("OpenAQ", self._action)
        self.iface.removeToolBarIcon(self._action)
        if self._dock:
            self.iface.removeDockWidget(self._dock)
            self._dock.deleteLater()
            self._dock = None

    def _create_dock(self) -> None:
        from .gui.main_dialog import MainDialog
        widget = MainDialog(self.iface)
        self._dock = QDockWidget("OpenAQ Air Quality", self.iface.mainWindow())
        self._dock.setObjectName("OpenAQDock")  # lets QGIS remember position across sessions
        self._dock.setWidget(widget)
        self._dock.visibilityChanged.connect(self._action.setChecked)
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._dock)
        self._dock.hide()

    def _toggle_dock(self, checked: bool) -> None:
        if checked:
            self._dock.show()
            self._dock.raise_()
        else:
            self._dock.hide()
