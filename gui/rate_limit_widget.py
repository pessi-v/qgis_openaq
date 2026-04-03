from __future__ import annotations

from ..compat.qt import QHBoxLayout, QLabel, QTimer, QWidget
from ..core.rate_limiter import RateLimiter


class RateLimitWidget(QWidget):
    """Live display of request counts against OpenAQ rate limits.

    Refreshes every second via a QTimer.
    """

    def __init__(self, rate_limiter: RateLimiter, parent=None) -> None:
        super().__init__(parent)
        self._rl = rate_limiter
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._min_label = QLabel()
        self._hr_label = QLabel()
        layout.addWidget(QLabel("Rate limits —"))
        layout.addWidget(self._min_label)
        layout.addWidget(QLabel("|"))
        layout.addWidget(self._hr_label)
        layout.addStretch()
        self._refresh()

    def _refresh(self) -> None:
        per_min = self._rl.count_last_minute()
        per_hr = self._rl.count_last_hour()
        self._min_label.setText(f"{per_min}/{RateLimiter.MINUTE_LIMIT} /min")
        self._hr_label.setText(f"{per_hr}/{RateLimiter.HOUR_LIMIT} /hr")

        # Highlight when approaching limits.
        warn_min = per_min >= RateLimiter.MINUTE_LIMIT * 0.8
        warn_hr = per_hr >= RateLimiter.HOUR_LIMIT * 0.8
        style = "color: orange;" if (warn_min or warn_hr) else ""
        self._min_label.setStyleSheet(style)
        self._hr_label.setStyleSheet(style)
