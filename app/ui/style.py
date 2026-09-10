from __future__ import annotations


# Happiptv UI — quiet dark surfaces, electric-blue focus, dense desktop ergonomics.
STYLESHEET = """
* { font-family: 'Segoe UI Variable', 'Segoe UI', Arial, sans-serif; font-size: 13px; color: #e8edf7; outline: none; }
QMainWindow, QWidget#CentralWidget { background: #080b12; }
QWidget { selection-background-color: #2b5cff; selection-color: #ffffff; }
#HeaderBar { background: #0d111a; border-bottom: 1px solid #20283a; }
#Sidebar { background: #0a0e16; border-right: 1px solid #20283a; }
#BrandLogo { font-size: 18px; font-weight: 900; color: #ffffff; }
#BrandSub, QLabel[mono='true'] { font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px; color: #7f8da6; letter-spacing: 1px; }
QLabel[kicker='true'] { font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px; font-weight: 700; color: #6f87b6; letter-spacing: 1.3px; }
QLabel[heading='true'] { font-size: 27px; font-weight: 800; color: #f8faff; letter-spacing: -0.7px; }
QLabel[subheading='true'] { font-size: 15px; font-weight: 700; color: #dce4f3; }
QLabel[muted='true'] { color: #8794aa; }
#ResultCount { color: #8090aa; padding: 4px 0; }

QPushButton { min-height: 18px; padding: 8px 13px; background: #151b27; border: 1px solid #283247; border-radius: 7px; color: #dce4f3; font-weight: 600; }
QPushButton:hover { background: #1b2434; border-color: #4568c9; color: #ffffff; }
QPushButton:pressed { background: #111722; }
QPushButton:disabled { color: #596477; background: #10151e; border-color: #1b2331; }
QPushButton[primary='true'] { background: #2b5cff; border-color: #2b5cff; color: #ffffff; font-weight: 750; }
QPushButton[primary='true']:hover { background: #4771ff; border-color: #4771ff; }
QPushButton[danger='true'] { background: #241419; border-color: #55242e; color: #ff8a9b; }
QPushButton[danger='true']:hover { background: #a52d42; border-color: #c53b52; color: #ffffff; }
QPushButton[nav='true'] { min-height: 20px; padding: 11px 13px; text-align: left; background: transparent; border: 0; border-left: 2px solid transparent; border-radius: 5px; color: #8d9ab0; font-weight: 600; }
QPushButton[nav='true']:hover { background: #111827; color: #dbe5f8; }
QPushButton[nav='true']:checked { background: #132044; border-left-color: #4e7aff; color: #ffffff; }

QLineEdit, QSpinBox, QComboBox { min-height: 20px; padding: 8px 11px; background: #0f1520; border: 1px solid #263147; border-radius: 7px; color: #edf2fc; }
QLineEdit:hover, QSpinBox:hover, QComboBox:hover { border-color: #35445e; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { background: #111927; border-color: #4e7aff; }
#GlobalSearch { min-height: 24px; padding: 8px 14px; background: #111724; border-color: #2a354b; border-radius: 9px; }
#GlobalSearch:focus { border: 1px solid #4e7aff; background: #131c2c; }
QComboBox::drop-down { width: 24px; border: 0; }
QComboBox QAbstractItemView { padding: 5px; background: #111722; border: 1px solid #2b3850; border-radius: 6px; color: #e8edf7; selection-background-color: #2549aa; }

#FilterPanel { background: #0e141f; border: 1px solid #202a3c; border-radius: 10px; }
QListWidget, QTreeWidget, QTableWidget { background: #0c111a; alternate-background-color: #0f1520; border: 1px solid #202a3b; border-radius: 9px; color: #dfe6f2; }
QListWidget::item { padding: 9px 11px; border-bottom: 1px solid #182131; }
QListWidget::item:hover { background: #121b2a; }
QListWidget::item:selected { background: #182a56; color: #ffffff; }
#CatalogGrid { background: transparent; border: 0; }
#CatalogGrid::item { padding: 8px; margin: 0; border: 1px solid #202a3b; border-radius: 9px; background: #0f1520; color: #dfe7f6; }
#CatalogGrid::item:hover { background: #141e2e; border-color: #3d5da9; }
#CatalogGrid::item:selected { background: #162650; border: 2px solid #4e7aff; color: #ffffff; }
#MediaRail { background: transparent; border: 0; }
#MediaRail::item { padding: 7px; border: 1px solid #202a3b; border-radius: 9px; background: #0f1520; color: #e5ebf6; }
#MediaRail::item:hover { background: #151f30; border-color: #4568c9; }
#MediaRail::item:selected { background: #182a56; border: 2px solid #4e7aff; color: #ffffff; }
#SearchResults::item { min-height: 34px; border-bottom: 1px solid #182131; }
#SearchResults::item:hover { background: #131d2d; }
#SearchResults::item:selected { background: #1a326d; color: #ffffff; }
QHeaderView::section { padding: 9px 11px; background: #111824; border: 0; border-right: 1px solid #202a3b; border-bottom: 1px solid #202a3b; color: #8593aa; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px; font-weight: 700; }

QTabWidget::pane { border: 1px solid #202a3b; border-radius: 8px; background: #0c111a; }
QTabBar::tab { padding: 9px 16px; color: #8492a8; background: #0d131d; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #ffffff; border-bottom-color: #4e7aff; background: #111927; }
QGroupBox { margin-top: 14px; padding: 17px; background: #0d131d; border: 1px solid #202a3b; border-radius: 9px; font-weight: 700; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #7e9cff; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px; }
QProgressBar { min-height: 7px; background: #111722; border: 1px solid #253046; border-radius: 4px; color: #dce5f4; text-align: center; font-size: 10px; }
QProgressBar::chunk { background: #3f70ff; border-radius: 3px; }

QScrollBar:vertical { width: 9px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { min-height: 30px; background: #2b3850; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: #4968a8; }
QScrollBar:horizontal { height: 9px; background: transparent; margin: 2px; }
QScrollBar::handle:horizontal { min-width: 30px; background: #2b3850; border-radius: 4px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QToolTip { padding: 6px 9px; color: #eaf0fb; background: #171f2d; border: 1px solid #34425a; }

#HeroBackdropCard { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #101b36, stop:0.62 #111827, stop:1 #0d131d); border: 1px solid #29447f; border-radius: 12px; }
#PlayerContainer { background: #000000; }
#PlayerControlsBar { background: rgba(8, 11, 18, 0.94); border-top: 1px solid #202a3b; padding: 10px 16px; }
#PlayerTimeLabel { font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; color: #9ba8bd; }
#DiagnosticsHUD { background: rgba(8, 12, 20, 0.92); border: 1px solid #4e7aff; padding: 12px; border-radius: 7px; }
#MiniPlayerDock { background: #080b12; border: 2px solid #4e7aff; border-radius: 9px; }
#DownloadBanner { background: #0f1623; border: 1px solid #25324a; border-radius: 8px; padding: 8px; }
"""
