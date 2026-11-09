from __future__ import annotations

from pathlib import Path
import subprocess
import math
import sys
import time

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    QSettings,
    Qt,
    QTimer,
    Signal,
    QUrl,
)
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QIcon, QIntValidator, QMouseEvent, QPainter, QPen, QPixmap
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
    QScrollArea,
    QSlider,
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
from ..updater import DEFAULT_UPDATE_MANIFEST_URL, UpdateManifest
from .themes import THEMES, THEME_BY_KEY, Theme
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


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 18)
    layout.setSpacing(12)
    heading = QLabel(title)
    heading.setObjectName("cardTitle")
    layout.addWidget(heading)
    return frame, layout


class SmoothScrollArea(QScrollArea):
    """A small eased scroll animation for the two main settings/work areas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scroll_animation = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._scroll_animation.setDuration(220)
        self._scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        scrollbar = self.verticalScrollBar()
        pixels = event.pixelDelta().y()
        distance = pixels if pixels else round(event.angleDelta().y() / 120 * 96)
        if not distance:
            super().wheelEvent(event)
            return
        current = scrollbar.value()
        target = max(scrollbar.minimum(), min(scrollbar.maximum(), current - distance))
        self._scroll_animation.stop()
        self._scroll_animation.setStartValue(current)
        self._scroll_animation.setEndValue(target)
        self._scroll_animation.start()
        event.accept()


class PreviewPanel(QFrame):
    def __init__(self, title: str, aspect: str, *, crop_interactive: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("previewCard")
        self._aspect = aspect
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        self.title = QLabel(title)
        self.title.setObjectName("previewTitle")
        self.title.setWordWrap(True)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.canvas = PreviewCanvas("Importez un média pour afficher une image repère", interactive=crop_interactive)
        self.canvas.setObjectName("previewCanvas")
        self.canvas.setMinimumSize(150, 145 if aspect == "vertical" else 125)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.caption = QLabel(
            "Glissez le cadre pour déplacer le cadrage" if crop_interactive else "Rendu final estimé · extrait localement avec FFmpeg"
        )
        self.caption.setObjectName("muted")
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setWordWrap(True)
        self.caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.title)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self.caption)
    def set_image(self, data: bytes) -> None:
        pixmap = QPixmap()
        if not pixmap.loadFromData(data, "JPEG"):
            self.set_placeholder("L’image repère n’a pas pu être décodée.")
            return
        self.canvas.set_image(pixmap)

    def set_placeholder(self, text: str) -> None:
        self.canvas.set_placeholder(text)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._render()

    def _render(self) -> None:
        self.canvas.update()


class PreviewCanvas(QLabel):
    crop_position_changed = Signal(int, int)

    def __init__(self, placeholder: str, *, interactive: bool = False, parent=None):
        super().__init__(placeholder, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self._pixmap: QPixmap | None = None
        self._interactive = interactive
        self._target_aspect = 9 / 16
        self._fit_mode = "fill"
        self._zoom = 1.0
        self._preview_background = "#0d1519"
        self._crop_accent = "#42d6bd"
        self._crop_x = 50
        self._crop_y = 50
        self._drag_start = None
        self._drag_position = (50, 50)
        self.setMouseTracking(interactive)
        if interactive:
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def set_image(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.setText("")
        self.update()

    def set_placeholder(self, text: str) -> None:
        self._pixmap = None
        self.setText(text)
        self.update()

    def set_crop(self, aspect: float, x: int, y: int, fit_mode: str, zoom: float) -> None:
        self._target_aspect = max(0.01, aspect)
        self._crop_x = max(0, min(100, x))
        self._crop_y = max(0, min(100, y))
        self._fit_mode = fit_mode
        self._zoom = max(1.0, zoom)
        self.setCursor(
            Qt.CursorShape.OpenHandCursor if self._interactive and fit_mode == "fill" else Qt.CursorShape.ArrowCursor
        )
        self.update()

    def set_theme_colors(self, preview_background: str, accent: str) -> None:
        self._preview_background = preview_background
        self._crop_accent = accent
        self.update()

    def _image_rect(self):
        if not self._pixmap or self._pixmap.isNull():
            return None
        available = QRectF(self.contentsRect()).adjusted(6, 6, -6, -6)
        ratio = min(available.width() / self._pixmap.width(), available.height() / self._pixmap.height())
        width, height = self._pixmap.width() * ratio, self._pixmap.height() * ratio
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)

    def _crop_rect(self):
        image = self._image_rect()
        if image is None:
            return None
        source_aspect = image.width() / max(1, image.height())
        if source_aspect > self._target_aspect:
            crop_height = image.height() / self._zoom
            crop_width = crop_height * self._target_aspect
        else:
            crop_width = image.width() / self._zoom
            crop_height = crop_width / self._target_aspect
        available_x = max(0.0, image.width() - crop_width)
        available_y = max(0.0, image.height() - crop_height)
        return QRectF(
            image.left() + available_x * self._crop_x / 100,
            image.top() + available_y * self._crop_y / 100,
            crop_width,
            crop_height,
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self._pixmap or self._pixmap.isNull():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), QColor(self._preview_background))
        image = self._image_rect()
        if image is None:
            return
        painter.drawPixmap(image, self._pixmap, QRectF(self._pixmap.rect()))
        if not self._interactive or self._fit_mode != "fill":
            return
        crop = self._crop_rect()
        if crop is None:
            return
        shade = QColor(3, 10, 13, 155)
        painter.fillRect(QRectF(image.left(), image.top(), image.width(), max(0, crop.top() - image.top())), shade)
        painter.fillRect(QRectF(image.left(), crop.bottom(), image.width(), max(0, image.bottom() - crop.bottom())), shade)
        painter.fillRect(QRectF(image.left(), crop.top(), max(0, crop.left() - image.left()), crop.height()), shade)
        painter.fillRect(QRectF(crop.right(), crop.top(), max(0, image.right() - crop.right()), crop.height()), shade)
        painter.setPen(QPen(QColor(self._crop_accent), 2))
        painter.drawRect(crop)
        grid_color = QColor(self._crop_accent)
        grid_color.setAlpha(175)
        painter.setPen(QPen(grid_color, 1, Qt.PenStyle.DashLine))
        for division in (1, 2):
            x = crop.left() + crop.width() * division / 3
            y = crop.top() + crop.height() * division / 3
            painter.drawLine(int(x), int(crop.top()), int(x), int(crop.bottom()))
            painter.drawLine(int(crop.left()), int(y), int(crop.right()), int(y))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._crop_accent))
        for x, y in ((crop.left(), crop.top()), (crop.right(), crop.top()), (crop.left(), crop.bottom()), (crop.right(), crop.bottom())):
            painter.drawEllipse(int(x - 4), int(y - 4), 8, 8)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if not self._interactive or self._fit_mode != "fill" or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        image, crop = self._image_rect(), self._crop_rect()
        if image is None or crop is None or not image.contains(event.position()):
            return
        point = event.position()
        span_x = max(1.0, image.width() - crop.width())
        span_y = max(1.0, image.height() - crop.height())
        if not crop.contains(point):
            self._crop_x = round(max(0.0, min(1.0, (point.x() - crop.width() / 2 - image.left()) / span_x)) * 100)
            self._crop_y = round(max(0.0, min(1.0, (point.y() - crop.height() / 2 - image.top()) / span_y)) * 100)
            self.crop_position_changed.emit(self._crop_x, self._crop_y)
        self._drag_start = point
        self._drag_position = (self._crop_x, self._crop_y)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._drag_start is None:
            super().mouseMoveEvent(event)
            return
        image, crop = self._image_rect(), self._crop_rect()
        if image is None or crop is None:
            return
        span_x = max(1.0, image.width() - crop.width())
        span_y = max(1.0, image.height() - crop.height())
        dx = event.position().x() - self._drag_start.x()
        dy = event.position().y() - self._drag_start.y()
        x = max(0, min(100, self._drag_position[0] + round(dx * 100 / span_x)))
        y = max(0, min(100, self._drag_position[1] + round(dy * 100 / span_y)))
        if (x, y) != (self._crop_x, self._crop_y):
            self._crop_x, self._crop_y = x, y
            self.crop_position_changed.emit(x, y)
            self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            self._drag_start = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


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
        saved_theme = str(self._settings_store().value("appearance/theme", "northern_lights"))
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
        self.setWindowIcon(QIcon(str(_asset_path("beautycut_mark.ico"))))
        self._refresh_codecs()
        self._quality_preset_changed()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
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
        outer.setContentsMargins(24, 18, 24, 20)
        outer.setSpacing(16)
        outer.addWidget(self._build_header())

        content = QHBoxLayout()
        content.setSpacing(16)
        left = self._build_left_panel()
        left.setFixedWidth(320)
        content.addWidget(left)
        self.workspace_scroll = SmoothScrollArea()
        self.workspace_scroll.setObjectName("workspaceScroll")
        self.workspace_scroll.setWidgetResizable(True)
        self.workspace_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.workspace_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.workspace_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.workspace_scroll.viewport().setObjectName("workspaceViewport")
        self.workspace_scroll.setWidget(self._build_workspace())
        content.addWidget(self.workspace_scroll, 1)
        outer.addLayout(content, 1)
        self.setCentralWidget(root)

    def _build_header(self) -> QWidget:
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        mark = QLabel()
        mark.setObjectName("brandMark")
        mark.setPixmap(
            QPixmap(str(_asset_path("beautycut_mark.png"))).scaled(
                52,
                52,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        mark.setFixedSize(52, 52)
        names = QVBoxLayout()
        brand = QLabel('<span style="color:#f3f8ff">Beauty</span><span style="color:#22d3ee">Cut</span>')
        brand.setObjectName("brand")
        brand.setAccessibleName("BeautyCut")
        subtitle = QLabel("Transformez vos vidéos, en local")
        subtitle.setObjectName("muted")
        names.addWidget(brand)
        names.addWidget(subtitle)
        row.addWidget(mark)
        row.addLayout(names)
        row.addStretch(1)
        self.theme_combo = QComboBox()
        self.theme_combo.setObjectName("themeCombo")
        self.theme_combo.setAccessibleName("Thème visuel de BeautyCut")
        self.theme_combo.setToolTip("Choisir parmi 20 thèmes nature, océan et cosmos")
        self.theme_combo.setMinimumWidth(142)
        self.theme_combo.setMaximumWidth(190)
        for theme in THEMES:
            swatch = QPixmap(18, 18)
            swatch.fill(Qt.GlobalColor.transparent)
            painter = QPainter(swatch)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(QPen(QColor(theme.accent_light), 1))
            painter.setBrush(QColor(theme.accent))
            painter.drawEllipse(2, 2, 14, 14)
            painter.end()
            self.theme_combo.addItem(QIcon(swatch), theme.name, theme.key)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(self.theme_key))
        self.theme_combo.currentIndexChanged.connect(self._theme_selected)
        row.addWidget(self.theme_combo)
        self.engine_badge = QLabel("Moteur vidéo · prêt à importer")
        self.engine_badge.setObjectName("engineBadge")
        row.addWidget(self.engine_badge)
        return header

    def _build_left_panel(self) -> QWidget:
        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setObjectName("leftScroll")
        scroll.viewport().setObjectName("leftViewport")
        panel = QWidget()
        panel.setObjectName("scrollContent")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 5, 0)
        layout.setSpacing(14)

        source_card, source = _card("Votre média")
        self.import_button = QPushButton("＋   Importer une vidéo ou un audio")
        self.import_button.setObjectName("primaryButton")
        self.import_button.setMinimumHeight(44)
        self.import_button.clicked.connect(self._choose_file)
        source.addWidget(self.import_button)
        self.drop_hint = QLabel("Glissez-déposez un fichier ici · formats FFmpeg")
        self.drop_hint.setObjectName("muted")
        self.drop_hint.setWordWrap(True)
        source.addWidget(self.drop_hint)
        self.media_name = QLabel("Aucun média importé")
        self.media_name.setObjectName("mediaName")
        self.media_name.setWordWrap(True)
        source.addWidget(self.media_name)
        self.media_details = QLabel("Les informations du fichier apparaîtront ici.")
        self.media_details.setObjectName("muted")
        self.media_details.setWordWrap(True)
        source.addWidget(self.media_details)
        self.audio_tracks = QLabel("")
        self.audio_tracks.setObjectName("trackDetails")
        self.audio_tracks.setWordWrap(True)
        source.addWidget(self.audio_tracks)
        self.compatibility = QLabel("L’importation analyse les pistes et les codecs avec FFprobe.")
        self.compatibility.setObjectName("statusInfo")
        self.compatibility.setWordWrap(True)
        source.addWidget(self.compatibility)
        layout.addWidget(source_card)

        crop_card, crop = _card("Ajustement du cadrage")
        self.crop_note = QLabel("Glissez le cadre dans l’aperçu ou choisissez un point rapide. Chaque réglage est repris à l’export.")
        self.crop_note.setObjectName("muted")
        self.crop_note.setWordWrap(True)
        crop.addWidget(self.crop_note)
        fit_label = QLabel("Remplissage de la sortie")
        crop.addWidget(fit_label)
        self.fit_mode_combo = QComboBox()
        self.fit_mode_combo.addItem("Remplir · recadrer les bords", "fill")
        self.fit_mode_combo.addItem("Tout conserver · bandes noires", "contain")
        self.fit_mode_combo.currentIndexChanged.connect(self._framing_mode_changed)
        crop.addWidget(self.fit_mode_combo)
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

        anchor_header = QHBoxLayout()
        anchor_header.addWidget(QLabel("Position rapide"))
        anchor_header.addStretch(1)
        self.center_crop_button = QPushButton("Recentrer")
        self.center_crop_button.setObjectName("subtleButton")
        self.center_crop_button.clicked.connect(lambda: self._set_crop_position(50, 50))
        anchor_header.addWidget(self.center_crop_button)
        crop.addLayout(anchor_header)
        self.crop_anchor_grid = QGridLayout()
        self.crop_anchor_grid.setSpacing(4)
        self.crop_anchor_group = QButtonGroup(self)
        self.crop_anchor_group.setExclusive(True)
        anchors = (
            ("↖", 0, 0, "Haut gauche"), ("↑", 50, 0, "Haut centré"), ("↗", 100, 0, "Haut droite"),
            ("←", 0, 50, "Centre gauche"), ("●", 50, 50, "Centre"), ("→", 100, 50, "Centre droite"),
            ("↙", 0, 100, "Bas gauche"), ("↓", 50, 100, "Bas centré"), ("↘", 100, 100, "Bas droite"),
        )
        for index, (glyph, x, y, tooltip) in enumerate(anchors):
            button = QToolButton()
            button.setObjectName("cropAnchorButton")
            button.setText(glyph)
            button.setToolTip(tooltip)
            button.setAccessibleName(f"Position rapide : {tooltip}")
            button.setProperty("cropX", x)
            button.setProperty("cropY", y)
            button.setCheckable(True)
            button.setFixedSize(36, 30)
            button.clicked.connect(lambda checked=False, crop_x=x, crop_y=y: self._set_crop_position(crop_x, crop_y))
            self.crop_anchor_group.addButton(button, index)
            self.crop_anchor_grid.addWidget(button, index // 3, index % 3)
            if x == 50 and y == 50:
                button.setChecked(True)
        crop.addLayout(self.crop_anchor_grid)
        self.x_slider = self._add_slider(crop, "Horizontal", "cropXValue", vertical=False)
        self.y_slider = self._add_slider(crop, "Vertical", "cropYValue", vertical=True)
        layout.addWidget(crop_card)

        settings_card, settings = _card("Réglages rapides")
        self.format_details = QLabel("Vertical · 9:16 · TikTok · Reels · Shorts")
        self.format_details.setObjectName("formatDetails")
        settings.addWidget(self.format_details)

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

        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setObjectName("advancedToggle")
        self.advanced_toggle.setText("Paramètres avancés")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_panel = QWidget()
        advanced = QVBoxLayout(self.advanced_panel)
        advanced.setContentsMargins(0, 4, 0, 0)
        advanced.setSpacing(8)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)

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
        self.video_codec_combo.currentIndexChanged.connect(self._settings_changed)
        self.audio_codec_combo.currentIndexChanged.connect(self._settings_changed)
        self.quality_spin.valueChanged.connect(self._quality_number_changed)
        self.bitrate_edit.textChanged.connect(self._settings_changed)
        self.advanced_panel.setVisible(False)
        settings.addWidget(self.advanced_toggle)
        settings.addWidget(self.advanced_panel)
        layout.addWidget(settings_card)

        update_card, update = _card("Mise à jour automatique")
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
        layout.addWidget(update_card)
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
            if hasattr(self, "crop_anchor_group"):
                self._sync_crop_anchor()
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
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

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

        platform_card, platform = _card("Format de sortie")
        platform_row = QGridLayout()
        platform_row.setSpacing(8)
        self.profile_layout = platform_row
        self.profile_buttons: dict[str, QToolButton] = {}
        self.profile_button_group = QButtonGroup(self)
        self.profile_button_group.setExclusive(True)
        profiles = (
            ("vertical", "Vertical\n9:16", "icons/vertical.svg", "TikTok · Reels · Shorts · Stories"),
            ("portrait", "Portrait\n4:5", "icons/portrait.svg", "Fil Instagram / Facebook"),
            ("square", "Carré\n1:1", "icons/square.svg", "Publications carrées"),
            ("landscape", "Paysage\n16:9", "icons/landscape.svg", "YouTube · vidéo classique"),
            ("custom", "Perso\n↗", "icons/custom.svg", "Définir largeur et hauteur"),
        )
        for key, label, icon_path, description in profiles:
            button = QToolButton()
            button.setObjectName("presetButton")
            button.setText(label)
            button.setIcon(QIcon(str(_asset_path(icon_path))))
            button.setIconSize(QSize(23, 23))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setCheckable(True)
            button.setMinimumSize(60, 68)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            button.setToolTip(description)
            button.setAccessibleName(f"Format {label.replace(chr(10), ' ')}. {description}")
            button.clicked.connect(lambda checked=False, profile=key: self._select_profile(profile))
            self.profile_button_group.addButton(button)
            self.profile_buttons[key] = button
            self.profile_layout.addWidget(button, 0, len(self.profile_buttons) - 1)
        platform.addLayout(platform_row)
        self.profile_format_label = QLabel("TikTok · Reels · Shorts · 1080 × 1920")
        self.profile_format_label.setObjectName("muted")
        self.profile_format_label.setWordWrap(True)
        platform.addWidget(self.profile_format_label)
        self.profile_buttons["vertical"].setChecked(True)
        layout.addWidget(platform_card)

        preview_card, preview = _card("Aperçu du recadrage")
        previews = QHBoxLayout()
        previews.setSpacing(12)
        self.preview_layout = previews
        self.original_preview = PreviewPanel("ORIGINAL · glissez le cadre", "landscape", crop_interactive=True)
        self.output_preview = PreviewPanel("SORTIE · 9:16", "vertical")
        self.original_preview.canvas.crop_position_changed.connect(self._crop_preview_position_changed)
        previews.addWidget(self.original_preview, 1)
        previews.addWidget(self.output_preview, 1)
        preview.addLayout(previews, 1)
        self.preview_note = QLabel("Aperçu sur une image repère · l’export traite la vidéo complète et toutes les pistes audio.")
        self.preview_note.setObjectName("muted")
        self.preview_note.setWordWrap(True)
        preview.addWidget(self.preview_note)
        layout.addWidget(preview_card, 1)

        export_card, export = _card("Fichier de sortie")
        path_row = QHBoxLayout()
        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText("Choisissez un média à importer pour préparer le fichier de sortie")
        self.output_path.textEdited.connect(self._output_path_edited)
        self.output_browse = QPushButton("Parcourir…")
        self.output_browse.clicked.connect(self._choose_output)
        path_row.addWidget(self.output_path, 1)
        path_row.addWidget(self.output_browse)
        export.addLayout(path_row)
        self.status_label = QLabel("Prêt à importer un média.")
        self.status_label.setObjectName("exportStage")
        self.status_label.setWordWrap(True)
        export.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("En attente")
        export.addWidget(self.progress)
        self.export_metrics = QLabel("Après le démarrage, BeautyCut indiquera le temps, la durée traitée et la vitesse.")
        self.export_metrics.setObjectName("muted")
        self.export_metrics.setWordWrap(True)
        export.addWidget(self.export_metrics)
        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.export_button = QPushButton("Exporter")
        self.export_button.setObjectName("primaryButton")
        self.export_button.setMinimumWidth(144)
        self.export_button.setMinimumHeight(42)
        self.export_button.clicked.connect(self._start_export)
        self.cancel_button = QPushButton("Annuler")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_export)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.export_button)
        export.addLayout(action_row)
        layout.addWidget(export_card)
        self._update_profile_details()
        self._apply_responsive_layout()
        return workspace

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def _apply_responsive_layout(self) -> None:
        if not hasattr(self, "preview_layout"):
            return
        narrow = self.width() < 850
        if getattr(self, "_preview_layout_narrow", None) != narrow:
            self.preview_layout.setDirection(
                QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight
            )
            self.preview_layout.setSpacing(10 if narrow else 12)
            self._preview_layout_narrow = narrow
        columns = 3 if self.width() < 760 else len(self.profile_buttons)
        if getattr(self, "_profile_layout_columns", None) != columns:
            for index, button in enumerate(self.profile_buttons.values()):
                self.profile_layout.removeWidget(button)
                row, column = divmod(index, columns)
                self.profile_layout.addWidget(button, row, column)
            self._profile_layout_columns = columns

    def _apply_style(self) -> None:
        theme: Theme = THEME_BY_KEY[self.theme_key]
        self.setStyleSheet(f"""
            QMainWindow, QWidget#root {{ background: {theme.background}; color: {theme.text}; font-family: 'Segoe UI'; font-size: 10pt; }}
            QLabel {{ color: {theme.text}; background: transparent; }}
            QLabel#brandMark {{ background: transparent; }}
            QLabel#brand {{ font-size: 18pt; font-weight: 700; }}
            QLabel#muted {{ color: {theme.muted}; font-size: 10pt; }}
            QLabel#statusInfo, QLabel#trackDetails {{ color: {theme.muted}; font-size: 10pt; }}
            QLabel#cardTitle {{ font-size: 11pt; font-weight: 700; color: {theme.text}; }}
            QLabel#mediaName {{ font-weight: 700; color: {theme.text}; }}
            QLabel#previewTitle {{ color: {theme.muted}; font-size: 10pt; font-weight: 700; letter-spacing: 1px; }}
            QLabel#previewCanvas {{ background: {theme.preview}; border: 1px solid {theme.border}; border-radius: 12px; color: {theme.muted}; }}
            QFrame#card, QFrame#previewCard {{ background: {theme.card}; border: 1px solid {theme.border}; border-radius: 15px; }}
            QFrame#updateBanner {{ background: {theme.selected}; border: 1px solid {theme.accent}; border-radius: 13px; }}
            QLabel#updateBannerTitle {{ color: {theme.accent_light}; font-weight: 700; }}
            QLabel#engineBadge {{ color: {theme.accent_light}; background: {theme.selected}; border: 1px solid {theme.border}; padding: 8px 12px; border-radius: 10px; }}
            QLabel#exportStage {{ color: {theme.accent_light}; font-size: 10pt; font-weight: 700; }}
            QLabel#accentText, QLabel#formatDetails {{ color: {theme.accent_light}; font-size: 10pt; font-weight: 700; padding-top: 2px; }}
            QPushButton {{ background: {theme.surface}; color: {theme.text}; border: 1px solid {theme.border}; border-radius: 9px; padding: 9px 12px; }}
            QPushButton:hover {{ background: {theme.selected}; border-color: {theme.accent}; }}
            QPushButton:disabled {{ color: {theme.muted}; background: {theme.surface}; border-color: {theme.border}; }}
            QPushButton#primaryButton {{ color: {theme.accent_text}; font-weight: 750; border: none;
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {theme.accent_light}, stop:1 {theme.accent}); }}
            QPushButton#primaryButton:hover {{ color: {theme.accent_text}; background: {theme.accent_light}; }}
            QPushButton#primaryButton:disabled {{ color: {theme.muted}; background: {theme.surface}; border: 1px solid {theme.border}; }}
            QPushButton#subtleButton {{ color: {theme.accent_light}; background: transparent; border: 1px solid {theme.border}; padding: 6px 10px; }}
            QComboBox, QLineEdit, QSpinBox {{ background: {theme.surface}; border: 1px solid {theme.border}; border-radius: 8px;
                padding: 8px; color: {theme.text}; min-height: 24px; selection-background-color: {theme.selected}; }}
            QComboBox:hover, QLineEdit:hover, QSpinBox:hover {{ border-color: {theme.accent}; }}
            QComboBox QAbstractItemView {{ background: {theme.card}; color: {theme.text}; selection-background-color: {theme.selected}; }}
            QCheckBox {{ color: {theme.text}; spacing: 8px; }}
            QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {theme.border}; border-radius: 4px; background: {theme.surface}; }}
            QCheckBox::indicator:checked {{ background: {theme.accent}; border-color: {theme.accent}; }}
            QToolButton#presetButton {{ color: {theme.text}; background: {theme.surface}; border: 1px solid {theme.border}; border-radius: 11px; padding: 6px; font-weight: 700; }}
            QToolButton#presetButton:hover {{ background: {theme.selected}; border-color: {theme.accent}; }}
            QToolButton#presetButton:checked {{ color: {theme.accent_light}; background: {theme.selected}; border: 1px solid {theme.accent}; }}
            QToolButton#cropAnchorButton {{ color: {theme.text}; background: {theme.surface}; border: 1px solid {theme.border}; border-radius: 7px; font-size: 12pt; }}
            QToolButton#cropAnchorButton:hover {{ color: {theme.accent_light}; border-color: {theme.accent}; }}
            QToolButton#cropAnchorButton:checked {{ color: {theme.accent_text}; background: {theme.accent}; border-color: {theme.accent}; }}
            QToolButton#advancedToggle {{ color: {theme.accent_light}; background: transparent; border: 0; padding: 5px 0; text-align: left; font-weight: 700; }}
            QSlider::groove:horizontal {{ height: 5px; background: {theme.groove}; border-radius: 2px; }}
            QSlider::sub-page:horizontal {{ background: {theme.accent}; border-radius: 2px; }}
            QSlider::handle:horizontal {{ background: {theme.accent_light}; width: 16px; margin: -6px 0; border-radius: 8px; }}
            QProgressBar {{ background: {theme.surface}; color: {theme.text}; border: 1px solid {theme.border}; border-radius: 7px; text-align: center; min-height: 17px; }}
            QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {theme.accent}, stop:1 {theme.accent_light}); border-radius: 6px; }}
            QScrollArea#leftScroll, QScrollArea#workspaceScroll {{ background: {theme.background}; border: 0; }}
            QWidget#leftViewport, QWidget#workspaceViewport, QWidget#scrollContent, QWidget#workspaceContent {{ background: {theme.background}; }}
            QScrollBar:vertical {{ background: {theme.surface}; width: 9px; margin: 0; border-radius: 4px; }}
            QScrollBar::handle:vertical {{ background: {theme.scrollbar}; min-height: 30px; border-radius: 4px; }}
            QScrollBar::handle:vertical:hover {{ background: {theme.accent}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QToolTip {{ color: {theme.text}; background: {theme.card}; border: 1px solid {theme.border}; padding: 5px; }}
        """)
        for preview_name in ("original_preview", "output_preview"):
            preview = getattr(self, preview_name, None)
            if preview is not None:
                preview.canvas.set_theme_colors(theme.preview, theme.accent_light)

    def _theme_selected(self, _index: int) -> None:
        key = self.theme_combo.currentData()
        if key not in THEME_BY_KEY:
            return
        self.theme_key = key
        self._settings_store().setValue("appearance/theme", key)
        self._apply_style()

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
        self.update_banner_title.setText("Recherche des mises à jour")
        self.update_banner_notes.setText("BeautyCut vérifie les versions publiées.")
        self.update_banner_progress.setRange(0, 0)
        self.update_banner_progress.setFormat("Vérification…")
        self.update_banner_progress.setVisible(True)
        self.update_banner_button.setVisible(False)
        self.update_banner.setVisible(True)
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
            self.update_banner_title.setText("Mise à jour indisponible")
            self.update_banner_notes.setText(message)
            self.update_banner_progress.setVisible(False)
            self.update_banner_button.setVisible(False)
            self.update_banner.setVisible(True)
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
        self.export_button.setEnabled(False)
        self.media_name.setText(Path(path).name)
        self.media_details.setText("Lecture des métadonnées et des codecs avec FFprobe…")
        self.compatibility.setText("Analyse en cours…")
        self.original_preview.set_placeholder("Extraction d’une image repère…")
        self.output_preview.set_placeholder("Préparation du cadrage…")
        self.status_label.setText("Analyse du média…")
        self.progress.setValue(0)
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
        self.engine_badge.setText("FFmpeg · prêt" + (" · NVENC disponible" if inventory.nvenc_available else " · CPU"))
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
        if info.audio_streams:
            self.audio_tracks.setText("Pistes audio\n" + "\n".join(f"{n + 1}. {stream.label}" for n, stream in enumerate(info.audio_streams)))
        else:
            self.audio_tracks.setText("Aucune piste audio.")
        self.compatibility.setText("\n".join(report.messages))
        self.compatibility.setStyleSheet("color: #b6e2bf;" if report.supported else "color: #ff9a9a;")
        self.status_label.setText("Média prêt pour l’export." if report.supported else "Certains codecs ne peuvent pas être lus.")
        self._auto_output_path = True
        self.output_path.setText(str(info.path.with_name(info.path.stem + "_beautycut" + self._settings().suffix)))
        self._refresh_containers()
        self._refresh_codecs()
        self._request_preview()

    def _media_failed(self, generation: int, message: str) -> None:
        if generation != self.generation:
            return
        self.import_button.setEnabled(True)
        self.export_button.setEnabled(False)
        self.compatibility.setStyleSheet("color: #ff9a9a;")
        self.compatibility.setText(message)
        self.media_details.setText("Le fichier n’a pas pu être analysé.")
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
            speed=self.speed_combo.currentData() or "fast",
            split_enabled=self.segment_checkbox.isChecked(),
            split_limit_seconds=(
                self.segment_limit_minutes_spin.value() * 60
                + self.segment_limit_seconds_spin.value()
            ),
        )

    def _select_profile(self, profile: str) -> None:
        if profile == "custom" and self.selected_profile != "custom" and self.height_spin.value():
            self._custom_ratio = self.width_spin.value() / self.height_spin.value()
        self.selected_profile = profile
        for key, button in self.profile_buttons.items():
            button.setChecked(key == profile)
        self._apply_profile()

    def _update_profile_details(self) -> None:
        profile = self.selected_profile
        if profile == "custom":
            width, height = self.width_spin.value(), self.height_spin.value()
            description = "Résolution personnalisée"
        else:
            item = next(item for item in PROFILES if item.key == profile)
            width, height = item.width, item.height
            description = item.label.split(" · ")[0]
        ratio = width / height
        aspect = (
            "9:16" if abs(ratio - 9 / 16) < 0.01 else
            "4:5" if abs(ratio - 4 / 5) < 0.01 else
            "16:9" if abs(ratio - 16 / 9) < 0.01 else
            "1:1" if abs(ratio - 1) < 0.01 else f"{width}:{height}"
        )
        text = f"{description} · {aspect} · {width} × {height}"
        self.format_details.setText(text)
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
        self.profile_format_label.setText(f"{platform_hint} · {width} × {height}")
        if hasattr(self, "output_preview"):
            self.output_preview.title.setText(f"SORTIE · {aspect}")

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
        self._sync_crop_anchor()

    def _sync_crop_anchor(self) -> None:
        x, y = self.x_slider.value(), self.y_slider.value()
        for button in self.crop_anchor_group.buttons():
            button.setChecked(button.property("cropX") == x and button.property("cropY") == y)

    def _crop_preview_position_changed(self, x: int, y: int) -> None:
        self._set_crop_position(x, y)

    def _framing_mode_changed(self, *_args) -> None:
        fill = self.fit_mode_combo.currentData() == "fill"
        self.zoom_slider.setEnabled(fill)
        self.x_slider.setEnabled(fill)
        self.y_slider.setEnabled(fill)
        self.center_crop_button.setEnabled(fill)
        for button in self.crop_anchor_group.buttons():
            button.setEnabled(fill)
        self._settings_changed()

    def _zoom_changed(self, value: int) -> None:
        self.zoom_value.setText(f"{value} %")
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

    def _update_auto_output_extension(self) -> None:
        if self.info and self._auto_output_path:
            suffix = self._settings().suffix
            self.output_path.setText(str(self.info.path.with_name(self.info.path.stem + "_beautycut" + suffix)))

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
        current = self.output_path.text().strip()
        start = current or (str(self.info.path.with_suffix(self._settings().suffix)) if self.info else "")
        name, _ = QFileDialog.getSaveFileName(
            self, "Choisir le fichier de sortie", start,
            f"{self.container_combo.currentText()} (*{self._settings().suffix});;Tous les fichiers (*.*)",
        )
        if name:
            path = Path(name)
            if path.suffix.lower() != self._settings().suffix:
                path = path.with_suffix(self._settings().suffix)
            self.output_path.setText(str(path))
            self._auto_output_path = False

    def _start_export(self) -> None:
        if not self.info:
            self.status_label.setText("Importez d’abord un média.")
            return
        report = self.report
        if report and not report.supported:
            self.status_label.setText("Le fichier contient un codec absent de cette installation FFmpeg.")
            return
        target_text = self.output_path.text().strip()
        if not target_text:
            self.status_label.setText("Choisissez un fichier de sortie.")
            return
        target = Path(target_text).expanduser()
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
        self.import_button.setEnabled(False)
        self.progress.setValue(0)
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
        self.compatibility.setText(message)
        self.compatibility.setStyleSheet("color: #ff9a9a;")

    def _export_cancelled(self) -> None:
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
        self.import_button.setEnabled(True)
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


