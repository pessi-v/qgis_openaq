from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..compat.qt import (
    QButtonGroup, QDateTimeEdit, QDateTime, QGroupBox,
    QHBoxLayout, QLabel, QPushButton, QRadioButton, QVBoxLayout, QWidget,
    pyqtSignal,
)
from ..core.models import Granularity


class TimeRangeWidget(QWidget):
    changed = pyqtSignal()  # emitted whenever the time range or granularity changes

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._apply_preset("24h")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def datetime_from(self) -> datetime:
        # toSecsSinceEpoch() gives unambiguous UTC epoch regardless of how the
        # QDateTime was constructed, avoiding the local-time mislabelling bug.
        return datetime.fromtimestamp(self._from_edit.dateTime().toSecsSinceEpoch(), tz=timezone.utc)

    def datetime_to(self) -> datetime:
        return datetime.fromtimestamp(self._to_edit.dateTime().toSecsSinceEpoch(), tz=timezone.utc)

    def granularity(self) -> Granularity:
        return self._granularity_group.checkedButton().property("granularity")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # --- Preset buttons ---
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Quick select:"))
        for label, key in [("Last 24 h", "24h"), ("Last week", "7d"), ("Last month", "30d")]:
            btn = QPushButton(label)
            btn.setFlat(True)
            btn.clicked.connect(lambda _checked, k=key: self._apply_preset(k))
            preset_row.addWidget(btn)
        preset_row.addStretch()
        root.addLayout(preset_row)

        # --- From / to datetime pickers ---
        dt_row = QHBoxLayout()
        self._from_edit = QDateTimeEdit()
        self._from_edit.setCalendarPopup(True)
        self._from_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._to_edit = QDateTimeEdit()
        self._to_edit.setCalendarPopup(True)
        self._to_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._from_edit.dateTimeChanged.connect(self.changed)
        self._to_edit.dateTimeChanged.connect(self.changed)
        dt_row.addWidget(QLabel("From:"))
        dt_row.addWidget(self._from_edit)
        dt_row.addWidget(QLabel("To:"))
        dt_row.addWidget(self._to_edit)
        root.addLayout(dt_row)

        # --- Granularity radio buttons ---
        gran_row = QHBoxLayout()
        gran_row.addWidget(QLabel("Granularity:"))
        self._granularity_group = QButtonGroup(self)
        for label, gran in [("Raw", Granularity.RAW), ("Hourly", Granularity.HOURLY), ("Daily", Granularity.DAILY)]:
            rb = QRadioButton(label)
            rb.setProperty("granularity", gran)
            self._granularity_group.addButton(rb)
            gran_row.addWidget(rb)
            if gran == Granularity.HOURLY:
                rb.setChecked(True)
        self._granularity_group.buttonToggled.connect(lambda _btn, _checked: self.changed.emit())
        gran_row.addStretch()
        root.addLayout(gran_row)

    def _apply_preset(self, key: str) -> None:
        now = datetime.now(tz=timezone.utc)
        delta = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}[key]
        self._set_range(now - delta, now)

    def _set_range(self, dt_from: datetime, dt_to: datetime) -> None:
        self._from_edit.setDateTime(QDateTime.fromSecsSinceEpoch(int(dt_from.timestamp())))
        self._to_edit.setDateTime(QDateTime.fromSecsSinceEpoch(int(dt_to.timestamp())))
        self.changed.emit()
