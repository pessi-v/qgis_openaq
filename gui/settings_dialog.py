from __future__ import annotations

from ..compat.qt import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QSettings, QVBoxLayout,
)

SETTINGS_PREFIX = "openaq/"


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("OpenAQ Settings")
        self.setMinimumWidth(400)
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setPlaceholderText("Paste your OpenAQ API key here")
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("API Key:", self._api_key_edit)

        note = QLabel(
            '<a href="https://explore.openaq.org">Get a free key at explore.openaq.org</a>'
        )
        note.setOpenExternalLinks(True)
        form.addRow("", note)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load(self) -> None:
        s = QSettings()
        self._api_key_edit.setText(s.value(SETTINGS_PREFIX + "api_key", ""))

    def _save(self) -> None:
        s = QSettings()
        s.setValue(SETTINGS_PREFIX + "api_key", self._api_key_edit.text().strip())

    @staticmethod
    def saved_api_key() -> str:
        return QSettings().value(SETTINGS_PREFIX + "api_key", "")
