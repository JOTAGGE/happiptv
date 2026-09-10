from __future__ import annotations

# Blue Lab Design System for Happitv (https://bluelabhub.vercel.app)
# Primary: #1749e8 (Blue Lab Electric Blue), Ink: #0a0a0a, Surface: #12161f, Line: #1d2535

STYLESHEET = """
* {
    font-family: 'Inter', 'Segoe UI', Arial, sans-serif;
    font-size: 13px;
    color: #f1f3f7;
    outline: none;
}

QMainWindow, QWidget#CentralWidget {
    background-color: #0a0a0a;
}

/* Sidebar & Header Navigation */
#Sidebar {
    background-color: #0b0d13;
    border-right: 1px solid #1c2230;
}

#HeaderBar {
    background-color: #0e1118;
    border-bottom: 1px solid #1c2230;
}

#BrandLogo {
    font-size: 18px;
    font-weight: 900;
    letter-spacing: -0.5px;
    color: #ffffff;
}

#BrandAccent {
    color: #1749e8;
    font-weight: 900;
}

#BrandSub {
    font-family: 'DM Mono', 'Consolas', monospace;
    font-size: 10px;
    color: #6b778c;
    letter-spacing: 1.5px;
    font-weight: 700;
}

/* Kicker & Meta */
.mono, QLabel[mono='true'] {
    font-family: 'DM Mono', 'Consolas', monospace;
    letter-spacing: 0.5px;
}

.kicker, QLabel[kicker='true'] {
    font-family: 'DM Mono', 'Consolas', monospace;
    font-size: 11px;
    color: #79879c;
    letter-spacing: 1px;
    font-weight: 700;
    text-transform: uppercase;
}

QLabel[muted='true'] {
    color: #738094;
}

QLabel[heading='true'] {
    font-size: 22px;
    font-weight: 900;
    color: #ffffff;
    letter-spacing: -0.5px;
}

QLabel[subheading='true'] {
    font-size: 15px;
    font-weight: 700;
    color: #dbe2ed;
}

/* Buttons */
QPushButton {
    background-color: #161b26;
    border: 1px solid #232d3f;
    border-radius: 4px;
    padding: 8px 14px;
    font-weight: 600;
    color: #e2e8f0;
}

QPushButton:hover {
    background-color: #1e2638;
    border-color: #1749e8;
    color: #ffffff;
}

QPushButton:pressed {
    background-color: #121824;
}

QPushButton[primary='true'] {
    background-color: #1749e8;
    color: #ffffff;
    border: 1px solid #1749e8;
    font-weight: 800;
}

QPushButton[primary='true']:hover {
    background-color: #2258ff;
    border-color: #2258ff;
}

QPushButton[danger='true'] {
    background-color: #2a1518;
    color: #ff6b7a;
    border: 1px solid #4a1d22;
}

QPushButton[danger='true']:hover {
    background-color: #d32f2f;
    color: #ffffff;
    border-color: #d32f2f;
}

/* Sidebar Navigation Tabs */
QPushButton[nav='true'] {
    text-align: left;
    border: 0;
    border-left: 3px solid transparent;
    background: transparent;
    padding: 12px 14px;
    font-size: 13px;
    font-weight: 600;
    color: #8b99ad;
    border-radius: 0;
}

QPushButton[nav='true']:hover {
    background-color: #131924;
    color: #ffffff;
}

QPushButton[nav='true']:checked {
    background-color: #121c33;
    color: #ffffff;
    border-left: 3px solid #1749e8;
    font-weight: 700;
}

/* Inputs & Forms */
QLineEdit, QSpinBox {
    background-color: #11151e;
    border: 1px solid #222b3d;
    border-radius: 4px;
    padding: 8px 12px;
    color: #f1f3f7;
    selection-background-color: #1749e8;
}

QLineEdit:focus, QSpinBox:focus {
    border-color: #1749e8;
    background-color: #141a26;
}

QComboBox {
    background-color: #11151e;
    border: 1px solid #222b3d;
    border-radius: 4px;
    padding: 7px 12px;
    color: #f1f3f7;
    min-width: 130px;
}

QComboBox:hover, QComboBox:focus {
    border-color: #1749e8;
}

QComboBox::drop-down {
    border: 0;
    width: 24px;
}

QComboBox QAbstractItemView {
    background-color: #11151e;
    border: 1px solid #222b3d;
    selection-background-color: #1749e8;
    selection-color: #ffffff;
    color: #f1f3f7;
    padding: 6px;
}

/* Lists, Tables, Trees */
QListWidget, QTreeWidget, QTableWidget {
    background-color: #0e1118;
    border: 1px solid #1c2230;
    border-radius: 4px;
    outline: 0;
    color: #e2e8f0;
}

QListWidget::item {
    padding: 9px 12px;
    border-bottom: 1px solid #171d29;
}

QListWidget::item:hover {
    background-color: #141a27;
}

QListWidget::item:selected {
    background-color: #162445;
    color: #ffffff;
    border-left: 2px solid #1749e8;
}

QHeaderView::section {
    background-color: #131722;
    padding: 9px 12px;
    border: 0;
    border-right: 1px solid #1c2230;
    border-bottom: 1px solid #1c2230;
    font-weight: 700;
    font-family: 'DM Mono', monospace;
    font-size: 11px;
    color: #8b99ad;
    text-transform: uppercase;
}

/* Progress Bars */
QProgressBar {
    border: 1px solid #222b3d;
    border-radius: 3px;
    text-align: center;
    background-color: #11151e;
    font-size: 10px;
    font-family: 'DM Mono', monospace;
}

QProgressBar::chunk {
    background-color: #1749e8;
}

/* Group Boxes & Panels */
QGroupBox {
    border: 1px solid #1c2230;
    border-radius: 4px;
    margin-top: 14px;
    padding: 16px;
    font-weight: 700;
    background-color: #0e1219;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #1749e8;
    font-family: 'DM Mono', monospace;
    font-size: 11px;
    text-transform: uppercase;
}

/* Custom Scrollbars */
QScrollBar:vertical {
    background-color: #0a0a0a;
    width: 8px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background-color: #242e40;
    min-height: 28px;
    border-radius: 4px;
}

QScrollBar::handle:vertical:hover {
    background-color: #1749e8;
}

QScrollBar:horizontal {
    background-color: #0a0a0a;
    height: 8px;
    margin: 0;
}

QScrollBar::handle:horizontal {
    background-color: #242e40;
    min-width: 28px;
    border-radius: 4px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #1749e8;
}

QScrollBar::add-line, QScrollBar::sub-line {
    border: none;
    background: none;
}

/* Status Pill */
.status-pill, QFrame[statusPill='true'] {
    border: 1px solid #232d3f;
    background-color: #11151e;
    padding: 3px 8px;
    font-family: 'DM Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
}

/* Video Player HUD & Controls */
#PlayerContainer {
    background-color: #000000;
}

#PlayerControlsBar {
    background-color: rgba(10, 13, 19, 0.92);
    border-top: 1px solid #1c2332;
    padding: 10px 16px;
}

#PlayerTimeLabel {
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    color: #9ba8bc;
}

#DiagnosticsHUD {
    background-color: rgba(10, 12, 16, 0.88);
    border: 1px solid #1749e8;
    padding: 12px;
    border-radius: 4px;
    font-family: 'DM Mono', monospace;
    font-size: 11px;
    color: #dbe2ed;
}

#HeroBackdropCard {
    background-color: #10141d;
    border: 1px solid #1e2636;
    border-radius: 6px;
}

#MiniPlayerDock {
    background-color: #0c0e14;
    border: 2px solid #1749e8;
    border-radius: 6px;
}

#DownloadBanner {
    background-color: #11151f;
    border: 1px solid #1d2535;
    border-radius: 4px;
    padding: 10px 14px;
}
"""
