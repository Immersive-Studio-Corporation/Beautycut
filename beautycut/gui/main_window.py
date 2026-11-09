from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time

from PySide6.QtCore import (
    QPointF,
    QSize,
    QSettings,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QIcon, QIntValidator, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QBoxLayout,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QMenu,
    QSlider,
    QSplitter,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..media.export import (
    AUDIO_FORMATS,
    CONTAINERS,
    PROFILES,
    VIDEO_FORMATS,
    ExportSettings,
    ExportProgress,
    usable_audio_codecs,
    usable_video_codecs,
    split_output_paths,
)
from ..media.probe import MEDIA_EXTENSIONS, MediaInfo, codec_label
from ..media.tools import find_tool
from ..updater import DEFAULT_UPDATE_MANIFEST_URL, UpdateManifest
from .themes import THEMES, THEME_BY_KEY, Theme
from .style import build_stylesheet
from .widgets import CollapsibleSection, ExportDock, PreviewPanel, SmoothScrollArea, StatusPill, tinted_icon
from .workers import (
    AnalyzeMediaThread,
    CheckForUpdateThread,
    DownloadUpdateThread,
    ExportMediaThread,
    PreviewMediaThread,
)


def _asset_path(relative: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / "assets" / relative


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if value < 1000 or unit == "To":
            return f"{value:.1f} {unit}" if unit != "o" else f"{int(value)} o"
        value /= 1000
    return f"{value:.1f} To"


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "Durée inconnue"
    whole = int(seconds)
    hours, rem = divmod(whole, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02}:{minutes:02}:{secs:02}" if hours else f"{minutes:02}:{secs:02}"


def _brand_mark_pixmap(width: int, height: int) -> QPixmap:
    """Return the transparent logo artwork cropped to its visible bounds."""
    mark = QPixmap(str(_asset_path("beautycut_mark.png")))
    # The source square reserves space for a square badge, while the actual
    # BeautyCut mark is a wide play-frame. Crop the transparent padding so it
    # reads at the same size as the reference header and export bar.
    artwork = mark.copy(10, 50, 234, 160)
    return artwork.scaled(
        width, height, Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _card(title: str, icon_path: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 18)
    layout.setSpacing(12)
    heading = QLabel(title)
    heading.setObjectName("cardTitle")
    heading.setWordWrap(True)
    heading.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    if icon_path:
        title_row = QWidget()
        title_layout = QHBoxLayout(title_row)
        title_layout.setContentsMargins(11, 0, 0, 0)
        title_layout.setSpacing(14)
        icon = QLabel()
        icon.setObjectName("profileHeadingIcon")
        icon.setProperty("iconPath", icon_path)
        icon.setFixedSize(28, 28)
        title_layout.addWidget(icon)
        title_layout.addWidget(heading)
        layout.addWidget(title_row)
    else:
        layout.addWidget(heading)
    return frame, layout


def _set_tone(widget: QWidget, tone: str) -> None:
    """Refresh a theme-driven semantic color after changing a Qt property."""
    if widget.property("tone") == tone:
        return
    widget.setProperty("tone", tone)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class BeautyHeader(QWidget):
    """Bandeau de marque avec une vague lumineuse accordée au thème actif."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("headerBar")
        self._theme = THEME_BY_KEY["deep_ocean"]
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width = self.width()
        height = self.height()
        if width <= 0 or height <= 0:
            return
        wave = QPainterPath(QPointF(width * 0.24, height * 0.98))
        wave.cubicTo(width * 0.37, height * 0.78, width * 0.42, height * 0.83, width * 0.52, height * 0.49)
        wave.cubicTo(width * 0.63, height * 0.15, width * 0.70, height * 0.02, width * 0.82, height * 0.12)
        wave.cubicTo(width * 0.91, height * 0.18, width * 0.96, height * 0.34, width, height * 0.41)
        fill = QPainterPath(wave)
        fill.lineTo(width, height)
        fill.lineTo(width * 0.24, height)
        fill.closeSubpath()
        glow = QColor(self._theme.accent)
        glow.setAlpha(16 if self._theme.light else 20)
        painter.fillPath(fill, glow)
        edge = QColor(self._theme.accent_light)
        edge.setAlpha(18 if not self._theme.light else 12)
        painter.setPen(QPen(edge, 7))
        painter.drawPath(wave)
        edge.setAlpha(115 if not self._theme.light else 80)
        painter.setPen(QPen(edge, 1.2))
        painter.drawPath(wave)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BeautyCut")
        self.setMinimumSize(720, 460)
        self.resize(1340, 860)
        self.setAcceptDrops(True)
        self.info: MediaInfo | None = None
        self.inventory = None
        self.report = None
        self._auto_output_path = True
        self._output_directory: Path | None = None
        self.selected_profile = "vertical"
        self._custom_ratio = 1080 / 1920
        self.analysis_thread: AnalyzeMediaThread | None = None
        self.preview_thread: PreviewMediaThread | None = None
        self.export_thread: ExportMediaThread | None = None
        self.generation = 0
        self._latest_export_progress: ExportProgress | None = None
        self._export_started_at: float | None = None
        self.update_check_thread: CheckForUpdateThread | None = None
        self.update_download_thread: DownloadUpdateThread | None = None
        self._available_update: UpdateManifest | None = None
        self._downloaded_installer: Path | None = None
        self._automatic_update_flow = False
        self._install_after_update_download = False
        self._update_auto_check_scheduled = False
        self._active_destination: str | None = None
        self._media_thumbnail_has_image = False
        saved_theme = str(self._settings_store().value("appearance/theme", "deep_ocean"))
        self.theme_key = saved_theme if saved_theme in THEME_BY_KEY else THEMES[0].key
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(220)
        self._preview_timer.timeout.connect(self._request_preview)
        self._export_timer = QTimer(self)
        self._export_timer.setInterval(1000)
        self._export_timer.timeout.connect(self._refresh_export_metrics)
        self._build_ui()
        self._apply_style()
        self._apply_responsive_layout()
        self.setWindowIcon(QIcon(str(_asset_path("beautycut_mark.ico"))))
        self._refresh_codecs()
        self._refresh_engine_status()
        self._update_output_format_badge()
        self._quality_preset_changed()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self._apply_responsive_layout()
        if not getattr(self, "_initial_splitter_layout_done", False):
            QTimer.singleShot(0, self._apply_initial_splitter_sizes)
        if not self._update_auto_check_scheduled and QApplication.platformName() != "offscreen":
            self._update_auto_check_scheduled = True
            QTimer.singleShot(2800, self._automatic_update_check)
        if sys.platform != "win32" or QApplication.platformName() == "offscreen":
            return
        try:
            import ctypes

            hwnd = ctypes.c_void_p(int(self.winId()))
            enabled = ctypes.c_int(1)
            dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
            for attribute in (20, 19):
                result = dwm(hwnd, attribute, ctypes.byref(enabled), ctypes.sizeof(enabled))
                if result == 0:
                    break
        except (AttributeError, OSError):
            pass

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())

        body = QWidget()
        body_row = QHBoxLayout(body)
        body_row.setContentsMargins(0, 6, 14, 0)
        body_row.setSpacing(12)
        self.navigation_rail = self._build_navigation_rail()
        body_row.addWidget(self.navigation_rail)
        self.content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.content_splitter.setObjectName("contentSplitter")
        self.content_splitter.setHandleWidth(22)
        self.content_splitter.setChildrenCollapsible(False)
        left = self._build_left_panel()
        left.setMinimumWidth(265)
        left.setMaximumWidth(410)
        self.content_splitter.addWidget(left)
        self.workspace_scroll = SmoothScrollArea()
        self.workspace_scroll.setObjectName("workspaceScroll")
        self.workspace_scroll.setWidgetResizable(True)
        self.workspace_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.workspace_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.workspace_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.workspace_scroll.viewport().setObjectName("workspaceViewport")
        self.workspace_scroll.setWidget(self._build_workspace())
        self.workspace_scroll.setMinimumWidth(300)
        self.content_splitter.addWidget(self.workspace_scroll)
        saved_sidebar_width = int(self._settings_store().value("layout/sidebar_width", 344))
        saved_sidebar_width = max(265, min(410, saved_sidebar_width))
        self._saved_sidebar_width = saved_sidebar_width
        self.content_splitter.setSizes([
            saved_sidebar_width,
            max(300, self.width() - saved_sidebar_width - self.content_splitter.handleWidth() - 150),
        ])
        self.content_splitter.splitterMoved.connect(
            lambda position, _index: self._settings_store().setValue("layout/sidebar_width", position)
        )
        self.content_splitter.splitterMoved.connect(lambda *_args: self._apply_responsive_layout())
        body_row.addWidget(self.content_splitter, 1)
        outer.addWidget(body, 1)
        outer.addWidget(self._build_export_dock())
        self.setCentralWidget(root)

    def _apply_initial_splitter_sizes(self) -> None:
        if getattr(self, "_initial_splitter_layout_done", False):
            return
        available = self.content_splitter.width()
        if available <= 0:
            return
        desired = 265 if self.width() < 920 else self._saved_sidebar_width
        desired = max(265, min(410, desired))
        self.content_splitter.setSizes([
            desired, max(300, available - self.content_splitter.handleWidth() - desired)
        ])
        self._initial_splitter_layout_done = True

    def _build_navigation_rail(self) -> QWidget:
        rail = QFrame()
        rail.setObjectName("navigationRail")
        rail.setFixedWidth(98)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(0, 9, 0, 9)
        layout.setSpacing(7)
        self.navigation_rail_layout = layout
        self.navigation_buttons: dict[str, QToolButton] = {}
        self.navigation_group = QButtonGroup(self)
        self.navigation_group.setExclusive(True)
        entries = (
            ("home", "Accueil", "home.svg"),
            ("themes", "Thèmes", "palette.svg"),
            ("about", "À propos", "info.svg"),
            ("settings", "Réglages", "settings.svg"),
        )
        for key, label, icon_name in entries:
            button = QToolButton()
            button.setObjectName("navButton")
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(22, 22))
            button.setText(label)
            button.setMinimumHeight(88 if key == "home" else 80)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(label)
            button.setProperty("iconPath", f"icons/{icon_name}")
            button.clicked.connect(lambda checked=False, item=key: self._navigate(item))
            self.navigation_group.addButton(button)
            self.navigation_buttons[key] = button
            layout.addWidget(button)
        layout.addStretch(1)
        self.navigation_buttons["home"].setChecked(True)
        return rail

    def _build_header(self) -> QWidget:
        header = BeautyHeader()
        self.header_bar = header
        header.setFixedHeight(90)
        self.header_layout = QVBoxLayout(header)
        self.header_layout.setContentsMargins(22, 8, 77, 8)
        self.header_layout.setSpacing(4)
        self.header_top_row = QHBoxLayout()
        self.header_top_row.setContentsMargins(0, 0, 0, 0)
        self.header_top_row.setSpacing(12)
        mark = QLabel()
        mark.setObjectName("brandMark")
        mark.setPixmap(_brand_mark_pixmap(73, 50))
        mark.setContentsMargins(7, 0, 0, 0)
        mark.setFixedSize(100, 72)
        names = QVBoxLayout()
        wordmark = QWidget()
        wordmark.setAccessibleName("BeautyCut")
        wordmark_row = QHBoxLayout(wordmark)
        wordmark_row.setContentsMargins(0, 0, 0, 0)
        wordmark_row.setSpacing(0)
        brand = QLabel()
        brand.setObjectName("brand")
        brand.setTextFormat(Qt.TextFormat.RichText)
        brand.setAccessibleName("BeautyCut")
        brand.setMinimumWidth(205)
        wordmark_row.addWidget(brand)
        self.brand_label = brand
        subtitle = QLabel("Transformez vos vidéos, en local")
        subtitle.setObjectName("muted")
        names.addWidget(wordmark)
        names.addWidget(subtitle)
        identity = QWidget()
        identity_row = QHBoxLayout(identity)
        identity_row.setContentsMargins(0, 0, 0, 0)
        identity_row.setSpacing(0)
        identity_row.addWidget(mark)
        identity_row.addLayout(names)
        self.header_top_row.addWidget(identity)
        self.header_top_row.addStretch(1)
        self.header_controls = QWidget()
        controls = QHBoxLayout(self.header_controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(10)
        self.theme_combo = QComboBox()
        self.theme_combo.setObjectName("themeCombo")
        self.theme_combo.setAccessibleName("Thème visuel de BeautyCut")
        self.theme_combo.setToolTip("Choisir parmi 20 thèmes nature, océan et cosmos")
        self.theme_combo.setMinimumWidth(210)
        self.theme_combo.setMaximumWidth(230)
        for theme in THEMES:
            self.theme_combo.addItem(
                tinted_icon("icons/palette.svg", theme.accent_light, 18), f"Thème : {theme.name}", theme.key
            )
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(self.theme_key))
        self.theme_combo.currentIndexChanged.connect(self._theme_selected)
        controls.addWidget(self.theme_combo)
        self.engine_badge = StatusPill("Moteur vidéo · vérification…", "info")
        self.engine_badge.setObjectName("engineBadge")
        self.engine_badge.setMinimumWidth(245)
        self.engine_badge.setMaximumHeight(38)
        self.engine_badge.setAccessibleName("État du moteur vidéo FFmpeg")
        self.header_divider = QFrame()
        self.header_divider.setObjectName("headerDivider")
        self.header_divider.setFrameShape(QFrame.Shape.VLine)
        self.header_divider.setFixedSize(1, 36)
        controls.addWidget(self.header_divider)
        controls.addWidget(self.engine_badge)
        self.header_top_row.addWidget(self.header_controls)
        self.header_layout.addLayout(self.header_top_row)
        return header

    def _build_left_panel(self) -> QWidget:
        scroll = SmoothScrollArea()
        self.left_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setObjectName("leftScroll")
        scroll.viewport().setObjectName("leftViewport")
        panel = QWidget()
        panel.setObjectName("scrollContent")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 9, 0, 4)
        layout.setSpacing(3)
        self.sections: dict[str, CollapsibleSection] = {}

        def section(
            title: str,
            key: str,
            default_open: bool = False,
            icon_path: str = "",
            summary: str = "",
        ):
            saved = self._settings_store().value(f"layout/section/{key}", default_open)
            if isinstance(saved, str):
                expanded = saved.strip().lower() in {"1", "true", "yes"}
            else:
                expanded = bool(saved)
            widget = CollapsibleSection(title, expanded=expanded, icon=icon_path, summary=summary)
            if icon_path:
                widget.header.setProperty("iconPath", icon_path)
            widget.expandedChanged.connect(
                lambda value, section_key=key: self._settings_store().setValue(
                    f"layout/section/{section_key}", value
                )
            )
            self.sections[key] = widget
            return widget, widget.content_layout()

        media_section, source = section("Média", "media", True, "icons/media.svg")
        self.media_section = media_section
        self.import_button = QPushButton("Importer un média")
        self.import_button.setObjectName("primaryButton")
        self.import_button.setMinimumHeight(40)
        self.import_button.setToolTip("Ouvrir un fichier vidéo ou audio, ou le glisser-déposer dans BeautyCut.")
        self.import_button.clicked.connect(self._choose_file)

        media_row = QWidget()
        media_row_layout = QHBoxLayout(media_row)
        media_row_layout.setContentsMargins(0, 0, 0, 0)
        media_row_layout.setSpacing(10)
        self.media_thumbnail = QLabel()
        self.media_thumbnail.setObjectName("mediaThumbnail")
        self.media_thumbnail.setFixedSize(96, 66)
        self.media_thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        media_row_layout.addWidget(self.media_thumbnail)
        media_text = QVBoxLayout()
        media_text.setContentsMargins(0, 0, 0, 0)
        media_text.setSpacing(4)
        self.media_name = QLabel("Aucun média importé")
        self.media_name.setObjectName("mediaName")
        self.media_name.setWordWrap(True)
        self.media_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        media_text.addWidget(self.media_name)
        self.media_details = QLabel("Les détails apparaîtront après import.")
        self.media_details.setObjectName("muted")
        self.media_details.setWordWrap(True)
        self.media_details.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        media_text.addWidget(self.media_details)
        media_text.addStretch(1)
        media_row_layout.addLayout(media_text, 1)
        source.addWidget(media_row)
        source.addWidget(self.import_button)
        self.audio_tracks = QLabel("")
        self.audio_tracks.setObjectName("trackDetails")
        self.audio_tracks.setWordWrap(True)
        source.addWidget(self.audio_tracks)
        self.compatibility = QLabel("L’importation analyse les pistes et les codecs avec FFprobe.")
        self.compatibility.setObjectName("statusInfo")
        self.compatibility.setWordWrap(True)
        self.compatibility.setVisible(False)
        source.addWidget(self.compatibility)
        layout.addWidget(media_section)
        layout.addSpacing(7)

        crop_section, crop = section("Cadrage", "framing", True, "icons/crop.svg")
        crop.setSpacing(11)
        self.crop_note = QLabel("Déplacez le cadre dans l’aperçu pour ajuster le recadrage.")
        self.crop_note.setObjectName("muted")
        self.crop_note.setWordWrap(True)
        self.crop_note.setToolTip("Les réglages de cadrage sont repris à l’export. Utilisez les positions rapides ou les curseurs si nécessaire.")
        crop_profiles = QHBoxLayout()
        self.crop_profile_layout = crop_profiles
        crop_profiles.setSpacing(5)
        self.crop_profile_buttons: dict[str, QToolButton] = {}
        self.crop_profile_group = QButtonGroup(self)
        self.crop_profile_group.setExclusive(True)
        crop_profile_data = (
            ("vertical", "9:16", "icons/vertical.svg", "Vertical 9:16"),
            ("portrait", "4:5", "icons/portrait.svg", "Portrait 4:5"),
            ("square", "1:1", "icons/square.svg", "Carré 1:1"),
            ("landscape", "16:9", "icons/landscape.svg", "Paysage 16:9"),
        )
        for key, label, icon_path, accessible in crop_profile_data:
            button = QToolButton()
            button.setObjectName("sidePresetButton")
            button.setText(label)
            button.setIcon(QIcon(str(_asset_path(icon_path))))
            button.setProperty("iconPath", icon_path)
            button.setIconSize(QSize(18, 18))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setCheckable(True)
            button.setMinimumSize(46, 78)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            button.setAccessibleName(f"Format de cadrage {accessible}")
            button.clicked.connect(lambda checked=False, item=key: self._select_profile(item))
            self.crop_profile_group.addButton(button)
            self.crop_profile_buttons[key] = button
            crop_profiles.addWidget(button, 1)
        self.quick_position_button = QToolButton()
        self.quick_position_button.setObjectName("sidePresetButton")
        self.quick_position_button.setText("Plus")
        self.quick_position_button.setIcon(QIcon(str(_asset_path("icons/more.svg"))))
        self.quick_position_button.setIconSize(QSize(18, 18))
        self.quick_position_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.quick_position_button.setMinimumSize(46, 72)
        self.quick_position_button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.quick_position_button.setAccessibleName("Autres options de cadrage")
        crop_profiles.addWidget(self.quick_position_button, 1)
        crop.addLayout(crop_profiles)
        self.fit_mode_combo = QComboBox()
        self.fit_mode_combo.addItem("Remplir · recadrer les bords", "fill")
        self.fit_mode_combo.addItem("Tout conserver · bandes noires", "contain")
        self.fit_mode_combo.currentIndexChanged.connect(self._framing_mode_changed)
        zoom_row = QHBoxLayout()
        zoom_label = QLabel("Zoom")
        self.zoom_value = QLabel("100 %")
        self.zoom_value.setObjectName("accentText")
        zoom_row.addWidget(zoom_label)
        zoom_row.addStretch(1)
        zoom_row.addWidget(self.zoom_value)
        crop.addLayout(zoom_row)
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(100, 200)
        self.zoom_slider.setSingleStep(5)
        self.zoom_slider.setPageStep(10)
        self.zoom_slider.valueChanged.connect(self._zoom_changed)
        crop.addWidget(self.zoom_slider)

        self.quick_position_menu = QMenu(self.quick_position_button)
        self.quick_position_actions: dict[str, object] = {}
        custom_action = self.quick_position_menu.addAction("Format personnalisé…")
        custom_action.triggered.connect(lambda: self._select_profile("custom"))
        self.quick_position_menu.addSeparator()
        fit_action = self.quick_position_menu.addAction("Remplir · recadrer les bords")
        fit_action.triggered.connect(lambda: self.fit_mode_combo.setCurrentIndex(self.fit_mode_combo.findData("fill")))
        contain_action = self.quick_position_menu.addAction("Tout conserver · bandes noires")
        contain_action.triggered.connect(lambda: self.fit_mode_combo.setCurrentIndex(self.fit_mode_combo.findData("contain")))
        self.quick_position_menu.addSeparator()
        self.guides_menu_action = self.quick_position_menu.addAction("Afficher les repères de cadrage")
        self.guides_menu_action.setCheckable(True)
        self.guides_menu_action.toggled.connect(self._toggle_preview_guides)
        self.quick_position_menu.addSeparator()
        position_menu = self.quick_position_menu.addMenu("Position rapide")
        anchors = (
            ("Haut gauche", 0, 0), ("Haut centré", 50, 0), ("Haut droite", 100, 0),
            ("Centre gauche", 0, 50), ("Centre", 50, 50), ("Centre droite", 100, 50),
            ("Bas gauche", 0, 100), ("Bas centré", 50, 100), ("Bas droite", 100, 100),
        )
        for label, x, y in anchors:
            action = position_menu.addAction(label)
            action.triggered.connect(
                lambda checked=False, crop_x=x, crop_y=y: self._set_crop_position(crop_x, crop_y)
            )
            self.quick_position_actions[label] = action
        self.quick_position_button.setMenu(self.quick_position_menu)
        self.quick_position_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.center_crop_button = QPushButton("Réinitialiser")
        self.center_crop_button.setObjectName("subtleButton")
        self.center_crop_button.setIcon(QIcon(str(_asset_path("icons/reset.svg"))))
        self.center_crop_button.clicked.connect(self._reset_framing)
        self.x_slider = self._add_slider(crop, "Position X", "cropXValue", vertical=False)
        self.y_slider = self._add_slider(crop, "Position Y", "cropYValue", vertical=True)
        self.center_crop_button.setMinimumHeight(36)
        crop.addWidget(self.center_crop_button)
        layout.addWidget(crop_section)

        settings_section, settings = section(
            "Qualité et export", "quality", False, "icons/sliders.svg",
            "Qualité, vitesse et options d’export",
        )
        settings_section.setMinimumHeight(56)
        self.format_details = QLabel("Vertical · 9:16 · TikTok · Reels · Shorts")
        self.format_details.setObjectName("formatDetails")
        settings.addWidget(self.format_details)
        destination_label = QLabel("Préréglage de destination")
        destination_label.setObjectName("fieldLabel")
        settings.addWidget(destination_label)
        self.destination_combo = QComboBox()
        self.destination_combo.addItem("Choisir une destination…", "")
        destination_options = (
            ("TikTok · vertical 9:16", "tiktok", "vertical"),
            ("Instagram Reels · vertical 9:16", "instagram_reels", "vertical"),
            ("YouTube Shorts · vertical 9:16", "youtube_shorts", "vertical"),
            ("Instagram Stories · vertical 9:16", "instagram_stories", "vertical"),
            ("Instagram · portrait 4:5", "instagram_portrait", "portrait"),
            ("Publication carrée · 1:1", "square_post", "square"),
            ("YouTube classique · paysage 16:9", "youtube_landscape", "landscape"),
            ("Format personnalisé", "custom", "custom"),
        )
        for label, key, profile in destination_options:
            self.destination_combo.addItem(label, profile)
            self.destination_combo.setItemData(
                self.destination_combo.count() - 1, key, Qt.ItemDataRole.UserRole + 1
            )
        self.destination_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.destination_combo.setMinimumContentsLength(14)
        self.destination_combo.setAccessibleName("Préréglage de destination")
        self.destination_combo.currentIndexChanged.connect(self._destination_changed)
        settings.addWidget(self.destination_combo)

        self.custom_size_widget = QWidget()
        custom_row = QHBoxLayout()
        custom_row.setContentsMargins(0, 0, 0, 0)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(16, 7680)
        self.width_spin.setSingleStep(2)
        self.width_spin.setValue(1080)
        self.width_spin.setSuffix(" px")
        self.height_spin = QSpinBox()
        self.height_spin.setRange(16, 7680)
        self.height_spin.setSingleStep(2)
        self.height_spin.setValue(1920)
        self.height_spin.setSuffix(" px")
        self.width_spin.setMaximumWidth(132)
        self.height_spin.setMaximumWidth(132)
        self.width_spin.valueChanged.connect(self._custom_width_changed)
        self.height_spin.valueChanged.connect(self._custom_height_changed)
        custom_row.addWidget(self.width_spin)
        custom_row.addWidget(self.height_spin)
        custom_row.addStretch(1)
        custom_layout = QVBoxLayout()
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.addLayout(custom_row)
        self.lock_ratio_checkbox = QCheckBox("Conserver le ratio")
        self.lock_ratio_checkbox.setChecked(True)
        self.lock_ratio_checkbox.toggled.connect(self._ratio_lock_changed)
        custom_layout.addWidget(self.lock_ratio_checkbox)
        self.custom_size_widget.setLayout(custom_layout)
        self.custom_size_widget.setVisible(False)
        settings.addWidget(self.custom_size_widget)

        settings.addWidget(QLabel("Vitesse d’export"))
        self.speed_combo = QComboBox()
        self.speed_combo.addItem("Rapide · recommandé pour longues vidéos", "fast")
        self.speed_combo.addItem("Équilibré · taille et vitesse", "balanced")
        self.speed_combo.addItem("Qualité / compression maximale", "quality")
        self.speed_combo.currentIndexChanged.connect(self._settings_changed)
        settings.addWidget(self.speed_combo)

        settings.addWidget(QLabel("Préréglage de qualité"))
        self.quality_preset_combo = QComboBox()
        self.quality_preset_combo.addItem("Recommandé · équilibré", "recommended")
        self.quality_preset_combo.addItem("Haute qualité", "high")
        self.quality_preset_combo.addItem("Fichier plus léger", "compact")
        self.quality_preset_combo.addItem("Personnalisé", "custom")
        self.quality_preset_combo.currentIndexChanged.connect(self._quality_preset_changed)
        settings.addWidget(self.quality_preset_combo)
        quality_row = QHBoxLayout()
        quality_row.addWidget(QLabel("Qualité vidéo"))
        self.container_combo = QComboBox()
        self.container_combo.addItems(CONTAINERS.keys())
        self.container_combo.currentTextChanged.connect(self._container_changed)
        self.video_codec_combo = QComboBox()
        self.audio_codec_combo = QComboBox()
        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(0, 51)
        self.quality_spin.setValue(22)
        self.quality_spin.setToolTip("Valeur CRF approximative : plus le nombre est bas, meilleure est la qualité.")
        self.quality_spin.setEnabled(False)
        quality_row.addStretch(1)
        quality_row.addWidget(self.quality_spin)
        settings.addLayout(quality_row)

        self.segment_checkbox = QCheckBox("Découper les vidéos trop longues")
        self.segment_checkbox.setToolTip("Créer plusieurs fichiers numérotés selon la durée choisie pour le réseau cible.")
        settings.addWidget(self.segment_checkbox)
        self.segment_options = QWidget()
        segment_layout = QVBoxLayout(self.segment_options)
        segment_layout.setContentsMargins(0, 0, 0, 0)
        segment_layout.setSpacing(7)
        self.segment_target_combo = QComboBox()
        self.segment_target_combo.addItem("Réseaux sociaux", None)
        self.segment_target_combo.model().item(0).setEnabled(False)
        self.segment_target_combo.addItem("TikTok · parties de 15 s", 15)
        self.segment_target_combo.addItem("TikTok · parties de 30 s", 30)
        self.segment_target_combo.addItem("TikTok · parties de 1 min", 60)
        self.segment_target_combo.addItem("TikTok · 3 min, sûr pour tous", 180)
        self.segment_target_combo.addItem("TikTok · 5 min, comptes éligibles", 300)
        self.segment_target_combo.addItem("TikTok · 10 min, comptes éligibles", 600)
        self.segment_target_combo.addItem("TikTok · 60 min, import mobile variable", 3600)
        self.segment_target_combo.addItem("Instagram Reels · parties de 15 s", 15)
        self.segment_target_combo.addItem("Instagram Reels · parties de 30 s", 30)
        self.segment_target_combo.addItem("Instagram Reels · parties de 1 min", 60)
        self.segment_target_combo.addItem("Instagram Reels · 3 min découverte", 180)
        self.segment_target_combo.addItem("Instagram Reels · 20 min max indicatif", 1200)
        self.segment_target_combo.addItem("YouTube Shorts · parties de 15 s", 15)
        self.segment_target_combo.addItem("YouTube Shorts · parties de 30 s", 30)
        self.segment_target_combo.addItem("YouTube Shorts · parties de 1 min", 60)
        self.segment_target_combo.addItem("YouTube Shorts · 3 min max", 180)
        self.segment_target_combo.addItem("Durées courantes pour les autres usages", None)
        self.segment_target_combo.model().item(self.segment_target_combo.count() - 1).setEnabled(False)
        for seconds in (1, 2, 5, 10, 15, 20, 30, 45, 60, 90, 120):
            self.segment_target_combo.addItem(f"Découpage · {seconds} s par partie", seconds)
        for minutes in (1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360, 480, 720, 1440):
            label = f"{minutes // 60} h" if minutes >= 60 and minutes % 60 == 0 else f"{minutes} min"
            self.segment_target_combo.addItem(f"Découpage · {label} par partie", minutes * 60)
        self.segment_target_combo.addItem("Durée personnalisée", None)
        self.segment_target_combo.setMaxVisibleItems(12)
        self.segment_target_combo.setCurrentIndex(4)
        self.segment_target_combo.setToolTip("Les limites disponibles dépendent parfois du compte et du mode d’import.")
        self.segment_target_combo.currentIndexChanged.connect(self._segment_target_changed)
        segment_layout.addWidget(self.segment_target_combo)
        limit_row = QHBoxLayout()
        limit_row.addWidget(QLabel("Durée cible (min + s)"))
        self.segment_limit_minutes_spin = QSpinBox()
        self.segment_limit_minutes_spin.setRange(0, 1440)
        self.segment_limit_minutes_spin.setValue(3)
        self.segment_limit_minutes_spin.setSuffix(" min")
        self.segment_limit_minutes_spin.setMaximumWidth(94)
        self.segment_limit_minutes_spin.valueChanged.connect(self._segment_minutes_changed)
        self.segment_limit_seconds_spin = QSpinBox()
        self.segment_limit_seconds_spin.setRange(0, 59)
        self.segment_limit_seconds_spin.setSuffix(" s")
        self.segment_limit_seconds_spin.setMaximumWidth(78)
        self.segment_limit_seconds_spin.valueChanged.connect(self._settings_changed)
        limit_row.addStretch(1)
        limit_row.addWidget(self.segment_limit_minutes_spin)
        limit_row.addWidget(self.segment_limit_seconds_spin)
        segment_layout.addLayout(limit_row)
        self.segment_hint = QLabel("Réglez les minutes et les secondes, ou choisissez un préréglage. Les limites des réseaux varient selon le compte ; une seconde de marge est gardée quand la durée le permet.")
        self.segment_hint.setObjectName("muted")
        self.segment_hint.setWordWrap(True)
        segment_layout.addWidget(self.segment_hint)
        self.segment_options.setVisible(False)
        settings.addWidget(self.segment_options)
        self.segment_checkbox.toggled.connect(self.segment_options.setVisible)
        self.segment_checkbox.toggled.connect(self._settings_changed)

        advanced_section, advanced = section(
            "Paramètres avancés", "advanced", False, "icons/settings.svg", "Options techniques"
        )
        advanced_section.setMinimumHeight(55)
        self.advanced_section = advanced_section
        self.advanced_panel = advanced_section.body
        advanced.setSpacing(8)

        self.bitrate_edit = QLineEdit()
        self.bitrate_edit.setPlaceholderText("Automatique selon qualité")
        self.bitrate_edit.setValidator(QIntValidator(64, 200000, self))
        self.bitrate_edit.setToolTip("Débit vidéo en kbit/s ; laisse vide pour utiliser le réglage de qualité.")
        self.fps_combo = QComboBox()
        self.fps_combo.addItem("Identique à la source", "")
        for fps in (24, 25, 30, 50, 60):
            self.fps_combo.addItem(f"{fps} images/s", str(fps))
        self.fps_combo.currentIndexChanged.connect(self._settings_changed)
        for label, widget in (
            ("Conteneur", self.container_combo), ("Codec vidéo", self.video_codec_combo),
            ("Codec audio", self.audio_codec_combo),
            ("Débit vidéo (kbit/s, facultatif)", self.bitrate_edit), ("Fréquence d’images", self.fps_combo),
        ):
            advanced.addWidget(QLabel(label))
            advanced.addWidget(widget)
        advanced.addWidget(QLabel("Remplissage de la sortie"))
        advanced.addWidget(self.fit_mode_combo)
        self.video_codec_combo.currentIndexChanged.connect(self._settings_changed)
        self.audio_codec_combo.currentIndexChanged.connect(self._settings_changed)
        self.video_codec_combo.currentTextChanged.connect(self._update_output_format_badge)
        self.audio_codec_combo.currentTextChanged.connect(self._update_output_format_badge)
        self.quality_spin.valueChanged.connect(self._quality_number_changed)
        self.bitrate_edit.textChanged.connect(self._settings_changed)
        layout.addWidget(settings_section)
        layout.addWidget(advanced_section)

        update_section, update = section(
            "Mise à jour automatique", "updates", False, "icons/refresh.svg", "Toujours à jour"
        )
        update_section.setMinimumHeight(54)
        update_help = QLabel("Au lancement, BeautyCut vérifie le manifeste public, puis télécharge et installe seul toute nouvelle version.")
        update_help.setObjectName("muted")
        update_help.setWordWrap(True)
        update.addWidget(update_help)
        self.update_manifest_edit = QLineEdit()
        self.update_manifest_edit.setText(self._settings_store().value("updates/manifest_url", DEFAULT_UPDATE_MANIFEST_URL))
        self.update_manifest_edit.setPlaceholderText("https://…/latest.json")
        self.update_manifest_edit.setToolTip("Adresse HTTPS publique du fichier latest.json")
        update.addWidget(self.update_manifest_edit)
        self.update_channel_save_button = QPushButton("Enregistrer cette adresse")
        self.update_channel_save_button.setObjectName("subtleButton")
        self.update_channel_save_button.clicked.connect(self._save_update_channel)
        update.addWidget(self.update_channel_save_button)
        self.update_status = QLabel("À chaque lancement : vérification, téléchargement sécurisé et installation automatique des nouvelles versions.")
        self.update_status.setObjectName("muted")
        self.update_status.setWordWrap(True)
        update.addWidget(self.update_status)
        self.update_progress = QProgressBar()
        self.update_progress.setRange(0, 100)
        self.update_progress.setValue(0)
        self.update_progress.setFormat("Téléchargement · %p %")
        self.update_progress.setVisible(False)
        update.addWidget(self.update_progress)
        self.update_check_button = QPushButton("Vérifier maintenant")
        self.update_check_button.clicked.connect(lambda: self._check_for_updates(manual=True))
        update.addWidget(self.update_check_button)
        self.update_download_button = QPushButton("Télécharger la mise à jour")
        self.update_download_button.setObjectName("primaryButton")
        self.update_download_button.setVisible(False)
        self.update_download_button.clicked.connect(self._download_available_update)
        update.addWidget(self.update_download_button)
        self.update_install_button = QPushButton("Installer et relancer BeautyCut")
        self.update_install_button.setObjectName("primaryButton")
        self.update_install_button.setVisible(False)
        self.update_install_button.clicked.connect(self._install_downloaded_update)
        update.addWidget(self.update_install_button)
        layout.addWidget(update_section)
        layout.addStretch(1)
        scroll.setWidget(panel)
        return scroll

    def _add_slider(self, layout: QVBoxLayout, label: str, value_object: str, *, vertical: bool) -> QSlider:
        row = QHBoxLayout()
        text = QLabel(label)
        value = QLabel("Centre")
        value.setObjectName("muted")
        value.setObjectName(value_object)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(50)

        def update_position(number: int) -> None:
            value.setText(self._position_label(number, vertical))
            self._settings_changed()

        slider.valueChanged.connect(update_position)
        row.addWidget(text)
        row.addStretch(1)
        row.addWidget(value)
        layout.addLayout(row)
        layout.addWidget(slider)
        return slider

    def _build_workspace(self) -> QWidget:
        workspace = QWidget()
        workspace.setObjectName("workspaceContent")
        workspace.setMinimumWidth(0)
        workspace.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        self.update_banner = QFrame()
        self.update_banner.setObjectName("updateBanner")
        banner_row = QHBoxLayout(self.update_banner)
        banner_row.setContentsMargins(16, 12, 12, 12)
        banner_copy = QVBoxLayout()
        self.update_banner_title = QLabel("Nouvelle version disponible")
        self.update_banner_title.setObjectName("updateBannerTitle")
        self.update_banner_notes = QLabel("")
        self.update_banner_notes.setObjectName("muted")
        self.update_banner_notes.setWordWrap(True)
        banner_copy.addWidget(self.update_banner_title)
        banner_copy.addWidget(self.update_banner_notes)
        self.update_banner_progress = QProgressBar()
        self.update_banner_progress.setRange(0, 0)
        self.update_banner_progress.setFormat("Vérification des mises à jour…")
        self.update_banner_progress.setVisible(False)
        banner_copy.addWidget(self.update_banner_progress)
        banner_row.addLayout(banner_copy, 1)
        self.update_banner_button = QPushButton("Télécharger")
        self.update_banner_button.setObjectName("primaryButton")
        self.update_banner_button.clicked.connect(self._update_banner_action)
        banner_row.addWidget(self.update_banner_button)
        self.update_banner.setVisible(False)
        layout.addWidget(self.update_banner)

        preview_card = QFrame()
        preview_card.setObjectName("previewWorkspace")
        preview_card.setMaximumHeight(503)
        preview_card.setMinimumHeight(503)
        self.preview_workspace = preview_card
        preview = QVBoxLayout(preview_card)
        preview.setContentsMargins(4, 1, 4, 12)
        preview.setSpacing(8)
        previews = QHBoxLayout()
        previews.setSpacing(18)
        self.preview_layout = previews
        self.original_preview = PreviewPanel(
            "Vidéo originale", "landscape", crop_interactive=True, icon_path="icons/eye.svg"
        )
        self.output_preview = PreviewPanel(
            "Résultat estimé", "landscape", icon_path="icons/check.svg", icon_tone="success"
        )
        self.original_preview.canvas.crop_position_changed.connect(self._crop_preview_position_changed)
        # A slightly wider output pane follows the reference composition.
        previews.addWidget(self.original_preview, 49)
        previews.addWidget(self.output_preview, 51)
        preview.addLayout(previews, 1)

        preview_actions = QHBoxLayout()
        preview_actions.setSpacing(8)
        self.preview_actions_layout = preview_actions
        self.grid_action_button = QToolButton()
        self.grid_action_button.setObjectName("previewActionButton")
        self.grid_action_button.setText("Grille")
        self.grid_action_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.grid_action_button.setIconSize(QSize(16, 16))
        self.grid_action_button.setProperty("iconPath", "icons/grid.svg")
        self.grid_action_button.setCheckable(True)
        self.grid_action_button.setChecked(True)
        self.grid_action_button.setToolTip("Afficher ou masquer les tiers de composition")
        self.grid_action_button.toggled.connect(self._toggle_preview_grid)
        self.grid_action_button.setMinimumSize(112, 38)
        preview_actions.addStretch(1)
        preview_actions.addWidget(self.grid_action_button)
        self.center_preview_button = QPushButton("Centrer")
        self.center_preview_button.setObjectName("previewActionButton")
        self.center_preview_button.setIconSize(QSize(16, 16))
        self.center_preview_button.setProperty("iconPath", "icons/center.svg")
        self.center_preview_button.setToolTip("Recentrer le cadrage et remettre les positions au milieu")
        self.center_preview_button.clicked.connect(self._center_crop)
        self.center_preview_button.setMinimumSize(124, 38)
        preview_actions.addWidget(self.center_preview_button)
        self.flip_button = QToolButton()
        self.flip_button.setObjectName("previewActionButton")
        self.flip_button.setText("Inverser")
        self.flip_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.flip_button.setIconSize(QSize(16, 16))
        self.flip_button.setProperty("iconPath", "icons/flip.svg")
        self.flip_button.setCheckable(True)
        self.flip_button.setAccessibleName("Inverser horizontalement la vidéo")
        self.flip_button.setToolTip("Applique une inversion horizontale à l’aperçu de sortie et à l’export")
        self.flip_button.toggled.connect(self._flip_changed)
        self.flip_button.setMinimumSize(128, 38)
        preview_actions.addWidget(self.flip_button)
        self.fullscreen_action_button = QPushButton("Plein écran")
        self.fullscreen_action_button.setObjectName("previewActionButton")
        self.fullscreen_action_button.setIconSize(QSize(16, 16))
        self.fullscreen_action_button.setProperty("iconPath", "icons/fullscreen.svg")
        self.fullscreen_action_button.clicked.connect(self.output_preview._open_fullscreen)
        self.fullscreen_action_button.setMinimumSize(145, 38)
        preview_actions.addWidget(self.fullscreen_action_button)
        self.reset_preview_button = QPushButton("Réinitialiser l’aperçu")
        self.reset_preview_button.setObjectName("subtleButton")
        self.reset_preview_button.setIconSize(QSize(16, 16))
        self.reset_preview_button.setProperty("iconPath", "icons/reset.svg")
        self.reset_preview_button.clicked.connect(self._reset_preview_views)
        self.reset_preview_button.setMinimumSize(178, 38)
        preview_actions.addStretch(1)
        preview_actions.addWidget(self.reset_preview_button)
        self.preview_action_widgets = (
            self.grid_action_button, self.center_preview_button, self.flip_button,
            self.fullscreen_action_button, self.reset_preview_button,
        )
        preview.addLayout(preview_actions)
        layout.addWidget(preview_card, 1)

        bottom_cards = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        bottom_cards.setSpacing(10)
        self.bottom_cards_layout = bottom_cards
        platform_card, platform = _card("Formats de sortie", "icons/media.svg")
        platform.setContentsMargins(18, 21, 18, 22)
        platform.setSpacing(4)
        self.output_formats_icon = platform_card.findChild(QLabel, "profileHeadingIcon")
        platform_row = QHBoxLayout()
        platform_row.setContentsMargins(4, 0, 4, 0)
        platform_row.setSpacing(7)
        self.profile_layout = platform_row
        self.profile_buttons: dict[str, QToolButton] = {}
        self.profile_button_group = QButtonGroup(self)
        self.profile_button_group.setExclusive(True)
        profiles = (
            ("vertical", "Vertical\n9:16", "icons/vertical.svg", "Vertical · TikTok · Reels · Shorts · Stories"),
            ("portrait", "Portrait\n4:5", "icons/portrait.svg", "Portrait · fil Instagram / Facebook"),
            ("square", "Carré\n1:1", "icons/square.svg", "Carré · publications sociales"),
            ("landscape", "Paysage\n16:9", "icons/landscape.svg", "Paysage · YouTube et vidéo classique"),
            ("custom", "Personnalisé", "icons/custom.svg", "Définir largeur et hauteur"),
        )
        profile_weights = (118, 96, 82, 84, 96)
        for (key, label, icon_path, description), weight in zip(profiles, profile_weights):
            button = QToolButton()
            button.setObjectName("presetButton")
            button.setText(label)
            button.setIcon(QIcon(str(_asset_path(icon_path))))
            button.setProperty("iconPath", icon_path)
            button.setIconSize(QSize(24, 24))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setCheckable(True)
            button.setMinimumSize(62, 102)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            button.setToolTip(description)
            button.setAccessibleName(f"Format {key}. {description}")
            button.clicked.connect(lambda checked=False, profile=key: self._select_profile(profile))
            self.profile_button_group.addButton(button)
            self.profile_buttons[key] = button
            self.profile_layout.addWidget(button, weight)
        platform.addLayout(platform_row)
        self.profile_buttons["vertical"].setChecked(True)
        self.crop_profile_buttons["vertical"].setChecked(True)
        bottom_cards.addWidget(platform_card, 3)

        destinations_card, destinations = _card("Destinations populaires")
        destinations.setContentsMargins(25, 21, 26, 22)
        destinations.setSpacing(4)
        destination_heading = destinations_card.findChild(QLabel, "cardTitle")
        if destination_heading is not None:
            destination_heading.setIndent(7)
        destination_row = QGridLayout()
        destination_row.setContentsMargins(0, 0, 0, 0)
        destination_row.setSpacing(7)
        self.destination_layout = destination_row
        self.destination_buttons: dict[str, QToolButton] = {}
        self.destination_group = QButtonGroup(self)
        self.destination_group.setExclusive(True)
        destination_profiles = (
            ("tiktok", "TikTok", "icons/tiktok.svg", "vertical"),
            ("instagram_reels", "Instagram\nReels", "icons/instagram_reels.svg", "vertical"),
            ("youtube_shorts", "YouTube\nShorts", "icons/youtube_shorts.svg", "vertical"),
        )
        for index, (key, label, icon_path, profile) in enumerate(destination_profiles):
            button = QToolButton()
            button.setObjectName("destinationButton")
            button.setText(label)
            button.setIcon(QIcon(str(_asset_path(icon_path))))
            button.setIconSize(QSize(36, 36))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setCheckable(True)
            button.setMinimumSize(68, 100)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            button.setAccessibleName(f"Préréglage de sortie {label.replace(chr(10), ' ')} · vertical 9:16")
            button.setToolTip("Applique le profil vertical 9:16 pris en charge par BeautyCut.")
            button.clicked.connect(lambda checked=False, dest=key, item=profile: self._select_destination(dest, item))
            self.destination_group.addButton(button)
            self.destination_buttons[key] = button
            destination_row.addWidget(button, 0, index)
        self.destination_more_button = QToolButton()
        self.destination_more_button.setObjectName("destinationButton")
        self.destination_more_button.setText("Plus")
        self.destination_more_button.setMinimumSize(68, 82)
        self.destination_more_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.destination_more_button.setAccessibleName("Autres préréglages de destination")
        self.destination_more_menu = QMenu(self.destination_more_button)
        extra_destinations = (
            ("Instagram Stories · 9:16", "instagram_stories", "vertical"),
            ("Instagram · Portrait 4:5", "instagram_portrait", "portrait"),
            ("Publication carrée · 1:1", "square_post", "square"),
            ("YouTube · Paysage 16:9", "youtube_landscape", "landscape"),
            ("Dimensions personnalisées…", "custom", "custom"),
        )
        for label, key, profile in extra_destinations:
            action = self.destination_more_menu.addAction(label)
            action.triggered.connect(lambda checked=False, dest=key, item=profile: self._select_destination(dest, item))
        self.destination_more_button.setMenu(self.destination_more_menu)
        self.destination_more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        destination_row.addWidget(self.destination_more_button, 0, 3)
        destinations.addLayout(destination_row)
        destination_hint = QLabel("Les destinations appliquent un profil de format ; BeautyCut ne publie pas directement sur ces services.")
        destination_hint.setObjectName("muted")
        destination_hint.setWordWrap(True)
        destination_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        bottom_cards.addWidget(destinations_card, 2)
        layout.addLayout(bottom_cards)
        layout.addStretch(1)

        self._update_profile_details()
        self._apply_responsive_layout()
        return workspace

    def _build_export_dock(self) -> QWidget:
        dock = ExportDock()
        self.export_dock = dock
        layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, dock)
        layout.setContentsMargins(18, 8, 18, 8)
        layout.setSpacing(0)
        dock.setMinimumHeight(86)
        self.export_dock_layout = layout

        brand = QWidget()
        self.footer_brand_widget = brand
        brand.setMinimumWidth(464)
        brand_row = QHBoxLayout(brand)
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(0)
        footer_mark = QLabel()
        footer_mark.setObjectName("brandMark")
        footer_mark.setPixmap(_brand_mark_pixmap(59, 40))
        footer_mark.setContentsMargins(11, 0, 0, 0)
        footer_mark.setFixedSize(90, 66)
        footer_names = QVBoxLayout()
        footer_names.setContentsMargins(0, 0, 0, 0)
        footer_names.setSpacing(2)
        self.footer_brand_label = QLabel()
        self.footer_brand_label.setObjectName("brand")
        self.footer_brand_label.setTextFormat(Qt.TextFormat.RichText)
        self.footer_brand_label.setAccessibleName("BeautyCut")
        self.footer_summary = QLabel("")
        self.footer_summary.setObjectName("muted")
        footer_names.addWidget(self.footer_brand_label)
        footer_names.addWidget(self.footer_summary)
        brand_row.addWidget(footer_mark)
        brand_row.addLayout(footer_names)
        layout.addWidget(brand, 2)

        self.footer_spacer_left = QWidget()
        self.footer_spacer_left.setFixedWidth(12)
        layout.addWidget(self.footer_spacer_left)

        path_container = QWidget()
        self.footer_path_container = path_container
        path_container.setMinimumWidth(469)
        path_row = QHBoxLayout(path_container)
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(8)
        file_icon = QLabel()
        file_icon.setObjectName("footerFileIcon")
        file_icon.setFixedSize(26, 26)
        self.footer_file_icon = file_icon
        path_row.addWidget(file_icon)
        self.output_path = QLineEdit()
        self.output_path.setAccessibleName("Nom et emplacement du fichier de sortie")
        self.output_path.setPlaceholderText("Nom du fichier de sortie")
        self.output_path.textEdited.connect(self._output_path_edited)
        self.output_path.setMinimumHeight(44)
        self.output_format_badge = StatusPill("MP4", "info")
        self.output_format_badge.setAccessibleName("Format d’exportation sélectionné")
        self.output_format_badge.hide()
        self.output_browse = QPushButton()
        self.output_browse.setObjectName("footerEditButton")
        self.output_browse.setProperty("iconPath", "icons/edit.svg")
        self.output_browse.setToolTip("Choisir le nom et l’emplacement du fichier d’export")
        self.output_browse.setAccessibleName("Choisir le nom et l’emplacement du fichier d’export")
        self.output_browse.setFixedSize(42, 42)
        self.output_browse.setIconSize(QSize(18, 18))
        self.output_browse.clicked.connect(self._choose_output)
        path_row.addWidget(self.output_path, 1)
        path_row.addWidget(self.output_browse)
        layout.addWidget(path_container, 2)

        self.footer_spacer_divider = QWidget()
        self.footer_spacer_divider.setFixedWidth(21)
        layout.addWidget(self.footer_spacer_divider)

        divider = QFrame()
        divider.setObjectName("footerDivider")
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFixedWidth(1)
        self.footer_divider = divider
        layout.addWidget(divider)

        self.footer_spacer_status = QWidget()
        self.footer_spacer_status.setFixedWidth(29)
        layout.addWidget(self.footer_spacer_status)

        action_container = QWidget()
        self.footer_action_container = action_container
        action_container.setMinimumWidth(359)
        action_row = QHBoxLayout(action_container)
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(10)
        self.status_indicator = QLabel()
        self.status_indicator.setObjectName("statusIndicator")
        self.status_indicator.setFixedSize(24, 24)
        action_row.addWidget(self.status_indicator, 0, Qt.AlignmentFlag.AlignVCenter)
        status_column = QVBoxLayout()
        status_column.setSpacing(2)
        self.status_label = QLabel("Importez un média pour commencer.")
        self.status_label.setObjectName("exportStage")
        self.status_label.setProperty("tone", "info")
        status_column.addWidget(self.status_label)
        self.export_metrics = QLabel("")
        self.export_metrics.setObjectName("muted")
        status_column.addWidget(self.export_metrics)
        action_row.addLayout(status_column, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("En attente")
        self.progress.setMinimumWidth(112)
        self.progress.setMaximumWidth(170)
        self.progress.setVisible(False)
        action_row.addWidget(self.progress)
        layout.addWidget(action_container, 1)

        self.footer_spacer_export = QWidget()
        self.footer_spacer_export.setFixedWidth(12)
        layout.addWidget(self.footer_spacer_export)

        self.cancel_button = QPushButton("Annuler")
        self.cancel_button.setMinimumHeight(42)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel_export)
        layout.addWidget(self.cancel_button)
        self.export_button = QPushButton("Exporter")
        self.export_button.setObjectName("primaryButton")
        self.export_button.setProperty("iconPath", "icons/download.svg")
        self.export_button.setIcon(QIcon(str(_asset_path("icons/download.svg"))))
        self.export_button.setIconSize(QSize(24, 24))
        self.export_button.setMinimumWidth(265)
        self.export_button.setMaximumWidth(265)
        self.export_button.setMinimumHeight(54)
        self.export_button.clicked.connect(self._start_export)
        layout.addWidget(self.export_button, 0)
        return dock

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def _apply_responsive_layout(self) -> None:
        if not hasattr(self, "preview_layout"):
            return
        window_width = self.width()
        workspace_width = (
            self.workspace_scroll.viewport().width()
            if hasattr(self, "workspace_scroll") and self.workspace_scroll.viewport().width() > 0
            else max(0, window_width - 380)
        )
        controls_stacked = window_width < 920
        if getattr(self, "_crop_profiles_compact", None) != controls_stacked:
            minimum = 40 if controls_stacked else 46
            self.crop_profile_layout.setSpacing(4 if controls_stacked else 5)
            for button in self.crop_profile_buttons.values():
                button.setMinimumWidth(minimum)
            self.quick_position_button.setMinimumWidth(minimum)
            self._crop_profiles_compact = controls_stacked
        if getattr(self, "_header_controls_stacked", None) != controls_stacked:
            if controls_stacked:
                self.header_top_row.removeWidget(self.header_controls)
                self.header_layout.addWidget(self.header_controls, 0, Qt.AlignmentFlag.AlignRight)
            else:
                self.header_layout.removeWidget(self.header_controls)
                self.header_top_row.addWidget(self.header_controls)
            self._header_controls_stacked = controls_stacked
        navigation_compact = window_width < 920
        if getattr(self, "_navigation_compact", None) != navigation_compact:
            self.header_bar.setFixedHeight(122 if navigation_compact else 90)
            if navigation_compact:
                self.navigation_rail.setFixedWidth(56)
                self.navigation_rail_layout.setContentsMargins(4, 6, 4, 6)
                self.navigation_rail_layout.setSpacing(4)
                for button in self.navigation_buttons.values():
                    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                    button.setIconSize(QSize(18, 18))
                    button.setFixedSize(40, 34)
            else:
                self.navigation_rail.setFixedWidth(98)
                self.navigation_rail_layout.setContentsMargins(0, 9, 0, 9)
                self.navigation_rail_layout.setSpacing(8)
                for button in self.navigation_buttons.values():
                    button.setMinimumSize(0, 88 if button is self.navigation_buttons["home"] else 80)
                    button.setMaximumSize(16777215, 16777215)
                    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
                    button.setIconSize(QSize(22, 22))
            self._navigation_compact = navigation_compact
        export_density = "small" if window_width < 1000 else "medium" if window_width < 1668 else "wide"
        if hasattr(self, "export_dock_layout") and getattr(self, "_export_density", None) != export_density:
            compact = export_density == "small"
            medium = export_density == "medium"
            self.footer_brand_widget.setVisible(not compact)
            self.footer_divider.setVisible(not compact)
            self.footer_brand_widget.setMinimumWidth(0 if compact else 260 if medium else 464)
            self.footer_path_container.setMinimumWidth(0 if compact else 320 if medium else 469)
            self.footer_action_container.setMinimumWidth(0 if compact else 240 if medium else 359)
            self.footer_spacer_left.setFixedWidth(0 if compact else 6 if medium else 12)
            self.footer_spacer_divider.setFixedWidth(0 if compact else 10 if medium else 21)
            self.footer_spacer_status.setFixedWidth(6 if compact else 15 if medium else 29)
            self.footer_spacer_export.setFixedWidth(6 if compact else 8 if medium else 12)
            button_width = 135 if compact else 180 if medium else 265
            self.export_button.setMinimumWidth(button_width)
            self.export_button.setMaximumWidth(150 if compact else button_width)
            self.export_button.setMinimumHeight(44 if compact else 54)
            self.export_dock_layout.setContentsMargins(10 if compact else 18, 6 if compact else 8,
                                                       10 if compact else 18, 6 if compact else 8)
            self._export_density = export_density
        narrow = workspace_width < 710
        if getattr(self, "_preview_layout_narrow", None) != narrow:
            self.preview_workspace.setMaximumHeight(16777215 if narrow else 503)
            self.preview_workspace.setMinimumHeight(0 if narrow else 503)
            self.preview_layout.setDirection(
                QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight
            )
            self.preview_layout.setSpacing(10 if narrow else 18)
            self._preview_layout_narrow = narrow
        cards_stacked = workspace_width < 780
        if getattr(self, "_bottom_cards_stacked", None) != cards_stacked:
            self.bottom_cards_layout.setDirection(
                QBoxLayout.Direction.TopToBottom if cards_stacked else QBoxLayout.Direction.LeftToRight
            )
            for key, button in self.profile_buttons.items():
                button.setMinimumWidth(44 if cards_stacked else 62)
                button.setMinimumHeight(110 if cards_stacked else 104)
                if key == "custom":
                    button.setText("Perso." if cards_stacked else "Personnalisé")
            for button in (*self.destination_buttons.values(), self.destination_more_button):
                button.setMinimumWidth(56 if cards_stacked else 68)
                button.setMinimumHeight(90 if cards_stacked else 104)
            self._bottom_cards_stacked = cards_stacked
        destinations_compact = workspace_width < 760
        if getattr(self, "_destinations_compact", None) != destinations_compact:
            destination_widgets = (*self.destination_buttons.values(), self.destination_more_button)
            for widget in destination_widgets:
                self.destination_layout.removeWidget(widget)
            columns = 2 if destinations_compact else 4
            for index, widget in enumerate(destination_widgets):
                row, column = divmod(index, columns)
                self.destination_layout.addWidget(widget, row, column)
            self.destination_layout.invalidate()
            self.destination_layout.activate()
            self._destinations_compact = destinations_compact
        actions_narrow = workspace_width < 760
        if getattr(self, "_preview_actions_narrow", None) != actions_narrow:
            if actions_narrow:
                for widget in self.preview_action_widgets[:-1]:
                    widget.setText("")
                    widget.setMinimumWidth(38)
                self.reset_preview_button.setText("Réinit.")
                self.reset_preview_button.setMinimumWidth(72)
            else:
                theme = THEME_BY_KEY[self.theme_key]
                for widget in self.preview_action_widgets:
                    icon_path = widget.property("iconPath")
                    if icon_path:
                        widget.setIcon(tinted_icon(icon_path, theme.accent_light, 16))
                self.grid_action_button.setText("Grille")
                self.center_preview_button.setText("Centrer")
                self.flip_button.setText("Inverser")
                self.fullscreen_action_button.setText("Plein écran")
                self.reset_preview_button.setText("Réinitialiser l’aperçu")
                for widget, width in zip(self.preview_action_widgets, (112, 124, 128, 145, 178)):
                    widget.setMinimumWidth(width)
                self.reset_preview_button.setMinimumWidth(178)
            self.preview_actions_layout.setSpacing(5 if actions_narrow else 8)
            self.preview_actions_layout.invalidate()
            self.preview_actions_layout.activate()
            self._preview_actions_narrow = actions_narrow
    def _apply_style(self) -> None:
        theme: Theme = THEME_BY_KEY[self.theme_key]
        self.setStyleSheet(build_stylesheet(theme))
        self.header_bar.set_theme(theme)
        brand_text = f"Beauty<span style='color:{theme.accent_light}'>Cut</span>"
        self.brand_label.setText(brand_text)
        self.footer_brand_label.setText(brand_text)
        for button in getattr(self, "navigation_buttons", {}).values():
            icon_path = button.property("iconPath")
            if icon_path:
                button.setIcon(tinted_icon(icon_path, theme.accent_light, 26))
        for button in (*getattr(self, "crop_profile_buttons", {}).values(), *getattr(self, "profile_buttons", {}).values()):
            icon_path = button.property("iconPath")
            if icon_path:
                button.setIcon(tinted_icon(icon_path, theme.accent_light, 24))
        for section in getattr(self, "sections", {}).values():
            icon_path = section.header.property("iconPath")
            if icon_path:
                section.header.setIcon(tinted_icon(icon_path, theme.accent_light, 19))
        if hasattr(self, "quick_position_button"):
            self.quick_position_button.setIcon(tinted_icon("icons/more.svg", theme.accent_light, 18))
        if hasattr(self, "center_crop_button"):
            self.center_crop_button.setIcon(tinted_icon("icons/reset.svg", theme.accent_light, 17))
        if hasattr(self, "output_browse"):
            self.output_browse.setIcon(tinted_icon("icons/edit.svg", theme.accent_light, 18))
        if hasattr(self, "export_button"):
            icon_color = theme.accent_text if theme.light else theme.text
            self.export_button.setIcon(tinted_icon("icons/download.svg", icon_color, 24))
        if hasattr(self, "footer_file_icon"):
            self.footer_file_icon.setPixmap(tinted_icon("icons/file.svg", theme.accent_light, 24).pixmap(24, 24))
        if hasattr(self, "output_formats_icon") and self.output_formats_icon is not None:
            self.output_formats_icon.setPixmap(
                tinted_icon("icons/media.svg", theme.accent_light, 24).pixmap(24, 24)
            )
        self._set_status_indicator("success" if self.info and (self.report is None or self.report.supported) else "info")
        for name in (
            "grid_action_button", "center_preview_button", "flip_button",
            "fullscreen_action_button", "reset_preview_button",
        ):
            button = getattr(self, name, None)
            icon_path = button.property("iconPath") if button is not None else None
            if icon_path:
                button.setIcon(
                    tinted_icon(icon_path, theme.accent_light, 16)
                )
        if hasattr(self, "media_thumbnail") and not self._media_thumbnail_has_image:
            self.media_thumbnail.setPixmap(tinted_icon("icons/media.svg", theme.accent_light, 32).pixmap(32, 32))
        for preview_name in ("original_preview", "output_preview"):
            preview = getattr(self, preview_name, None)
            if preview is not None:
                preview.set_theme(theme)

    def _refresh_engine_status(self) -> None:
        try:
            ffmpeg_path = find_tool("ffmpeg")
            ffprobe_path = find_tool("ffprobe")
        except Exception as exc:
            self._set_engine_status("Moteur vidéo : indisponible", "error")
            self.engine_badge.setToolTip(str(exc))
            return
        self._set_engine_status("Moteur vidéo : prêt à importer", "success")
        self.engine_badge.setToolTip(f"FFmpeg : {ffmpeg_path}\nFFprobe : {ffprobe_path}")

    def _set_engine_status(self, text: str, tone: str) -> None:
        theme = THEME_BY_KEY.get(self.theme_key, THEME_BY_KEY["deep_ocean"])
        color = theme.success if tone == "success" else theme.error if tone == "error" else theme.accent_light
        self.engine_badge.set_state(f"<span style='color:{color}'>●</span>&nbsp; {text}", tone)

    def _update_output_format_badge(self) -> None:
        container = self.container_combo.currentText() if self.container_combo.count() else ""
        video = self.video_codec_combo.currentText()
        audio = self.audio_codec_combo.currentText()
        self.output_format_badge.set_state(container.upper() if container else "—", "info")
        details = [f"Conteneur {container}" if container else "Aucun conteneur disponible"]
        if video:
            details.append(f"Vidéo {video}")
        if audio:
            details.append(f"Audio {audio}")
        self.output_format_badge.setToolTip(" · ".join(details))
        if hasattr(self, "footer_summary"):
            state = "Prêt à exporter" if self.info and (self.report is None or self.report.supported) else "Prêt à importer"
            formats = [container.upper()] if container else []
            if video:
                formats.append(video)
            summary = " · ".join([state, *formats])
            self.footer_summary.setText(summary)
            self.footer_summary.setToolTip(self.output_format_badge.toolTip())

    def _set_status_indicator(self, tone: str) -> None:
        indicator = getattr(self, "status_indicator", None)
        if indicator is None:
            return
        theme = THEME_BY_KEY.get(self.theme_key, THEME_BY_KEY["deep_ocean"])
        if tone == "success":
            icon, color, label = "icons/check.svg", theme.success, "Prêt"
        elif tone == "error":
            icon, color, label = "icons/info.svg", theme.error, "Erreur"
        else:
            icon, color, label = "icons/info.svg", theme.accent_light, "En attente"
        indicator.setPixmap(tinted_icon(icon, color, 23).pixmap(23, 23))
        indicator.setToolTip(label)
    def _theme_selected(self, _index: int) -> None:
        key = self.theme_combo.currentData()
        if key not in THEME_BY_KEY:
            return
        self.theme_key = key
        self._settings_store().setValue("appearance/theme", key)
        self._apply_style()

    def _navigate(self, destination: str) -> None:
        for key, button in self.navigation_buttons.items():
            button.setChecked(key == destination)
        if destination == "home":
            self.left_scroll.verticalScrollBar().setValue(0)
            self.workspace_scroll.verticalScrollBar().setValue(0)
            return
        if destination == "themes":
            self.theme_combo.setFocus(Qt.FocusReason.ShortcutFocusReason)
            self.theme_combo.showPopup()
            return
        if destination == "about":
            QMessageBox.about(
                self,
                "À propos de BeautyCut",
                f"BeautyCut {__version__}\n\nRecadrage et export vidéo en local avec FFmpeg et FFprobe.",
            )
            return
        if destination == "settings":
            for key in ("quality", "advanced", "updates"):
                self.sections[key].set_expanded(True)
            QTimer.singleShot(0, lambda: self.left_scroll.ensureWidgetVisible(self.sections["quality"]))

    def _set_media_thumbnail(self, data: bytes | None = None) -> None:
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data, "JPEG"):
            self.media_thumbnail.setPixmap(
                pixmap.scaled(self.media_thumbnail.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                              Qt.TransformationMode.SmoothTransformation)
            )
            self._media_thumbnail_has_image = True
            return
        self._media_thumbnail_has_image = False
        icon = tinted_icon("icons/media.svg", THEME_BY_KEY[self.theme_key].accent_light, 32)
        self.media_thumbnail.setPixmap(icon.pixmap(32, 32))

    def _segment_target_changed(self, index: int) -> None:
        seconds = self.segment_target_combo.itemData(index)
        if isinstance(seconds, int):
            minutes, remainder = divmod(seconds, 60)
            self.segment_limit_minutes_spin.setValue(minutes)
            self.segment_limit_seconds_spin.setValue(remainder)

    def _segment_minutes_changed(self, minutes: int) -> None:
        at_maximum = minutes >= self.segment_limit_minutes_spin.maximum()
        if at_maximum and self.segment_limit_seconds_spin.value():
            self.segment_limit_seconds_spin.setValue(0)
        self.segment_limit_seconds_spin.setEnabled(not at_maximum)
        self._settings_changed()

    @staticmethod
    def _settings_store() -> QSettings:
        return QSettings("BeautyCut", "BeautyCut")

    def _automatic_update_check(self) -> None:
        if not self.isVisible():
            return
        self.update_banner.setVisible(False)
        self._check_for_updates(manual=False)

    def _save_update_channel(self, *, show_status: bool = True) -> bool:
        url = self.update_manifest_edit.text().strip()
        parsed = QUrl(url)
        if parsed.scheme().lower() != "https" or not parsed.host():
            if show_status:
                self.update_status.setText("Saisissez une adresse de manifeste publique commençant par https://.")
            return False
        self._settings_store().setValue("updates/manifest_url", url)
        if show_status:
            self.update_status.setText("Adresse enregistrée. La prochaine vérification utilisera ce canal.")
        return True

    def _check_for_updates(self, *, manual: bool) -> None:
        if self.update_check_thread and self.update_check_thread.isRunning():
            return
        if manual:
            if not self._save_update_channel(show_status=False):
                self.update_status.setText("Adresse invalide : utilisez un manifeste HTTPS public.")
                return
            manifest_url = self.update_manifest_edit.text().strip()
        else:
            manifest_url = str(self._settings_store().value("updates/manifest_url", DEFAULT_UPDATE_MANIFEST_URL))
        self._automatic_update_flow = not manual
        self.update_status.setText("Vérification de la version publiée…")
        self.update_check_button.setEnabled(False)
        self.update_check_thread = CheckForUpdateThread(manifest_url, self)
        self.update_check_thread.checked.connect(self._update_check_completed)
        self.update_check_thread.failed.connect(self._update_check_failed)
        self.update_check_thread.finished.connect(self._update_check_finished)
        self.update_check_thread.start()

    def _update_check_completed(self, manifest: UpdateManifest | None) -> None:
        self._available_update = manifest
        if manifest is None:
            self.update_status.setText(f"BeautyCut {__version__} est à jour.")
            self.update_download_button.setVisible(False)
            self.update_install_button.setVisible(False)
            self.update_banner_progress.setVisible(False)
            self.update_banner.setVisible(False)
            self._downloaded_installer = None
            self._automatic_update_flow = False
            return
        if self._automatic_update_flow:
            self.update_banner_title.setText(f"Mise à jour de BeautyCut {manifest.version}")
            self.update_banner_notes.setText(manifest.release_notes or "Installation automatique de la nouvelle version.")
            self.update_banner_progress.setRange(0, 0)
            self.update_banner_progress.setFormat("Téléchargement sécurisé…")
            self.update_banner_progress.setVisible(True)
            self.update_banner_button.setVisible(False)
            self.update_banner.setVisible(True)
            self.update_status.setText(
                f"BeautyCut {manifest.version} est disponible. Téléchargement et installation automatiques…"
            )
            self._download_available_update(automatic=True)
            self._automatic_update_flow = False
            return
        self._downloaded_installer = None
        self.update_status.setText(f"Version {manifest.version} disponible. Téléchargez-la pour l’installer.")
        self.update_download_button.setText(f"Télécharger BeautyCut {manifest.version}")
        self.update_download_button.setVisible(True)
        self.update_download_button.setEnabled(True)
        self.update_install_button.setVisible(False)
        self.update_banner_title.setText(f"BeautyCut {manifest.version} est disponible")
        self.update_banner_notes.setText(manifest.release_notes or "Téléchargez la mise à jour pour profiter des nouveautés.")
        self.update_banner_button.setText("Télécharger")
        self.update_banner_button.setVisible(True)
        self.update_banner_progress.setVisible(False)
        self.update_banner.setVisible(True)

    def _update_check_failed(self, message: str) -> None:
        automatic = self._automatic_update_flow
        self._automatic_update_flow = False
        if automatic:
            self.update_banner_progress.setVisible(False)
            self.update_banner_button.setVisible(False)
            self.update_banner.setVisible(False)
        self.update_status.setText(f"Vérification impossible : {message}")

    def _update_check_finished(self) -> None:
        self.update_check_button.setEnabled(True)
        self.update_check_thread = None

    def _download_available_update(self, *, automatic: bool = False) -> None:
        if not self._available_update or (self.update_download_thread and self.update_download_thread.isRunning()):
            return
        self._install_after_update_download = automatic
        self.update_status.setText(f"Téléchargement sécurisé de BeautyCut {self._available_update.version}…")
        self.update_download_button.setEnabled(False)
        self.update_check_button.setEnabled(False)
        self.update_progress.setRange(0, 0)
        self.update_progress.setFormat("Connexion au serveur…")
        self.update_progress.setVisible(True)
        if automatic:
            self.update_banner_progress.setRange(0, 0)
            self.update_banner_progress.setFormat("Téléchargement sécurisé…")
            self.update_banner_progress.setVisible(True)
        self.update_download_thread = DownloadUpdateThread(self._available_update, self)
        self.update_download_thread.progress.connect(self._update_download_progress)
        self.update_download_thread.downloaded.connect(self._update_download_completed)
        self.update_download_thread.failed.connect(self._update_download_failed)
        self.update_download_thread.finished.connect(self._update_download_finished)
        self.update_download_thread.start()

    def _update_banner_action(self) -> None:
        if self._downloaded_installer:
            self._install_downloaded_update()
        else:
            self._download_available_update()

    def _update_download_progress(self, percent: int) -> None:
        if percent < 0:
            self.update_progress.setRange(0, 0)
            self.update_progress.setFormat("Téléchargement en cours…")
            self.update_banner_progress.setRange(0, 0)
            self.update_banner_progress.setFormat("Téléchargement en cours…")
        else:
            self.update_progress.setRange(0, 100)
            self.update_progress.setValue(percent)
            self.update_progress.setFormat("Vérification de l’installeur…" if percent == 100 else "Téléchargement · %p %")
            self.update_banner_progress.setRange(0, 100)
            self.update_banner_progress.setValue(percent)
            self.update_banner_progress.setFormat("Vérification de l’installeur…" if percent == 100 else "Téléchargement · %p %")

    def _update_download_completed(self, path: str) -> None:
        self._downloaded_installer = Path(path)
        self.update_progress.setRange(0, 100)
        self.update_progress.setValue(100)
        self.update_status.setText("Installeur téléchargé et empreinte SHA-256 vérifiée.")
        if self._install_after_update_download:
            self.update_banner_title.setText("Installation de la mise à jour")
            self.update_banner_notes.setText("Empreinte SHA-256 vérifiée. BeautyCut va redémarrer automatiquement.")
            self.update_banner_progress.setRange(0, 100)
            self.update_banner_progress.setValue(100)
            self.update_banner_progress.setFormat("Prêt à installer")
            self.update_status.setText("Empreinte vérifiée. Installation automatique, BeautyCut va redémarrer…")
            QTimer.singleShot(500, self._install_downloaded_update)
            return
        self.update_install_button.setVisible(True)
        self.update_banner_button.setText("Installer et relancer")

    def _update_download_failed(self, message: str) -> None:
        automatic = self._install_after_update_download
        self._install_after_update_download = False
        self.update_status.setText(f"Téléchargement impossible : {message}")
        self.update_download_button.setEnabled(True)
        self.update_progress.setVisible(False)
        if automatic:
            self.update_banner_title.setText("Téléchargement de la mise à jour impossible")
            self.update_banner_notes.setText(message)
            self.update_banner_progress.setVisible(False)
            self.update_banner.setVisible(True)

    def _update_download_finished(self) -> None:
        self.update_check_button.setEnabled(True)
        self.update_download_thread = None

    def _install_downloaded_update(self) -> None:
        installer = self._downloaded_installer
        if not installer or not installer.is_file():
            self.update_status.setText("L’installeur vérifié est introuvable. Téléchargez la mise à jour à nouveau.")
            return
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(
                [
                    str(installer), "/VERYSILENT", "/NORESTART",
                    "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS",
                ],
                close_fds=True,
                creationflags=flags,
            )
        except OSError as exc:
            self.update_status.setText(f"Impossible de lancer l’installeur : {exc}")
            return
        self.update_status.setText("Installation en cours. BeautyCut va se fermer puis redémarrer.")
        QTimer.singleShot(250, self.close)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt API
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt API
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self._load_media(url.toLocalFile())
                event.acceptProposedAction()
                return

    def _choose_file(self) -> None:
        known = " ".join(f"*{ext}" for ext in sorted(MEDIA_EXTENSIONS))
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importer un média",
            "",
            f"Médias compatibles ({known});;Tous les fichiers (*.*)",
        )
        if path:
            self._load_media(path)

    def _load_media(self, path: str) -> None:
        if self.export_thread and self.export_thread.isRunning():
            self.status_label.setText("Annulez ou terminez l’export avant de changer de média.")
            return
        self.generation += 1
        self.info = None
        self.import_button.setEnabled(False)
        self.import_button.setText("Changer de fichier")
        self.export_button.setEnabled(False)
        self.media_section.set_summary("Analyse…")
        self._set_status_indicator("info")
        self._set_media_thumbnail()
        _set_tone(self.compatibility, "info")
        self.media_name.setText(Path(path).name)
        self.media_details.setText("Lecture des métadonnées et des codecs avec FFprobe…")
        self.compatibility.setText("Analyse en cours…")
        self.compatibility.setVisible(True)
        self.original_preview.set_placeholder("Extraction d’une image repère…")
        self.output_preview.set_placeholder("Préparation du cadrage…")
        self.status_label.setText("Analyse du média…")
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.analysis_thread = AnalyzeMediaThread(path, self.generation, self)
        self.analysis_thread.loaded.connect(self._media_loaded)
        self.analysis_thread.failed.connect(self._media_failed)
        analysis_thread = self.analysis_thread
        self.analysis_thread.finished.connect(lambda: self._analysis_finished(analysis_thread))
        self.analysis_thread.start()

    def _analysis_finished(self, thread) -> None:
        if self.analysis_thread is thread:
            self.analysis_thread = None

    def _media_loaded(self, generation: int, info, report, inventory) -> None:
        if generation != self.generation:
            return
        self.info = info
        self.report = report
        self.inventory = inventory
        self.import_button.setEnabled(True)
        self.export_button.setEnabled(True)
        self._set_engine_status(
            "Moteur vidéo · prêt" + (" · NVENC" if inventory.nvenc_available else " · CPU"), "success"
        )
        self.engine_badge.setToolTip("FFmpeg et FFprobe ont analysé le média importé.")
        self.media_section.set_summary("Audio seul" if not info.primary_video else "Importé")
        self.media_name.setText(info.path.name)
        parts = [
            f"{info.container.replace(',', ', ')}",
            _format_duration(info.duration),
            _format_size(info.size_bytes),
        ]
        if info.primary_video:
            video = info.primary_video
            parts.append(f"{video.width} × {video.height} · {video.avg_fps or video.fps or 0:.3g} i/s")
            parts.append(codec_label(video.codec_name))
        else:
            parts.append("Audio seul")
        self.media_details.setText("\n".join(parts))
        self.compatibility.setVisible(True)
        if info.audio_streams:
            self.audio_tracks.setText("Pistes audio\n" + "\n".join(f"{n + 1}. {stream.label}" for n, stream in enumerate(info.audio_streams)))
        else:
            self.audio_tracks.setText("Aucune piste audio.")
        self.compatibility.setText("\n".join(report.messages))
        _set_tone(self.compatibility, "success" if report.supported else "error")
        self.status_label.setText("Média prêt pour l’export." if report.supported else "Certains codecs ne peuvent pas être lus.")
        if report.supported:
            self.status_label.setText("Prêt à exporter")
            self._set_status_indicator("success")
        else:
            self._set_status_indicator("error")
        self.progress.setVisible(False)
        self._auto_output_path = True
        self._output_directory = info.path.parent
        output_name = info.path.stem + "_beautycut" + self._settings().suffix
        self.output_path.setText(output_name)
        self.output_path.setToolTip(str(self._output_directory / output_name))
        self._refresh_containers()
        self._refresh_codecs()
        self._request_preview()

    def _media_failed(self, generation: int, message: str) -> None:
        if generation != self.generation:
            return
        self.import_button.setEnabled(True)
        self.export_button.setEnabled(False)
        self.progress.setVisible(False)
        self._set_status_indicator("error")
        _set_tone(self.compatibility, "error")
        self.compatibility.setText(message)
        self.media_details.setText("Le fichier n’a pas pu être analysé.")
        self.compatibility.setVisible(True)
        self.media_section.set_summary("Erreur")
        self._set_media_thumbnail()
        self.status_label.setText("Importation impossible.")
        self.original_preview.set_placeholder("Aucune image disponible")
        self.output_preview.set_placeholder("Aucune image disponible")

    def _settings(self) -> ExportSettings:
        profile = self.selected_profile
        if profile == "custom":
            width, height = self.width_spin.value(), self.height_spin.value()
        else:
            selected = next(item for item in PROFILES if item.key == profile)
            width, height = selected.width, selected.height
        bitrate_text = self.bitrate_edit.text().strip()
        return ExportSettings(
            profile=profile,
            width=width,
            height=height,
            container=CONTAINERS.get(self.container_combo.currentText(), "mp4"),
            video_codec=self.video_codec_combo.currentText() or "H.264 / AVC",
            audio_codec=self.audio_codec_combo.currentText() or "AAC",
            quality=self.quality_spin.value(),
            bitrate_kbps=int(bitrate_text) if bitrate_text else None,
            frame_rate=self.fps_combo.currentData() or None,
            crop_x=(self.x_slider.value() - 50) / 50,
            crop_y=(self.y_slider.value() - 50) / 50,
            crop_zoom=self.zoom_slider.value() / 100,
            fit_mode=self.fit_mode_combo.currentData() or "fill",
            flip_horizontal=self.flip_button.isChecked(),
            speed=self.speed_combo.currentData() or "fast",
            split_enabled=self.segment_checkbox.isChecked(),
            split_limit_seconds=(
                self.segment_limit_minutes_spin.value() * 60
                + self.segment_limit_seconds_spin.value()
            ),
        )

    def _select_profile(self, profile: str) -> None:
        if profile not in self.profile_buttons:
            return
        if profile == "custom" and self.selected_profile != "custom" and self.height_spin.value():
            self._custom_ratio = self.width_spin.value() / self.height_spin.value()
        self.selected_profile = profile
        for key, button in self.profile_buttons.items():
            button.setChecked(key == profile)
        for key, button in self.crop_profile_buttons.items():
            button.setChecked(key == profile)
        self._active_destination = None
        if hasattr(self, "destination_group"):
            self.destination_group.setExclusive(False)
            for button in self.destination_buttons.values():
                button.setChecked(False)
            self.destination_group.setExclusive(True)
        if hasattr(self, "destination_combo"):
            destination_index = self.destination_combo.findData(profile)
            self.destination_combo.blockSignals(True)
            self.destination_combo.setCurrentIndex(destination_index)
            self.destination_combo.blockSignals(False)
        self._apply_profile()

    def _destination_changed(self, index: int) -> None:
        profile = self.destination_combo.itemData(index)
        destination = self.destination_combo.itemData(index, Qt.ItemDataRole.UserRole + 1)
        if profile in self.profile_buttons and destination:
            self._select_destination(destination, profile)

    def _select_destination(self, destination: str, profile: str) -> None:
        if profile not in self.profile_buttons:
            return
        self._select_profile(profile)
        self._active_destination = destination
        if destination in self.destination_buttons:
            self.destination_buttons[destination].setChecked(True)
        for index in range(self.destination_combo.count()):
            if self.destination_combo.itemData(index, Qt.ItemDataRole.UserRole + 1) == destination:
                self.destination_combo.blockSignals(True)
                self.destination_combo.setCurrentIndex(index)
                self.destination_combo.blockSignals(False)
                break
        self._update_profile_details()

    def _update_profile_details(self) -> None:
        profile = self.selected_profile
        if profile == "custom":
            width, height = self.width_spin.value(), self.height_spin.value()
        else:
            item = next(item for item in PROFILES if item.key == profile)
            width, height = item.width, item.height
        ratio = width / height
        aspect = (
            "9:16" if abs(ratio - 9 / 16) < 0.01 else
            "4:5" if abs(ratio - 4 / 5) < 0.01 else
            "16:9" if abs(ratio - 16 / 9) < 0.01 else
            "1:1" if abs(ratio - 1) < 0.01 else f"{width}:{height}"
        )
        if profile == "vertical":
            platform_hint = "TikTok · Reels · Shorts · Stories"
        elif profile == "portrait":
            platform_hint = "Fil social portrait · Instagram / Facebook"
        elif profile == "square":
            platform_hint = "Publications carrées · flux social"
        elif profile == "landscape":
            platform_hint = "YouTube classique · paysage"
        else:
            platform_hint = "Dimensions définies par vous"
        if self._active_destination:
            destination_names = {
                "tiktok": "TikTok", "instagram_reels": "Instagram Reels", "youtube_shorts": "YouTube Shorts",
                "instagram_stories": "Instagram Stories", "instagram_portrait": "Instagram portrait",
                "square_post": "Publication carrée", "youtube_landscape": "YouTube paysage", "custom": "Format personnalisé",
            }
            platform_hint = destination_names.get(self._active_destination, platform_hint)
        self.format_details.setText(f"{platform_hint} · {width} × {height}")
        if hasattr(self, "output_preview"):
            self.output_preview.set_title("Résultat estimé")

    def _apply_profile(self) -> None:
        custom = self.selected_profile == "custom"
        self.custom_size_widget.setVisible(custom)
        if not custom:
            selected = next(item for item in PROFILES if item.key == self.selected_profile)
            self.width_spin.blockSignals(True)
            self.height_spin.blockSignals(True)
            self.width_spin.setValue(selected.width)
            self.height_spin.setValue(selected.height)
            self.width_spin.blockSignals(False)
            self.height_spin.blockSignals(False)
        self._update_profile_details()
        self._settings_changed()

    def _custom_width_changed(self, value: int) -> None:
        if self.selected_profile == "custom" and self.lock_ratio_checkbox.isChecked() and self._custom_ratio > 0:
            height = max(16, min(7680, int(round(value / self._custom_ratio / 2) * 2)))
            self.height_spin.blockSignals(True)
            self.height_spin.setValue(height)
            self.height_spin.blockSignals(False)
        self._settings_changed()

    def _custom_height_changed(self, value: int) -> None:
        if self.selected_profile == "custom" and self.lock_ratio_checkbox.isChecked() and self._custom_ratio > 0:
            width = max(16, min(7680, int(round(value * self._custom_ratio / 2) * 2)))
            self.width_spin.blockSignals(True)
            self.width_spin.setValue(width)
            self.width_spin.blockSignals(False)
        self._settings_changed()

    def _ratio_lock_changed(self, locked: bool) -> None:
        if locked and self.height_spin.value():
            self._custom_ratio = self.width_spin.value() / self.height_spin.value()
        self._settings_changed()

    def _set_crop_position(self, x: int, y: int) -> None:
        self.x_slider.setValue(max(0, min(100, x)))
        self.y_slider.setValue(max(0, min(100, y)))

    def _crop_preview_position_changed(self, x: int, y: int) -> None:
        self._set_crop_position(x, y)

    def _framing_mode_changed(self, *_args) -> None:
        fill = self.fit_mode_combo.currentData() == "fill"
        self.zoom_slider.setEnabled(fill)
        self.x_slider.setEnabled(fill)
        self.y_slider.setEnabled(fill)
        self.crop_note.setToolTip(
            "Le mode Remplir recadre les bords. Tout conserver adapte l’image entière à la sortie."
        )
        self._settings_changed()

    def _zoom_changed(self, value: int) -> None:
        self.zoom_value.setText(f"{value} %")
        self._settings_changed()

    def _toggle_preview_grid(self, visible: bool) -> None:
        for preview in (self.original_preview, self.output_preview):
            preview.canvas.set_grid_visible(visible)

    def _toggle_preview_guides(self, visible: bool) -> None:
        for preview in (self.original_preview, self.output_preview):
            preview.canvas.set_guides_visible(visible)

    def _center_crop(self) -> None:
        self._set_crop_position(50, 50)
        self.original_preview.canvas.reset_view()
        self.output_preview.canvas.reset_view()

    def _reset_preview_views(self) -> None:
        self.original_preview.canvas.reset_view()
        self.output_preview.canvas.reset_view()

    def _flip_changed(self, _checked: bool) -> None:
        self._settings_changed()

    def _reset_framing(self) -> None:
        self.zoom_slider.setValue(100)
        self.x_slider.setValue(50)
        self.y_slider.setValue(50)
        self.fit_mode_combo.setCurrentIndex(self.fit_mode_combo.findData("fill"))
        self.flip_button.setChecked(False)
        self._center_crop()
        self._settings_changed()

    def _quality_preset_changed(self, *_args) -> None:
        preset = self.quality_preset_combo.currentData()
        values = {"recommended": 22, "high": 18, "compact": 28}
        if preset == "custom":
            self.quality_spin.setEnabled(True)
        else:
            self.quality_spin.setEnabled(False)
            self.quality_spin.blockSignals(True)
            self.quality_spin.setValue(values.get(preset, 22))
            self.quality_spin.blockSignals(False)
        self._settings_changed()

    def _quality_number_changed(self, value: int) -> None:
        if not self.quality_spin.isEnabled():
            return
        matching = {22: "recommended", 18: "high", 28: "compact"}.get(value, "custom")
        self.quality_preset_combo.blockSignals(True)
        self.quality_preset_combo.setCurrentIndex(self.quality_preset_combo.findData(matching))
        self.quality_preset_combo.blockSignals(False)
        self._settings_changed()

    def _toggle_advanced(self, checked: bool) -> None:
        self.advanced_panel.setVisible(checked)
        self.advanced_toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

    def _container_changed(self) -> None:
        self._refresh_codecs()
        self._update_auto_output_extension()
        self._settings_changed()

    def _refresh_containers(self) -> None:
        if not self.inventory:
            return
        previous = self.container_combo.currentText()
        self.container_combo.blockSignals(True)
        self.container_combo.clear()
        for label, muxer in CONTAINERS.items():
            if muxer in self.inventory.muxers:
                self.container_combo.addItem(label, muxer)
        if previous and self.container_combo.findText(previous) >= 0:
            self.container_combo.setCurrentText(previous)
        elif self.container_combo.count():
            self.container_combo.setCurrentIndex(0)
        self.container_combo.blockSignals(False)
        if not self.container_combo.count():
            self.status_label.setText("Cette compilation de FFmpeg ne contient aucun muxeur proposé par BeautyCut.")

    def _refresh_codecs(self) -> None:
        if not self.container_combo.count():
            self.video_codec_combo.clear()
            self.audio_codec_combo.clear()
            self.export_button.setEnabled(False)
            return
        container = CONTAINERS[self.container_combo.currentText()]
        if self.inventory:
            videos = usable_video_codecs(container, self.inventory)
            audios = usable_audio_codecs(container, self.inventory)
        else:
            videos = sorted(VIDEO_FORMATS[container])
            audios = sorted(AUDIO_FORMATS[container])
        previous_video, previous_audio = self.video_codec_combo.currentText(), self.audio_codec_combo.currentText()
        self.video_codec_combo.blockSignals(True)
        self.audio_codec_combo.blockSignals(True)
        self.video_codec_combo.clear()
        self.audio_codec_combo.clear()
        self.video_codec_combo.addItems(videos)
        self.audio_codec_combo.addItems(audios)
        if previous_video in videos:
            self.video_codec_combo.setCurrentText(previous_video)
        elif "H.264 / AVC" in videos:
            self.video_codec_combo.setCurrentText("H.264 / AVC")
        if previous_audio in audios:
            self.audio_codec_combo.setCurrentText(previous_audio)
        elif "AAC" in audios:
            self.audio_codec_combo.setCurrentText("AAC")
        self.video_codec_combo.blockSignals(False)
        self.audio_codec_combo.blockSignals(False)
        self._update_output_format_badge()
        available = bool(videos and audios)
        self.export_button.setEnabled(available and self.info is not None and (self.report is None or self.report.supported))
        if not available:
            self.status_label.setText("Aucun couple de codecs compatible avec ce conteneur dans FFmpeg.")

    def _settings_changed(self, *_args) -> None:
        self._update_profile_details()
        self._update_crop_overlay()
        if not self.info:
            return
        self._preview_timer.start()

    def _update_crop_overlay(self) -> None:
        if not hasattr(self, "original_preview"):
            return
        settings = self._settings()
        self.original_preview.canvas.set_crop(
            settings.width / settings.height,
            self.x_slider.value(),
            self.y_slider.value(),
            settings.fit_mode,
            settings.crop_zoom,
        )

    def _output_path_edited(self, _text: str) -> None:
        self._auto_output_path = False
        path = Path(_text.strip()).expanduser()
        if path.parent != Path("."):
            self._output_directory = path.parent
        resolved = self._output_file_path()
        self.output_path.setToolTip(str(resolved) if resolved else "")

    def _output_file_path(self) -> Path | None:
        value = self.output_path.text().strip()
        if not value:
            return None
        path = Path(value).expanduser()
        if path.is_absolute() or path.parent != Path("."):
            return path
        directory = self._output_directory or (self.info.path.parent if self.info else None)
        return directory / path if directory else path

    def _update_auto_output_extension(self) -> None:
        if self.info and self._auto_output_path:
            suffix = self._settings().suffix
            self._output_directory = self.info.path.parent
            output_name = self.info.path.stem + "_beautycut" + suffix
            self.output_path.setText(output_name)
            self.output_path.setToolTip(str(self._output_directory / output_name))

    def _request_preview(self) -> None:
        if not self.info or not self.info.primary_video:
            if self.info:
                self.original_preview.set_placeholder("Le média importé ne contient pas d’image vidéo.")
                self.output_preview.set_placeholder("Un fichier audio sera exporté sans piste vidéo.")
            return
        generation = self.generation
        if self.preview_thread and self.preview_thread.isRunning():
            # La nouvelle demande attend la fin du calcul précédent.
            self._pending_preview = True
            return
        self._pending_preview = False
        self.preview_thread = PreviewMediaThread(generation, self.info, self._settings(), self)
        self.preview_thread.ready.connect(self._preview_ready)
        self.preview_thread.failed.connect(self._preview_failed)
        self.preview_thread.finished.connect(self._preview_finished)
        self.preview_thread.start()

    def _preview_ready(self, generation: int, original: bytes, vertical: bytes) -> None:
        if generation != self.generation:
            return
        self._set_media_thumbnail(original)
        self.original_preview.set_image(original)
        self.output_preview.set_image(vertical)

    def _preview_failed(self, generation: int, message: str) -> None:
        if generation == self.generation:
            self.output_preview.set_placeholder(message)

    def _preview_finished(self) -> None:
        self.preview_thread = None
        if getattr(self, "_pending_preview", False):
            self._request_preview()

    def _choose_output(self) -> None:
        current = self._output_file_path()
        start = str(current) if current else (str(self.info.path.with_suffix(self._settings().suffix)) if self.info else "")
        name, _ = QFileDialog.getSaveFileName(
            self, "Choisir le fichier de sortie", start,
            f"{self.container_combo.currentText()} (*{self._settings().suffix});;Tous les fichiers (*.*)",
        )
        if name:
            path = Path(name)
            if path.suffix.lower() != self._settings().suffix:
                path = path.with_suffix(self._settings().suffix)
            self._output_directory = path.parent
            self.output_path.setText(path.name)
            self.output_path.setToolTip(str(path))
            self._auto_output_path = False

    def _start_export(self) -> None:
        if not self.info:
            self.status_label.setText("Importez d’abord un média.")
            return
        report = self.report
        if report and not report.supported:
            self.status_label.setText("Le fichier contient un codec absent de cette installation FFmpeg.")
            return
        target = self._output_file_path()
        if target is None:
            self.status_label.setText("Choisissez un fichier de sortie.")
            return
        settings = self._settings()
        if target.suffix.lower() != settings.suffix:
            self.status_label.setText(f"Choisissez une extension {settings.suffix} pour ce conteneur.")
            return
        try:
            settings.validate()
        except Exception as exc:
            self.status_label.setText(str(exc))
            return
        segment_paths = split_output_paths(
            target, self.info.duration or 0, settings.split_limit_seconds
        ) if settings.split_enabled else []
        existing_outputs = [path for path in (segment_paths or [target]) if path.exists()]
        overwrite = bool(existing_outputs)
        if existing_outputs:
            if segment_paths:
                message = (
                    f"{len(existing_outputs)} fichier(s) sur {len(segment_paths)} existent déjà. "
                    "Voulez-vous remplacer les parties concernées ?"
                )
            else:
                message = f"{target.name} existe déjà. Voulez-vous le remplacer ?"
            answer = QMessageBox.question(
                self, "Remplacer les fichiers ?", message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._export_was_split = bool(segment_paths)
        self.export_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setVisible(True)
        self.import_button.setEnabled(False)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self._set_status_indicator("info")
        self._latest_export_progress = None
        self._export_started_at = time.perf_counter()
        self.status_label.setText(
            f"Préparation de {len(segment_paths)} parties…" if segment_paths else "Préparation de l’export…"
        )
        self.progress.setRange(0, 0)
        self.progress.setFormat("Préparation…")
        self.export_metrics.setText(
            f"Découpage en {len(segment_paths)} fichiers numérotés · limite {_format_duration(settings.split_limit_seconds)} par partie."
            if segment_paths else "FFmpeg analyse la source et prépare l’encodage. Cette étape peut prendre quelques instants."
        )
        self._export_timer.start()
        self.export_thread = ExportMediaThread(
            str(self.info.path), str(target), settings, overwrite, self,
            media_info=self.info, inventory=self.inventory,
        )
        self.export_thread.progress.connect(self._export_progress_updated)
        self.export_thread.completed.connect(self._export_completed)
        self.export_thread.failed.connect(self._export_failed)
        self.export_thread.cancelled.connect(self._export_cancelled)
        self.export_thread.finished.connect(self._export_thread_finished)
        self.export_thread.start()

    def _cancel_export(self) -> None:
        if self.export_thread:
            self.cancel_button.setEnabled(False)
            self.status_label.setText("Annulation de l’export…")
            self.export_thread.cancel()

    def _export_completed(self, result) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.progress.setFormat("100 %")
        self._set_status_indicator("success")
        elapsed = max(0, int(time.perf_counter() - self._export_started_at)) if self._export_started_at else 0
        if isinstance(result, list):
            paths = [Path(path) for path in result]
            size = sum(path.stat().st_size for path in paths if path.exists())
            self.status_label.setText(f"Export terminé · {len(paths)} parties créées")
            self.export_metrics.setText(
                f"Terminé en {_format_duration(elapsed)} · {len(paths)} fichiers · total {_format_size(size)} · dossier {paths[0].parent}"
            )
        else:
            path = Path(result)
            size = path.stat().st_size if path.exists() else 0
            self.status_label.setText(f"Export terminé · {path.name}")
            self.export_metrics.setText(f"Terminé en {_format_duration(elapsed)} · fichier {_format_size(size)}")

    def _export_failed(self, message: str) -> None:
        self.progress.setRange(0, 100)
        self.progress.setFormat("Échec")
        self.status_label.setText(message)
        self._set_status_indicator("error")
        self.compatibility.setText(message)
        _set_tone(self.compatibility, "error")

    def _export_cancelled(self) -> None:
        self._set_status_indicator("info")
        self.progress.setRange(0, 100)
        self.status_label.setText(
            "Export annulé. Les parties déjà terminées sont conservées ; aucun segment incomplet n’a été gardé."
            if getattr(self, "_export_was_split", False)
            else "Export annulé ; aucun fichier incomplet n’a été conservé."
        )
        self.progress.setFormat("Annulé")
        self.progress.setValue(0)

    def _export_thread_finished(self) -> None:
        self._export_timer.stop()
        self.progress.setRange(0, 100)
        self.export_button.setEnabled(bool(
            self.info and self.inventory and self.video_codec_combo.count() and self.audio_codec_combo.count()
            and (self.report is None or self.report.supported)
        ))
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.import_button.setEnabled(True)
        self.progress.setVisible(False)
        self.export_thread = None

    def _export_progress_updated(self, progress: ExportProgress) -> None:
        self._latest_export_progress = progress
        self.status_label.setText(progress.stage)
        if progress.percent is None:
            self.progress.setRange(0, 0)
            self.progress.setFormat("Préparation…")
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(progress.percent)
            self.progress.setFormat("%p %")
        self._refresh_export_metrics()

    def _refresh_export_metrics(self) -> None:
        if not self._export_started_at:
            return
        elapsed = max(0.0, time.perf_counter() - self._export_started_at)
        snapshot = self._latest_export_progress
        total = snapshot.total_seconds if snapshot else (self.info.duration if self.info else None)
        encoded = snapshot.encoded_seconds if snapshot else 0.0
        fields = [f"Écoulé {_format_duration(elapsed)}"]
        if total and total > 0:
            fields.insert(0, f"Traité {_format_duration(encoded)} / {_format_duration(total)}")
        if snapshot and snapshot.eta_seconds is not None and snapshot.eta_seconds >= 0:
            fields.append(f"Restant ~{_format_duration(snapshot.eta_seconds)}")
        elif snapshot and snapshot.speed:
            fields.append("Calcul du temps restant…")
        else:
            fields.append("Temps restant en calcul…")
        if snapshot and snapshot.speed:
            fields.append(f"Vitesse {snapshot.speed:.2f}×")
        if snapshot and snapshot.fps:
            fields.append(f"{snapshot.fps:.0f} i/s")
        if snapshot and snapshot.output_size_bytes:
            fields.append(f"Sortie {_format_size(snapshot.output_size_bytes)}")
        elif self.output_path.text():
            fields.append(f"Fichier : {Path(self.output_path.text()).name}")
        self.export_metrics.setText("   ·   ".join(fields))

    @staticmethod
    def _position_label(value: int, vertical: bool) -> str:
        if 47 <= value <= 53:
            return "Centre"
        if vertical:
            return "Haut" if value < 50 else "Bas"
        return "Gauche" if value < 50 else "Droite"


