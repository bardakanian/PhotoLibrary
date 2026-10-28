from .colors import Palette
from .metrics import Metrics
from .typography import Typography


def build_stylesheet(c: Palette) -> str:
    return f"""
    QWidget {{
        color: {c.text}; background: transparent; font-size: {Typography.BODY}px;
        selection-background-color: {c.selection}; selection-color: {c.text};
    }}
    QMainWindow, QDialog {{ background: {c.background}; }}
    QToolTip {{ background: {c.elevated}; color: {c.text}; border: 1px solid {c.border_strong};
        padding: 6px 8px; border-radius: 6px; }}
    QLabel[muted="true"] {{ color: {c.text_muted}; }}
    QLabel[subtle="true"] {{ color: {c.text_subtle}; font-size: {Typography.CAPTION}px; }}
    QLabel[role="title"] {{ font-size: {Typography.TITLE}px; font-weight: 600; }}
    QLabel[role="section"] {{ font-size: {Typography.SECTION}px; font-weight: 600; }}
    QLabel[role="insightValue"] {{ font-size: 27px; font-weight: 650; }}
    QLabel[role="badge"] {{ background: {c.surface_alt}; color: {c.text_muted}; border-radius: {Metrics.RADIUS}px;
        padding: 2px 7px; font-size: {Typography.CAPTION}px; font-weight: 600; }}
    QFrame[role="surface"] {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: {Metrics.RADIUS_LARGE}px; }}
    QFrame[role="toolbar"] {{ background: {c.surface}; border-bottom: 1px solid {c.border}; }}
    QPushButton {{ min-height: {Metrics.CONTROL_HEIGHT}px; padding: 0 13px; border-radius: {Metrics.RADIUS_SMALL}px; border: 1px solid {c.border_strong};
        background: {c.surface}; color: {c.text}; font-weight: 500; }}
    QPushButton:hover {{ background: {c.surface_alt}; border-color: {c.border_strong}; }}
    QPushButton:pressed {{ background: {c.border}; }}
    QPushButton:focus {{ border: 2px solid {c.accent}; padding: 0 12px; }}
    QPushButton:disabled {{ color: {c.text_subtle}; background: {c.surface_alt}; border-color: {c.border}; }}
    QPushButton[variant="primary"] {{ color: #FFFFFF; background: {c.accent}; border-color: {c.accent}; }}
    QPushButton[variant="primary"]:hover {{ background: {c.accent_hover}; border-color: {c.accent_hover}; }}
    QPushButton[variant="primary"]:pressed {{ background: {c.accent_pressed}; }}
    QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent; }}
    QPushButton[variant="ghost"]:hover {{ background: {c.surface_alt}; }}
    QPushButton[variant="danger"] {{ color: {c.danger}; background: transparent; border-color: {c.danger}; }}
    QPushButton[compact="true"] {{ min-width: {Metrics.CONTROL_HEIGHT}px; max-width: {Metrics.CONTROL_HEIGHT}px; padding: 0; }}
    QLineEdit, QComboBox {{ min-height: {Metrics.CONTROL_HEIGHT}px; padding: 0 10px; background: {c.surface};
        border: 1px solid {c.border_strong}; border-radius: {Metrics.RADIUS_SMALL}px; }}
    QLineEdit:focus, QComboBox:focus {{ border: 2px solid {c.accent}; padding: 0 9px; }}
    QLineEdit:disabled, QComboBox:disabled {{ color: {c.text_subtle}; background: {c.surface_alt}; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{ background: {c.elevated}; border: 1px solid {c.border};
        outline: none; selection-background-color: {c.selection}; padding: 4px; }}
    QScrollArea {{ border: none; background: {c.background}; }}
    QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {c.border_strong}; border-radius: 4px; min-height: 32px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ height: 0; background: transparent; }}
    QTableView {{ background: {c.surface}; alternate-background-color: {c.surface_alt}; border: 1px solid {c.border};
        border-radius: 10px; gridline-color: transparent; outline: none; }}
    QTableView::item {{ padding: 8px 10px; border-bottom: 1px solid {c.border}; }}
    QTableView::item:selected {{ background: {c.selection}; color: {c.text}; }}
    QHeaderView::section {{ background: {c.surface_alt}; color: {c.text_muted}; padding: 9px 10px;
        border: none; border-bottom: 1px solid {c.border}; font-weight: 600; }}
    QProgressBar {{ background: {c.surface_alt}; border: none; border-radius: 2px; height: 3px; }}
    QProgressBar::chunk {{ background: {c.accent}; border-radius: 2px; }}
    QMenu {{ background: {c.elevated}; border: 1px solid {c.border}; border-radius: 8px; padding: 6px; }}
    QMenu::item {{ padding: 7px 28px 7px 10px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {c.selection}; }}
    QStatusBar {{ background: {c.surface}; border-top: 1px solid {c.border}; color: {c.text_muted}; }}
    QAbstractItemView#mediaGrid {{ background: {c.background}; border: none; }}
    QFrame[role="segmented"] {{ background: {c.surface_alt}; border: 1px solid {c.border}; border-radius: {Metrics.RADIUS_SMALL + 1}px; }}
    QPushButton[segment="true"] {{ min-height: 26px; padding: 0 12px; border: none; border-radius: 5px;
        background: transparent; color: {c.text_muted}; font-weight: 600; }}
    QPushButton[segment="true"]:hover {{ color: {c.text}; }}
    QPushButton[segment="true"]:checked {{ background: {c.surface}; color: {c.text}; border: 1px solid {c.border_strong}; }}
    QPushButton[segment="true"]:focus {{ border: 2px solid {c.accent}; padding: 0 11px; }}
    QSlider::groove:horizontal {{ height: 4px; background: {c.border_strong}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {c.accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {c.surface}; border: 1px solid {c.border_strong}; width: 14px; height: 14px;
        margin: -6px 0; border-radius: 8px; }}
    QSlider::handle:horizontal:hover {{ border-color: {c.accent}; }}
    QSlider:disabled {{ opacity: 0.4; }}
    QFrame[role="sidebar"] {{ background: {c.surface_alt}; border-right: 1px solid {c.border}; }}
    QTreeView#sidebar {{ background: transparent; border: none; outline: none; }}
    QTreeView#sidebar::item {{ min-height: 30px; padding: 0 6px; border-radius: 6px; }}
    QTreeView#sidebar::item:hover {{ background: {c.border}; }}
    QTreeView#sidebar::item:selected {{ background: {c.accent_soft}; color: {c.text}; }}
    QTreeView#sidebar::branch {{ background: transparent; image: none; border: none; }}
    QFrame[role="infoPanel"] {{ background: {c.surface}; border-left: 1px solid {c.border}; }}
    QLabel[role="infoKey"] {{ color: {c.text_subtle}; font-size: {Typography.CAPTION}px; font-weight: 600; }}
    QLabel[role="infoValue"] {{ color: {c.text}; }}
    QWidget#viewerPage {{ background: #0B0C0E; }}
    QFrame[role="viewerBar"] {{ background: rgba(18, 20, 24, 0.94); border: none; }}
    QFrame[role="viewerBar"] QLabel {{ color: #ECEEF2; }}
    QFrame[role="viewerBar"] QLabel[muted="true"] {{ color: #A6ADBA; }}
    QFrame[role="viewerBar"] QPushButton {{ background: rgba(255,255,255,0.08); color: #ECEEF2; border: 1px solid rgba(255,255,255,0.14); }}
    QFrame[role="viewerBar"] QPushButton:hover {{ background: rgba(255,255,255,0.16); }}
    QFrame[role="viewerBar"] QPushButton:checked {{ background: rgba(255,255,255,0.24); }}
    QFrame[role="viewerBar"] QSlider::groove:horizontal {{ background: rgba(255,255,255,0.25); }}
    QFrame[role="viewerBar"] QSlider::handle:horizontal {{ background: #ECEEF2; border-color: #ECEEF2; }}
    """
