from __future__ import annotations

"""Feuille de style de BeautyCut.

Toute la feuille est générée à partir des jetons du thème actif : changer de
thème reconstruit le style sans jamais recoder une couleur dans un composant.
"""

from .themes import Theme


def build_stylesheet(theme: Theme) -> str:
    t = theme
    hover = t.hover or t.surface
    field = t.field or t.surface
    shade = t.shade or t.selected
    light = t.light
    subtle_text = t.text if light else t.muted
    return f"""
    QMainWindow, QWidget#root {{ background: {t.background}; color: {t.text}; font-family: 'Segoe UI'; font-size: 10.5pt; }}
    QLabel {{ color: {t.text}; background: transparent; }}
    QLabel#brandMark {{ background: transparent; }}
    QLabel#brand {{ font-size: 19pt; font-weight: 700; letter-spacing: 0; }}
    QLabel#subtitle {{ color: {t.muted}; font-size: 9.5pt; }}
    QLabel#muted {{ color: {t.muted}; font-size: 9.5pt; }}
    QLabel#statusInfo, QLabel#trackDetails {{ color: {t.muted}; font-size: 9.5pt; }}
    QLabel#statusInfo[tone="success"] {{ color: {t.success}; }}
    QLabel#statusInfo[tone="warning"] {{ color: {t.warning}; }}
    QLabel#statusInfo[tone="error"] {{ color: {t.error}; }}
    QLabel#cardTitle {{ font-size: 11pt; font-weight: 700; color: {t.text}; }}
    QLabel#cardHint {{ color: {t.muted}; font-size: 9pt; }}
    QLabel#mediaName {{ font-weight: 700; color: {t.text}; font-size: 10pt; }}
    QLabel#previewTitle {{ color: {t.text}; font-size: 11pt; font-weight: 700; letter-spacing: 0; }}
    QLabel#previewTitleIcon, QLabel#footerFileIcon, QLabel#statusIndicator {{ background: transparent; }}
    QLabel#footerDivider {{ background: {t.border}; }}
    QFrame#headerDivider {{ background: {t.border}; }}
    QLabel#accentText {{ color: {t.accent_light}; font-weight: 700; }}
    QLabel#formatDetails {{ color: {t.accent_light}; font-size: 9.5pt; font-weight: 700; }}
    QLabel#exportStage {{ color: {t.accent_light}; font-size: 10pt; font-weight: 700; }}
    QLabel#metric {{ color: {t.muted}; font-size: 9pt; }}
    QLabel#fieldLabel {{ color: {subtle_text}; font-size: 9pt; font-weight: 600; }}
    QLabel#sectionTitle {{ color: {t.text}; font-size: 9.5pt; font-weight: 700; }}
    QLabel#sectionSummary {{ color: {t.muted}; font-size: 8.5pt; }}
    QLabel#updateBannerTitle {{ color: {t.accent_light}; font-weight: 700; }}
    QLabel#emptyHint {{ color: {t.muted}; font-size: 9pt; }}
    QLabel#engineBadge {{ color: {t.accent_light}; background: {shade}; border: 1px solid {t.border};
        border-radius: 11px; padding: 5px 10px; font-size: 9pt; font-weight: 700; max-height: 38px; }}
    QLabel#engineBadge[tone="success"] {{ color: {t.text}; border-color: {t.border}; background: {field}; }}
    QLabel#engineBadge[tone="warning"] {{ color: {t.warning}; border-color: {t.warning}; }}
    QLabel#engineBadge[tone="error"] {{ color: {t.error}; border-color: {t.error}; }}
    QLabel#statusPill {{ color: {t.muted}; background: {field}; border: 1px solid {t.border}; border-radius: 11px;
        padding: 4px 10px; font-size: 9pt; font-weight: 600; }}
    QLabel#statusPill[tone="success"] {{ color: {t.success}; border-color: {t.accent}; background: {shade}; }}
    QLabel#statusPill[tone="warning"] {{ color: {t.warning}; border-color: {t.warning}; }}
    QLabel#statusPill[tone="error"] {{ color: {t.error}; border-color: {t.error}; }}
    QLabel#statusPill[tone="info"] {{ color: {t.accent_light}; border-color: {t.border}; background: {shade}; }}

    QWidget#headerBar {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t.card}, stop:0.62 {t.background}, stop:1 {t.card}); border: 0; border-bottom: 1px solid {t.border}; border-radius: 0; }}
    QFrame#navigationRail {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {t.card}, stop:1 {t.background}); border: 0; border-right: 1px solid {t.border}; border-radius: 0; }}
    QWidget#previewToolbar {{ background: transparent; }}
    QWidget#previewHeader {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t.card}, stop:1 {t.surface}); border: 1px solid {t.border}; border-radius: 9px; }}
    QFrame#card, QFrame#previewCard, QFrame#previewWorkspace, QFrame#collapsibleSection, QFrame#exportDock, QFrame#infoCard {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {t.card}, stop:1 {t.surface}); border: 1px solid {t.border}; border-radius: 14px; }}
    QFrame#previewCard {{ background: transparent; border: 0; border-radius: 0; }}
    QFrame#previewWorkspace {{ background: {t.card}; border-color: {t.border}; border-radius: 15px; }}
    QFrame#collapsibleSection[expanded="true"] {{ border-color: {t.border}; }}
    QFrame#exportDock {{ border-radius: 0; border-color: {t.accent}; border-left: 0; border-right: 0; border-bottom: 0; }}
    QFrame#updateBanner {{ background: {shade}; border: 1px solid {t.accent}; border-radius: 12px; }}
    QFrame#mediaDropZone {{ background: {field}; border: 1px dashed {t.border}; border-radius: 11px; }}
    QFrame#mediaDropZone[active="true"] {{ border: 1px dashed {t.accent}; background: {shade}; }}
    QLabel#mediaThumbnail {{ background: {field}; border: 1px solid {t.border}; border-radius: 9px; color: {t.muted}; }}
    QWidget#sectionBody {{ background: transparent; }}
    QWidget#sidebar {{ background: {t.background}; }}
    QWidget#workspace {{ background: {t.background}; }}
    QWidget#previewCanvas {{ background: {t.preview}; border: 1px solid {t.border}; border-radius: 2px; }}

    QToolButton#sectionHeader {{ color: {t.text}; background: transparent; border: 0; text-align: left;
        padding: 6px 8px; font-size: 10.5pt; font-weight: 700; border-radius: 8px; }}
    QToolButton#sectionHeader[compactSummary="true"] {{ padding: 2px 8px; min-height: 25px; }}
    QToolButton#sectionHeader:hover {{ color: {t.accent_light}; background: {shade}; }}
    QToolButton#sectionHeader:checked {{ color: {t.accent_light}; }}
    QToolButton#sectionHeader:focus {{ border: 1px solid {t.focus}; }}
    QToolButton#navButton {{ color: {t.muted}; background: transparent; border: 1px solid transparent; border-radius: 11px; padding: 9px 4px; font-size: 9pt; font-weight: 600; }}
    QToolButton#navButton:hover {{ color: {t.accent_light}; background: {shade}; border-color: {t.border}; }}
    QToolButton#navButton:checked {{ color: {t.accent_light}; background: {shade}; border: 1px solid {t.accent}; }}
    QToolButton#navButton:checked {{ border-left: 3px solid {t.accent}; }}
    QToolButton#navButton:focus {{ border: 1px solid {t.focus}; }}
    QToolButton#previewToolButton {{ color: {t.muted}; background: {field}; border: 1px solid {t.border};
        border-radius: 7px; padding: 4px 7px; min-height: 20px; min-width: 20px; font-size: 9.5pt; }}
    QToolButton#previewToolButton:hover {{ color: {t.accent_light}; border-color: {t.accent}; background: {shade}; }}
    QToolButton#previewToolButton:checked {{ color: {t.accent_text}; background: {t.accent}; border-color: {t.accent}; }}
    QToolButton#previewToolButton:focus {{ border: 1px solid {t.focus}; }}
    QToolButton#previewActionButton {{ color: {t.text}; background: {field}; border: 1px solid {t.border}; border-radius: 9px; padding: 7px 9px; font-weight: 600; min-height: 24px; }}
    QToolButton#previewActionButton:hover {{ color: {t.accent_light}; background: {shade}; border-color: {t.accent}; }}
    QToolButton#previewActionButton:checked {{ color: {t.accent_light}; background: {shade}; border-color: {t.accent}; }}
    QToolButton#previewActionButton:focus {{ border: 1px solid {t.focus}; }}
    QToolButton#cropAnchorButton {{ color: {t.text}; background: {field}; border: 1px solid {t.border};
        border-radius: 7px; font-size: 11pt; }}
    QToolButton#cropAnchorButton:hover {{ color: {t.accent_light}; border-color: {t.accent}; }}
    QToolButton#cropAnchorButton:checked {{ color: {t.accent_text}; background: {t.accent}; border-color: {t.accent}; }}
    QToolButton#cropAnchorButton:disabled {{ color: {t.muted}; background: {t.surface}; }}
    QToolButton#presetButton {{ color: {t.text}; background: {field}; border: 1px solid {t.border};
        border-radius: 11px; padding: 6px 4px; font-weight: 700; font-size: 10pt; }}
    QToolButton#presetButton:hover {{ background: {shade}; border-color: {t.accent}; }}
    QToolButton#presetButton:checked {{ color: {t.accent_light}; background: {shade}; border: 2px solid {t.accent}; }}
    QToolButton#presetButton:focus {{ border: 1px solid {t.focus}; }}
    QToolButton#presetButton:disabled {{ color: {t.muted}; }}
    QToolButton#sidePresetButton {{ color: {t.text}; background: {field}; border: 1px solid {t.border}; border-radius: 9px; padding: 4px 2px; font-size: 9pt; font-weight: 700; }}
    QToolButton#sidePresetButton:hover {{ background: {shade}; border-color: {t.accent}; }}
    QToolButton#sidePresetButton:checked {{ color: {t.accent_light}; background: {shade}; border: 1px solid {t.accent}; }}
    QToolButton#sidePresetButton:checked {{ border: 2px solid {t.accent}; }}
    QToolButton#destinationButton {{ color: {t.text}; background: {field}; border: 1px solid {t.border}; border-radius: 11px; padding: 9px 5px; font-size: 10pt; font-weight: 600; }}
    QToolButton#destinationButton:hover {{ background: {shade}; border-color: {t.accent}; }}
    QToolButton#destinationButton:checked {{ color: {t.accent_light}; background: {shade}; border: 1px solid {t.accent}; }}

    QPushButton {{ background: {field}; color: {t.text}; border: 1px solid {t.border}; border-radius: 9px; padding: 8px 12px; }}
    QPushButton:hover {{ background: {shade}; border-color: {t.accent}; }}
    QPushButton:pressed {{ background: {t.surface}; }}
    QPushButton:focus {{ border: 1px solid {t.focus}; }}
    QPushButton:disabled {{ color: {t.muted}; background: {t.surface}; border-color: {t.border}; }}
    QPushButton#primaryButton {{ color: {t.accent_text}; font-weight: 700; border: 1px solid {t.accent}; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t.accent}, stop:1 {t.accent_light}); }}
    QPushButton#primaryButton:hover {{ background: {t.accent_light}; border-color: {t.accent_light}; }}
    QPushButton#primaryButton:pressed {{ background: {t.accent}; }}
    QPushButton#primaryButton:disabled {{ color: {t.muted}; background: {t.surface}; border: 1px solid {t.border}; }}
    QPushButton#subtleButton {{ color: {t.accent_light}; background: transparent; border: 1px solid {t.border}; padding: 6px 10px; }}
    QPushButton#subtleButton:hover {{ background: {shade}; border-color: {t.accent}; }}
    QPushButton#ghostButton {{ color: {t.muted}; background: transparent; border: 1px solid transparent; padding: 6px 10px; }}
    QPushButton#ghostButton:hover {{ color: {t.accent_light}; border-color: {t.border}; background: {shade}; }}
    QPushButton#dangerButton {{ color: {t.error}; background: transparent; border: 1px solid {t.border}; }}
    QPushButton#dangerButton:hover {{ border-color: {t.error}; background: {shade}; }}
    QPushButton#importButton {{ text-align: left; padding: 10px 12px; }}
    QPushButton#footerEditButton {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 4px; }}
    QPushButton#footerEditButton:hover {{ background: {shade}; border-color: {t.accent}; }}

    QComboBox, QLineEdit, QSpinBox, QAbstractSpinBox {{ background: {field}; border: 1px solid {t.border}; border-radius: 8px;
        padding: 6px 8px; color: {t.text}; min-height: 22px; selection-background-color: {t.selected}; }}
    QComboBox:hover, QLineEdit:hover, QSpinBox:hover {{ border-color: {t.accent}; }}
    QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{ border: 1px solid {t.focus}; }}
    QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled {{ color: {t.muted}; background: {t.surface}; }}
    QComboBox::drop-down {{ border: 0; width: 18px; }}
    QComboBox QAbstractItemView {{ background: {t.card}; color: {t.text}; border: 1px solid {t.border};
        selection-background-color: {t.selected}; selection-color: {t.text}; outline: 0; }}
    QMenu {{ background: {t.card}; color: {t.text}; border: 1px solid {t.border}; padding: 5px; }}
    QMenu::item {{ padding: 7px 16px; border-radius: 5px; }}
    QMenu::item:selected {{ color: {t.accent_light}; background: {shade}; }}
    QCheckBox {{ color: {t.text}; spacing: 8px; }}
    QCheckBox:hover {{ color: {t.accent_light}; }}
    QCheckBox:focus {{ color: {t.accent_light}; }}
    QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {t.border}; border-radius: 4px; background: {field}; }}
    QCheckBox::indicator:hover {{ border-color: {t.accent}; }}
    QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; }}
    QCheckBox::indicator:disabled {{ background: {t.surface}; }}

    QSlider::groove:horizontal {{ height: 4px; background: {t.groove}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {t.accent_light}; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px; }}
    QSlider::handle:horizontal:hover {{ background: {t.accent}; }}
    QSlider:disabled {{ color: {t.muted}; }}
    QSlider::sub-page:horizontal:disabled {{ background: {t.groove}; }}
    QSlider::handle:horizontal:disabled {{ background: {t.border}; }}

    QProgressBar {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; border-radius: 7px;
        text-align: center; min-height: 16px; font-size: 9pt; }}
    QProgressBar::chunk {{ background: {t.accent}; border-radius: 6px; }}
    QProgressBar[tone="success"]::chunk {{ background: {t.accent_light}; }}
    QProgressBar[tone="error"]::chunk {{ background: {t.error}; }}

    QScrollArea, QScrollArea#leftScroll, QScrollArea#workspaceScroll {{ background: {t.background}; border: 0; }}
    QWidget#leftViewport, QWidget#workspaceViewport, QWidget#scrollContent, QWidget#workspaceContent {{ background: {t.background}; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; border-radius: 5px; }}
    QScrollBar::handle:vertical {{ background: {t.scrollbar}; min-height: 34px; border-radius: 5px; }}
    QScrollBar::handle:vertical:hover {{ background: {t.accent}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ background: transparent; height: 0px; }}
    QSplitter::handle {{ background: transparent; }}
    QSplitter::handle:horizontal {{ width: 6px; }}
    QSplitter::handle:horizontal:hover {{ background: transparent; }}
    QToolTip {{ color: {t.text}; background: {t.card}; border: 1px solid {t.border}; padding: 5px; }}
    QDialog {{ background: {t.background}; color: {t.text}; }}
    QMenu {{ background: {t.card}; color: {t.text}; border: 1px solid {t.border}; }}
    QMenu::item:selected {{ background: {t.selected}; }}
    """
