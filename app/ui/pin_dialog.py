from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QLineEdit, QMessageBox,
    QVBoxLayout, QWidget,
)


class PinDialog(QDialog):
    def __init__(self, expected_pin: str = "0000", title: str = "Controle Parental", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.expected_pin = expected_pin
        self.setWindowTitle(title)
        self.setFixedSize(320, 180)
        self.setStyleSheet("background-color: #0d1118; color: #fff;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        header = QLabel("BLOQUEIO DE SEGURANÇA // BLUE LAB")
        header.setStyleSheet("font-family: 'DM Mono'; font-size: 10px; color: #1749e8; font-weight: 800;")
        layout.addWidget(header)

        info = QLabel("Digite seu PIN de 4 dígitos para desbloquear este conteúdo restrito:")
        info.setWordWrap(True)
        info.setStyleSheet("font-size: 12px; color: #a1b0cb;")
        layout.addWidget(info)

        self.pin_input = QLineEdit()
        self.pin_input.setEchoMode(QLineEdit.Password)
        self.pin_input.setMaxLength(8)
        self.pin_input.setAlignment(Qt.AlignCenter)
        self.pin_input.setStyleSheet("font-size: 20px; font-weight: 800; letter-spacing: 6px; padding: 6px;")
        layout.addWidget(self.pin_input)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._check_pin)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _check_pin(self) -> None:
        if self.pin_input.text() == self.expected_pin:
            self.accept()
        else:
            QMessageBox.warning(self, "PIN Incorreto", "O código PIN informado está incorreto.")
            self.pin_input.clear()
            self.pin_input.setFocus()
