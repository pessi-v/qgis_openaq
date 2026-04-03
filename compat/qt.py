# Single Qt compatibility boundary for QGIS 3 (PyQt5) and QGIS 4 (PyQt6).
# All Qt/PyQt imports in the plugin must come from here — no bare PyQt5/PyQt6
# imports elsewhere.
#
# Strategy: prefer qgis.PyQt (QGIS's own version-agnostic re-export) so we
# never touch the raw PyQt5/PyQt6 packages directly inside QGIS.  The bare
# PyQt fallback is only for running core/ tests outside QGIS.
#
# NOTE: In Qt6/PyQt6, QAction moved from QtWidgets → QtGui.  qgis.PyQt
# normalises this and keeps QAction in QtWidgets, so no special casing needed
# when going through that path.
try:
    # Inside QGIS — use QGIS's version-agnostic re-export.
    from qgis.PyQt.QtCore import (
        Qt, QSettings, QTimer, pyqtSignal, QThread,
        QDateTime, QDate, QSize, QObject,
    )
    from qgis.PyQt.QtWidgets import (
        QDialog, QDockWidget, QComboBox, QProgressBar, QWidget, QLabel, QLineEdit,
        QPushButton, QCheckBox, QRadioButton, QGroupBox,
        QHBoxLayout, QVBoxLayout, QFormLayout, QGridLayout,
        QListWidget, QListWidgetItem, QSizePolicy, QAction,
        QMessageBox, QDialogButtonBox, QSplitter, QFrame,
        QButtonGroup, QToolButton, QDateTimeEdit, QApplication,
        QScrollArea, QFileDialog,
    )
    from qgis.PyQt.QtGui import QIcon, QColor, QDoubleValidator, QCursor
    try:
        from qgis.core import Qgis
        QGIS_V4 = int(Qgis.QGIS_VERSION.split(".")[0]) >= 4
    except Exception:
        QGIS_V4 = False
except ImportError:
    # Outside QGIS (e.g. running unit tests on core/ without QGIS installed).
    # QAction is in QtGui in PyQt6, QtWidgets in PyQt5.
    try:
        from PyQt6.QtCore import (
            Qt, QSettings, QTimer, pyqtSignal, QThread,
            QDateTime, QDate, QSize, QObject,
        )
        from PyQt6.QtWidgets import (
            QDialog, QDockWidget, QComboBox, QProgressBar, QWidget, QLabel, QLineEdit,
            QPushButton, QCheckBox, QRadioButton, QGroupBox,
            QHBoxLayout, QVBoxLayout, QFormLayout, QGridLayout,
            QListWidget, QListWidgetItem, QSizePolicy,
            QMessageBox, QDialogButtonBox, QSplitter, QFrame,
            QButtonGroup, QToolButton, QDateTimeEdit, QApplication,
            QScrollArea, QFileDialog,
        )
        from PyQt6.QtGui import QIcon, QColor, QDoubleValidator, QCursor, QAction
        QGIS_V4 = True
    except ImportError:
        from PyQt5.QtCore import (
            Qt, QSettings, QTimer, pyqtSignal, QThread,
            QDateTime, QDate, QSize, QObject,
        )
        from PyQt5.QtWidgets import (
            QDialog, QDockWidget, QComboBox, QProgressBar, QWidget, QLabel, QLineEdit,
            QPushButton, QCheckBox, QRadioButton, QGroupBox,
            QHBoxLayout, QVBoxLayout, QFormLayout, QGridLayout,
            QListWidget, QListWidgetItem, QSizePolicy, QAction,
            QMessageBox, QDialogButtonBox, QSplitter, QFrame,
            QButtonGroup, QToolButton, QDateTimeEdit, QApplication,
            QScrollArea, QFileDialog,
        )
        from PyQt5.QtGui import QIcon, QColor, QDoubleValidator, QCursor
        QGIS_V4 = False
