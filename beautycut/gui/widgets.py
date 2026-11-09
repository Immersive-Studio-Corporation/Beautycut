"""Composants d'interface réutilisables de BeautyCut.

Tous les composants s'appuient exclusivement sur les jetons de couleur du thème
actif (voir ``themes.py``) : aucune couleur n'est codée en dur ici.
"""

from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSettings,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .themes import THEME_BY_KEY


def asset_path(relative: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / "assets" / relative


def theme_tokens() -> dict[str, str]:
    """Retourne les jetons du thème actif, utilisables dans une feuille de style."""
    try:
        settings = QSettings("BeautyCut", "BeautyCut")
        key = str(settings.value("appearance/theme", "northern_lights"))
    except Exception:  # pragma: no cover - QSettings indisponible (tests isolés)
        key = "northern_lights"
    theme = THEME_BY_KEY.get(key, next(iter(THEME_BY_KEY.values())))
    tokens = {field: getattr(theme, field) for field in theme.__dataclass_fields__ if field != "name"}
    tokens["name"] = theme.name
    return tokens


# ---------------------------------------------------------------------------
# Navigation latérale : sections repliables indépendantes, sans double défilement
# ---------------------------------------------------------------------------


class SectionHeader(QToolButton):
    """En-tête cliquable d'une section, avec chevron animé et compteur optionnel."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("sectionHeader")
        self.setCheckable(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(f"Section {title}")
        self.setMinimumHeight(38)
        self.setText(title)
        self._title = title
        self.setArrowType(Qt.ArrowType.RightArrow)

    def title(self) -> str:
        return self._title

    def set_badge(self, text: str) -> None:
        """Affiche une information compacte à droite du titre (résumé de section)."""
        self._title = self._title.split("  ·  ")[0]
        self.setText(f"{self._title}  ·  {text}" if text else self._title)


class CollapsibleSection(QFrame):
    """Section repliable indépendante.

    Une seule section peut être dépliée dans le panneau latéral (mode accordéon),
    ce qui évite les longues colonnes à parcourir. Les sections avancées restent
    accessibles à tout moment.
    """

    expandedChanged = Signal(bool)

    def __init__(
        self,
        title: str,
        *,
        expanded: bool = False,
        icon: str = "",
        summary: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("collapsibleSection")
        self._title = title
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 5 if summary else 6, 8, 5 if summary else 7)
        layout.setSpacing(0 if summary else 4)
        self.header = SectionHeader(title)
        if summary:
            self.header.setFixedHeight(29)
            self.header.setProperty("compactSummary", True)
        if icon and asset_path(icon).is_file():
            self.header.setIcon(QIcon(str(asset_path(icon))))
            self.header.setIconSize(QSize(18, 18))
        layout.addWidget(self.header)
        self.summary_label: QLabel | None = None
        if summary:
            self.summary_label = QLabel(summary)
            self.summary_label.setObjectName("sectionSummary")
            self.summary_label.setContentsMargins(50, 0, 4, 0)
            self.summary_label.setWordWrap(False)
            layout.addWidget(self.summary_label)
        self.body = QWidget()
        self.body.setObjectName("sectionBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(2, 3, 2, 2)
        self.body_layout.setSpacing(7)
        layout.addWidget(self.body)
        self.header.toggled.connect(self._set_expanded)
        self.header.setChecked(expanded)
        self._set_expanded(expanded)

    def content_layout(self) -> QVBoxLayout:
        return self.body_layout

    def title(self) -> str:
        return self._title

    def is_expanded(self) -> bool:
        return self.header.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        self.header.setChecked(expanded)

    def set_summary(self, text: str) -> None:
        self.header.set_badge(text)

    def _set_expanded(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        if self.summary_label is not None:
            self.summary_label.setVisible(not expanded)
        self.header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.header.setAccessibleDescription("Réduire la section" if expanded else "Développer la section")
        self.setProperty("expanded", expanded)
        self.style().unpolish(self)
        self.style().polish(self)
        self.expandedChanged.emit(expanded)


class SmoothScrollArea(QScrollArea):
    """Zone de défilement à molette lissée (utilisée uniquement si nécessaire)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scroll_animation = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._scroll_animation.setDuration(220)
        self._scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        scrollbar = self.verticalScrollBar()
        if scrollbar.maximum() <= 0:
            event.ignore()
            return
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


class EllipsisLabel(QLabel):
    """Étiquette d'une seule ligne qui abrège le texte au lieu de déborder."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        super().setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        self._full_text = text
        self.setToolTip(text if len(text) > 28 else "")
        self._elide()

    def full_text(self) -> str:
        return self._full_text

    def _elide(self) -> None:
        metrics = self.fontMetrics()
        available = max(20, self.width() - 4)
        super().setText(metrics.elidedText(self._full_text, Qt.TextElideMode.ElideMiddle, available))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._elide()


# ---------------------------------------------------------------------------
# Aperçus
# ---------------------------------------------------------------------------


class PreviewCanvas(QWidget):
    """Canevas d'aperçu : image, cadre de recadrage, grille, zoom et déplacement.

    Les aperçus restent légers : aucun décodage vidéo n'est fait ici, l'image
    repère est produite une seule fois par FFmpeg puis réutilisée.
    """

    crop_position_changed = Signal(int, int)
    crop_zoom_changed = Signal(float)
    zoom_changed = Signal(float)
    pan_changed = Signal(float, float)
    view_interaction = Signal()

    HANDLE = 9

    def __init__(self, placeholder: str, *, interactive: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("previewCanvas")
        self.setMinimumSize(150, 150)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName(placeholder)
        self._placeholder = placeholder
        self._pixmap: QPixmap | None = None
        self._interactive = interactive
        self._target_aspect = 9 / 16
        self._fit_mode = "fill"
        self._zoom = 1.0
        default_theme = THEME_BY_KEY["northern_lights"]
        self._preview_background = default_theme.preview
        self._crop_accent = default_theme.accent_light
        self._outline = default_theme.border
        self._text_color = default_theme.muted
        self._grid_visible = True
        self._safe_guides = False
        self._thirds = True
        self._crop_x = 50
        self._crop_y = 50
        self._drag_start: QPointF | None = None
        self._drag_position = (50, 50)
        self._resize_start: QPointF | None = None
        self._resize_zoom = 1.0
        self._resize_radius = 1.0
        self._view_zoom = 1.0
        self._pan = QPointF(0, 0)
        self._pan_start: QPointF | None = None
        self._hovering_handle = False
        if interactive:
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    # -- état ---------------------------------------------------------------
    def set_image(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.update()

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        self._pixmap = None
        self.update()

    def placeholder_text(self) -> str:
        return self._placeholder

    def set_crop(self, aspect: float, x: int, y: int, fit_mode: str, zoom: float) -> None:
        self._target_aspect = max(0.01, aspect)
        self._crop_x = max(0, min(100, x))
        self._crop_y = max(0, min(100, y))
        self._fit_mode = fit_mode
        self._zoom = max(1.0, zoom)
        self._update_cursor()
        self.update()

    def set_theme(self, theme) -> None:
        self._preview_background = theme.preview
        self._crop_accent = theme.accent_light
        self._outline = theme.border
        self._text_color = theme.muted
        self.update()

    def set_theme_colors(self, preview_background: str, accent: str) -> None:
        self._preview_background = preview_background
        self._crop_accent = accent
        self.update()

    def set_grid_visible(self, visible: bool) -> None:
        self._grid_visible = visible
        self.update()

    def set_guides_visible(self, visible: bool) -> None:
        self._safe_guides = visible
        self.update()

    def set_view_zoom(self, zoom: float) -> None:
        self._view_zoom = max(1.0, min(4.0, zoom))
        if self._view_zoom <= 1.001:
            self._pan = QPointF(0, 0)
        self.update()

    def view_zoom(self) -> float:
        return self._view_zoom

    def reset_view(self) -> None:
        self._view_zoom = 1.0
        self._pan = QPointF(0, 0)
        self.update()

    def recenter(self) -> None:
        """Recentrer le cadre de recadrage et remettre la vue à l'échelle."""
        self.reset_view()
        self._crop_x = self._crop_y = 50
        self.crop_position_changed.emit(50, 50)
        self.update()

    # -- géométrie ----------------------------------------------------------
    def _frame_rect(self) -> QRectF:
        """Rectangle du média; un aperçu vide garde une zone large et lisible."""
        if self._pixmap is not None and not self._pixmap.isNull():
            ratio = self._pixmap.width() / max(1, self._pixmap.height())
        else:
            # Empty-state previews still use the full viewing area. Letterboxing
            # an imaginary 16:9 frame made the editor look like two inset boxes.
            return QRectF(self.contentsRect())
        available = QRectF(self.contentsRect()).adjusted(10, 10, -10, -10)
        if available.width() <= 0 or available.height() <= 0:
            return QRectF()
        if available.width() / available.height() > ratio:
            height = available.height()
            width = height * ratio
        else:
            width = available.width()
            height = width / ratio
        rect = QRectF(
            available.center().x() - width / 2,
            available.center().y() - height / 2,
            width,
            height,
        )
        if self._view_zoom > 1.001:
            rect = QRectF(
                rect.center().x() - rect.width() * self._view_zoom / 2 + self._pan.x(),
                rect.center().y() - rect.height() * self._view_zoom / 2 + self._pan.y(),
                rect.width() * self._view_zoom,
                rect.height() * self._view_zoom,
            )
        return rect

    def _image_rect(self) -> QRectF | None:
        if not self._pixmap or self._pixmap.isNull():
            return None
        return self._frame_rect()

    def _crop_rect(self) -> QRectF | None:
        image = self._image_rect()
        if image is None or image.width() <= 0 or image.height() <= 0:
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

    def _crop_interactive(self) -> bool:
        return self._interactive and self._fit_mode == "fill" and self._pixmap is not None

    def _corner_at(self, point: QPointF) -> int | None:
        crop = self._crop_rect()
        if crop is None or not self._crop_interactive():
            return None
        for index, (x, y) in enumerate((
            (crop.left(), crop.top()), (crop.right(), crop.top()),
            (crop.left(), crop.bottom()), (crop.right(), crop.bottom()),
        )):
            if abs(point.x() - x) <= self.HANDLE and abs(point.y() - y) <= self.HANDLE:
                return index
        return None

    def _handle_at(self, point: QPointF) -> bool:
        return self._corner_at(point) is not None

    def _update_cursor(self) -> None:
        if not self._interactive:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if self._fit_mode != "fill":
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif self._view_zoom > 1.001:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    # -- dessin -------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), QColor(self._preview_background))
        frame = self._frame_rect()
        if frame.isNull() or frame.width() <= 0 or frame.height() <= 0:
            return
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(frame, 8, 8)
        painter.setClipPath(clip)
        if self._pixmap is not None and not self._pixmap.isNull():
            painter.drawPixmap(frame, self._pixmap, QRectF(self._pixmap.rect()))
        else:
            painter.setPen(QPen(QColor(self._text_color)))
            font = QFont(self.font())
            font.setPointSizeF(max(8.5, font.pointSizeF() - 0.5))
            painter.setFont(font)
            painter.drawText(frame.adjusted(18, 18, -18, -18), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._placeholder)
        grid_rect = frame
        if self._crop_interactive():
            crop = self._crop_rect()
            if crop is not None:
                # Zones coupées : assombries et légèrement désaturées visuellement.
                shade = QColor(self._preview_background)
                shade.setAlpha(165)
                painter.fillRect(QRectF(frame.left(), frame.top(), frame.width(), max(0.0, crop.top() - frame.top())), shade)
                painter.fillRect(QRectF(frame.left(), crop.bottom(), frame.width(), max(0.0, frame.bottom() - crop.bottom())), shade)
                painter.fillRect(QRectF(frame.left(), crop.top(), max(0.0, crop.left() - frame.left()), crop.height()), shade)
                painter.fillRect(QRectF(crop.right(), crop.top(), max(0.0, frame.right() - crop.right()), crop.height()), shade)
                accent = QColor(self._crop_accent)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(0, 0, 0, 90), 4))
                painter.drawRect(crop)
                painter.setPen(QPen(accent, 2))
                painter.drawRect(crop)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(accent)
                for x, y in ((crop.left(), crop.top()), (crop.right(), crop.top()),
                             (crop.left(), crop.bottom()), (crop.right(), crop.bottom())):
                    painter.drawRoundedRect(
                        QRectF(x - self.HANDLE / 2, y - self.HANDLE / 2, self.HANDLE, self.HANDLE), 2, 2
                    )
                grid_rect = crop
        if self._pixmap is not None and self._grid_visible and self._thirds:
            grid_color = QColor(self._crop_accent)
            grid_color.setAlpha(150)
            painter.setPen(QPen(grid_color, 1, Qt.PenStyle.DashLine))
            for division in (1, 2):
                x = grid_rect.left() + grid_rect.width() * division / 3
                y = grid_rect.top() + grid_rect.height() * division / 3
                painter.drawLine(QPointF(x, grid_rect.top()), QPointF(x, grid_rect.bottom()))
                painter.drawLine(QPointF(grid_rect.left(), y), QPointF(grid_rect.right(), y))
        if self._pixmap is not None and self._safe_guides:
            guide = QColor(self._text_color)
            guide.setAlpha(140)
            painter.setPen(QPen(guide, 1, Qt.PenStyle.DotLine))
            inset_x = grid_rect.width() * 0.05
            inset_y = grid_rect.height() * 0.05
            painter.drawRect(grid_rect.adjusted(inset_x, inset_y, -inset_x, -inset_y))
            painter.drawEllipse(grid_rect.center(), grid_rect.width() * 0.42, grid_rect.height() * 0.42)
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(self._outline), 1))
        painter.drawRoundedRect(frame, 8, 8)
        if self._view_zoom > 1.001:
            painter.setPen(QPen(QColor(self._text_color)))
            painter.drawText(
                self.contentsRect().adjusted(8, 6, -8, -8),
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
                f"Zoom aperçu ×{self._view_zoom:.1f}",
            )

    # -- interaction --------------------------------------------------------
    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self._interactive:
            event.ignore()
            return
        modifiers = event.modifiers()
        steps = event.angleDelta().y() / 120
        if not steps:
            event.ignore()
            return
        if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            self.set_view_zoom(self._view_zoom * (1.15 if steps > 0 else 1 / 1.15))
            self.view_interaction.emit()
        else:
            crop = self._crop_rect()
            frame = self._frame_rect()
            if crop is None or frame.isNull() or frame.width() <= 0:
                event.ignore()
                return
            step = 2
            if modifiers & Qt.KeyboardModifier.AltModifier:
                self._crop_y = max(0, min(100, self._crop_y - step * (1 if steps > 0 else -1)))
                self.crop_position_changed.emit(self._crop_x, self._crop_y)
            else:
                self._crop_x = max(0, min(100, self._crop_x - step * (1 if steps > 0 else -1)))
                self.crop_position_changed.emit(self._crop_x, self._crop_y)
            self.view_interaction.emit()
        self.update()
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        point = event.position()
        if self._view_zoom > 1.001 and self._interactive:
            self._pan_start = point - self._pan
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if not self._crop_interactive():
            super().mousePressEvent(event)
            return
        frame, crop = self._frame_rect(), self._crop_rect()
        if frame.isNull() or crop is None or not frame.contains(point):
            return
        corner = self._corner_at(point)
        if corner is not None:
            self._resize_start = point
            self._resize_zoom = self._zoom
            self._resize_radius = max(1.0, (point - crop.center()).manhattanLength() / 1.41421356237)
            self.setCursor(
                Qt.CursorShape.SizeFDiagCursor if corner in (0, 3) else Qt.CursorShape.SizeBDiagCursor
            )
            event.accept()
            return
        span_x = max(1.0, frame.width() - crop.width())
        span_y = max(1.0, frame.height() - crop.height())
        if not crop.contains(point):
            self._crop_x = round(max(0.0, min(1.0, (point.x() - crop.width() / 2 - frame.left()) / span_x)) * 100)
            self._crop_y = round(max(0.0, min(1.0, (point.y() - crop.height() / 2 - frame.top()) / span_y)) * 100)
            self.crop_position_changed.emit(self._crop_x, self._crop_y)
        self._drag_start = point
        self._drag_position = (self._crop_x, self._crop_y)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._pan_start is not None:
            self._pan = event.position() - self._pan_start
            self.update()
            self.pan_changed.emit(self._pan.x(), self._pan.y())
            event.accept()
            return
        if self._resize_start is not None:
            crop = self._crop_rect()
            if crop is None:
                return
            radius = max(1.0, (event.position() - crop.center()).manhattanLength() / 1.41421356237)
            zoom = max(1.0, min(2.0, self._resize_zoom * self._resize_radius / radius))
            if abs(zoom - self._zoom) >= 0.004:
                self._zoom = zoom
                self.crop_zoom_changed.emit(zoom)
                self.update()
            event.accept()
            return
        if self._drag_start is None:
            if self._interactive:
                corner = self._corner_at(event.position())
                cursor = (
                    Qt.CursorShape.SizeFDiagCursor if corner in (0, 3) else
                    Qt.CursorShape.SizeBDiagCursor if corner in (1, 2) else
                    Qt.CursorShape.OpenHandCursor
                )
                self.setCursor(cursor)
            super().mouseMoveEvent(event)
            return
        frame, crop = self._frame_rect(), self._crop_rect()
        if frame.isNull() or crop is None:
            return
        span_x = max(1.0, frame.width() - crop.width())
        span_y = max(1.0, frame.height() - crop.height())
        dx = event.position().x() - self._drag_start.x()
        dy = event.position().y() - self._drag_start.y()
        x = max(0, min(100, self._drag_position[0] + round(dx * 100 / span_x)))
        y = max(0, min(100, self._drag_position[1] + round(dy * 100 / span_y)))
        if (x, y) != (self._crop_x, self._crop_y):
            self._crop_x, self._crop_y = x, y
            self.crop_position_changed.emit(x, y)
            self.update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and (
            self._drag_start is not None or self._pan_start is not None or self._resize_start is not None
        ):
            self._drag_start = None
            self._pan_start = None
            self._resize_start = None
            self._update_cursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self._interactive:
            super().keyPressEvent(event)
            return
        step = 1
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            step = 10
        moves = {
            Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
            Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step),
        }
        if event.key() in moves:
            dx, dy = moves[event.key()]
            self._crop_x = max(0, min(100, self._crop_x + dx))
            self._crop_y = max(0, min(100, self._crop_y + dy))
            self.crop_position_changed.emit(self._crop_x, self._crop_y)
            self.update()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.set_view_zoom(self._view_zoom * 1.15)
            self.view_interaction.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Minus:
            self.set_view_zoom(self._view_zoom / 1.15)
            self.view_interaction.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_0:
            self.reset_view()
            self.view_interaction.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class BeforeAfterCanvas(QWidget):
    """Curseur de comparaison entre l'image source et l'aperçu FFmpeg réel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("comparisonCanvas")
        self.setMinimumSize(320, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Comparaison avant après, curseur déplaçable")
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self._source: QPixmap | None = None
        self._result: QPixmap | None = None
        self._aspect = 9 / 16
        self._position = 0.5
        self._background = THEME_BY_KEY["deep_ocean"].preview
        self._accent = THEME_BY_KEY["deep_ocean"].accent_light
        self._text = THEME_BY_KEY["deep_ocean"].text
        self._dragging = False

    def set_images(self, source: QPixmap, result: QPixmap) -> None:
        self._source = source
        self._result = result
        if not source.isNull():
            self._aspect = source.width() / max(1, source.height())
        self.update()

    def set_theme(self, theme) -> None:
        self._background = theme.preview
        self._accent = theme.accent_light
        self._text = theme.text
        self.update()

    def _frame_rect(self) -> QRectF:
        available = QRectF(self.rect()).adjusted(12, 12, -12, -12)
        if available.width() <= 0 or available.height() <= 0:
            return QRectF()
        if available.width() / available.height() > self._aspect:
            width = available.height() * self._aspect
            return QRectF(available.center().x() - width / 2, available.top(), width, available.height())
        height = available.width() / self._aspect
        return QRectF(available.left(), available.center().y() - height / 2, available.width(), height)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), QColor(self._background))
        frame = self._frame_rect()
        if frame.isNull():
            return
        clip = QPainterPath()
        clip.addRoundedRect(frame, 10, 10)
        painter.save()
        painter.setClipPath(clip)
        painter.fillRect(frame, QColor(self._background))
        if self._source is not None and not self._source.isNull():
            painter.drawPixmap(frame, self._source, QRectF(self._source.rect()))
        split = frame.left() + frame.width() * self._position
        if self._result is not None and not self._result.isNull():
            painter.save()
            painter.setClipRect(QRectF(split, frame.top(), frame.right() - split, frame.height()))
            result_aspect = self._result.width() / max(1, self._result.height())
            if result_aspect > frame.width() / max(1.0, frame.height()):
                result_width = frame.width()
                result_height = result_width / result_aspect
            else:
                result_height = frame.height()
                result_width = result_height * result_aspect
            result_frame = QRectF(0, 0, result_width, result_height)
            result_frame.moveCenter(frame.center())
            painter.drawPixmap(result_frame, self._result, QRectF(self._result.rect()))
            painter.restore()
        painter.restore()

        painter.setPen(QPen(QColor(self._accent), 2))
        painter.drawLine(QPointF(split, frame.top()), QPointF(split, frame.bottom()))
        handle = QRectF(split - 15, frame.center().y() - 19, 30, 38)
        painter.setPen(QPen(QColor(self._accent), 1))
        painter.setBrush(QColor(self._background))
        painter.drawRoundedRect(handle, 11, 11)
        painter.setPen(QPen(QColor(self._accent), 1.7))
        center = frame.center().y()
        painter.drawLine(QPointF(split - 4, center - 5), QPointF(split - 8, center))
        painter.drawLine(QPointF(split - 8, center), QPointF(split - 4, center + 5))
        painter.drawLine(QPointF(split + 4, center - 5), QPointF(split + 8, center))
        painter.drawLine(QPointF(split + 8, center), QPointF(split + 4, center + 5))

        font = QFont(self.font())
        font.setPointSizeF(max(8, font.pointSizeF() - 1))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(self._text))
        painter.setBrush(QColor(self._background))
        before = QRectF(frame.left() + 10, frame.top() + 10, 102, 25)
        after = QRectF(frame.right() - 112, frame.top() + 10, 102, 25)
        painter.drawRoundedRect(before, 7, 7)
        painter.drawRoundedRect(after, 7, 7)
        painter.drawText(before, Qt.AlignmentFlag.AlignCenter, "ORIGINAL")
        painter.drawText(after, Qt.AlignmentFlag.AlignCenter, "RÉSULTAT")
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(self._accent), 1))
        painter.drawRoundedRect(frame, 10, 10)

    def _move_divider(self, x: float) -> None:
        frame = self._frame_rect()
        if frame.width() <= 0:
            return
        self._position = max(0.03, min(0.97, (x - frame.left()) / frame.width()))
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._move_divider(event.position().x())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._dragging:
            self._move_divider(event.position().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = 0.01 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.04
            self._position = max(0.03, min(0.97, self._position + (step if event.key() == Qt.Key.Key_Right else -step)))
            self.update()
            event.accept()
            return
        super().keyPressEvent(event)


class PreviewPanel(QFrame):
    """Carte d'aperçu avec un titre illustré, zoom par menu et plein écran."""

    def __init__(
        self,
        title: str,
        aspect: str,
        *,
        crop_interactive: bool = False,
        icon_path: str | None = None,
        icon_tone: str = "accent",
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("previewCard")
        self._aspect = aspect
        self._icon_path = icon_path
        self._icon_tone = icon_tone
        self.setMinimumHeight(270)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        # The reference editor gives most of the preview card to the image;
        # keep the canvas almost flush with the workspace edge.
        layout.setContentsMargins(0, 0, 0, 15)
        layout.setSpacing(12)
        self.title = QLabel(title)
        self.title.setObjectName("previewTitle")
        self.title.setWordWrap(False)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title_bar = QWidget()
        self.title_bar.setObjectName("previewHeader")
        title_row = QHBoxLayout(self.title_bar)
        title_row.setContentsMargins(12, 0, 7, 0)
        title_row.setSpacing(9)
        self.title_bar.setMinimumHeight(53)
        self.title_icon = QLabel()
        self.title_icon.setObjectName("previewTitleIcon")
        self.title_icon.setFixedSize(23, 23)
        if icon_path:
            self.title_icon.setProperty("iconPath", icon_path)
        title_row.addWidget(self.title_icon)
        title_row.addWidget(self.title, 1)
        self.toolbar = QWidget()
        self.toolbar.setObjectName("previewToolbar")
        tools = QHBoxLayout(self.toolbar)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(4)

        self.zoom_reset_button = self._tool(
            "100 %", "Choisir le niveau de zoom de l’aperçu", icon_path="icons/search.svg"
        )
        self.zoom_reset_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.zoom_reset_button.setMinimumWidth(108)
        self.zoom_reset_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.zoom_menu = QMenu(self.zoom_reset_button)
        self.zoom_menu_actions = {}
        for percent in (100, 125, 150, 200, 300, 400):
            action = self.zoom_menu.addAction(f"{percent} %")
            action.triggered.connect(
                lambda checked=False, scale=percent / 100: self._set_zoom(scale)
            )
            self.zoom_menu_actions[percent] = action
        self.zoom_reset_button.setMenu(self.zoom_menu)
        self.fullscreen_button = self._tool("", "Ouvrir l’aperçu en plein écran", icon_path="icons/fullscreen.svg")
        for widget in (self.zoom_reset_button, self.fullscreen_button):
            tools.addWidget(widget)
        title_row.addWidget(self.toolbar)
        self.canvas = PreviewCanvas(
            "Importez un média pour afficher une image repère", interactive=crop_interactive
        )
        self.canvas.setMinimumSize(150, 150)
        layout.addWidget(self.title_bar)
        layout.addWidget(self.canvas, 1)
        self.fullscreen_button.clicked.connect(self._open_fullscreen)
        self.canvas.zoom_changed.connect(lambda _z: self._update_zoom_label())
        self.canvas.view_interaction.connect(self._update_zoom_label)
        self.canvas.set_view_zoom(1.0)
        self._fullscreen_dialog: QDialog | None = None
        self._apply_tool_visibility()

    def _tool(
        self,
        text: str,
        tooltip: str,
        *,
        checkable: bool = False,
        checked: bool = False,
        icon_path: str | None = None,
    ) -> QToolButton:
        button = QToolButton()
        button.setObjectName("previewToolButton")
        button.setText(text)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setCheckable(checkable)
        button.setChecked(checked)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if icon_path:
            button.setProperty("iconPath", icon_path)
            button.setIcon(QIcon(str(asset_path(icon_path))))
            button.setIconSize(QSize(16, 16))
        return button

    def _apply_tool_visibility(self) -> None:
        self.zoom_reset_button.setVisible(True)

    def _set_zoom(self, scale: float) -> None:
        self.canvas.set_view_zoom(scale)
        self._update_zoom_label()

    def _update_zoom_label(self, *_args) -> None:
        self.zoom_reset_button.setText(f"{round(self.canvas.view_zoom() * 100)} %")

    def set_image(self, data: bytes) -> None:
        pixmap = QPixmap()
        if not pixmap.loadFromData(data, "JPEG"):
            self.set_placeholder("L’image repère n’a pas pu être décodée.")
            return
        self.canvas.set_image(pixmap)

    def set_placeholder(self, text: str) -> None:
        self.canvas.set_placeholder(text)

    def set_theme(self, theme) -> None:
        self.canvas.set_theme(theme)
        if self._icon_path:
            color = theme.success if self._icon_tone == "success" else theme.accent_light
            self.title_icon.setPixmap(tinted_icon(self._icon_path, color, 22).pixmap(22, 22))
        for button in self.findChildren(QToolButton, "previewToolButton"):
            icon_path = button.property("iconPath")
            if icon_path:
                button.setIcon(tinted_icon(icon_path, theme.accent_light, 16))

    def set_title(self, text: str) -> None:
        self.title.setText(text)

    def _open_fullscreen(self) -> None:
        dialog = QDialog(self)
        dialog.setObjectName("fullscreenDialog")
        dialog.setWindowTitle(self.title.text())
        dialog.setWindowFlag(Qt.WindowType.Window, True)
        dialog.setStyleSheet(self.window().styleSheet() if self.window() else "")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        header = QHBoxLayout()
        label = QLabel(self.title.text())
        label.setObjectName("previewTitle")
        header.addWidget(label, 1)
        close_button = self._tool("Fermer", "Fermer le plein écran")
        close_button.clicked.connect(dialog.close)
        header.addWidget(close_button)
        layout.addLayout(header)
        canvas = PreviewCanvas(self.canvas.placeholder_text(), interactive=self.canvas._interactive, parent=dialog)
        canvas.set_theme_colors(self.canvas._preview_background, self.canvas._crop_accent)
        canvas.set_grid_visible(self.canvas._grid_visible)
        canvas.set_guides_visible(self.canvas._safe_guides)
        canvas.set_crop(
            self.canvas._target_aspect,
            self.canvas._crop_x,
            self.canvas._crop_y,
            self.canvas._fit_mode,
            self.canvas._zoom,
        )
        if self.canvas._pixmap is not None:
            canvas.set_image(self.canvas._pixmap)
        if self.canvas._interactive:
            canvas.crop_position_changed.connect(self.canvas.crop_position_changed)
        layout.addWidget(canvas, 1)
        self._fullscreen_dialog = dialog
        dialog.showFullScreen()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply_tool_visibility()
        self.canvas.update()


# ---------------------------------------------------------------------------
# Barre d'export (zone inférieure fixe)
# ---------------------------------------------------------------------------


class ExportDock(QFrame):
    """Conteneur visuel de la barre d'export fixe, toujours visible."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("exportDock")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)


class StatusPill(QLabel):
    """Petit badge d'état (moteur, compatibilité, export) piloté par le thème."""

    TONES = {"info": "info", "success": "success", "warning": "warning", "error": "error", "busy": "info"}

    def __init__(self, text: str = "", tone: str = "info", parent=None):
        super().__init__(text, parent)
        self.setObjectName("statusPill")
        self.setProperty("tone", tone)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)

    def set_state(self, text: str, tone: str = "info") -> None:
        self.setText(text)
        tone = self.TONES.get(tone, "info")
        if self.property("tone") != tone:
            self.setProperty("tone", tone)
            self.style().unpolish(self)
            self.style().polish(self)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        hint = super().sizeHint()
        return QSize(hint.width(), hint.height())


def tinted_icon(icon_path: str, color: str, size: int = 22) -> QIcon:
    """Repeint une icône SVG mono-chrome avec la couleur du thème actif."""
    source = QPixmap(str(asset_path(icon_path)))
    if source.isNull():
        return QIcon()
    source = source.scaled(
        size * 3, size * 3, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    tinted = QPixmap(source.size())
    tinted.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tinted)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.drawPixmap(0, 0, source)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), QColor(color))
    painter.end()
    return QIcon(tinted)


def ui_scale() -> float:
    """Échelle de l'écran principal, bornée pour rester lisible."""
    screen = QApplication.primaryScreen()
    if screen is None:
        return 1.0
    try:
        return max(1.0, min(2.0, screen.logicalDotsPerInch() / 96))
    except Exception:  # pragma: no cover
        return 1.0


def is_windows() -> bool:
    return sys.platform == "win32"


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    whole = int(seconds)
    hours, rem = divmod(whole, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02}:{minutes:02}:{secs:02}" if hours else f"{minutes:02}:{secs:02}"


__all__ = [
    "BeforeAfterCanvas",
    "CollapsibleSection",
    "EllipsisLabel",
    "ExportDock",
    "PreviewCanvas",
    "PreviewPanel",
    "SectionHeader",
    "SmoothScrollArea",
    "StatusPill",
    "asset_path",
    "clamp",
    "format_duration",
    "is_windows",
    "theme_tokens",
    "tinted_icon",
    "ui_scale",
]
