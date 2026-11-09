from __future__ import annotations

import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from beautycut.media.export import ExportProgress
from beautycut.gui.main_window import MainWindow
from beautycut.gui.themes import THEMES, THEME_BY_KEY


class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.window = MainWindow()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_window_has_working_import_and_export_workspace(self) -> None:
        self.assertEqual(self.window.windowTitle(), "BeautyCut")
        self.assertTrue(self.window.import_button.isEnabled())
        self.assertFalse(self.window.export_button.isEnabled())
        self.assertEqual(set(self.window.profile_buttons), {"vertical", "portrait", "square", "landscape", "custom"})
        self.assertTrue(self.window.profile_buttons["vertical"].isChecked())
        self.assertFalse(self.window.profile_buttons["vertical"].icon().isNull())
        self.assertFalse(self.window.windowIcon().isNull())
        self.assertEqual(self.window.theme_combo.count(), 20)
        self.assertIn(THEME_BY_KEY[self.window.theme_key].muted, self.window.styleSheet())
        self.assertIn("QPushButton#primaryButton:disabled", self.window.styleSheet())

    def test_twenty_themes_persist_and_apply_without_rebuilding_the_window(self) -> None:
        original = self.window.theme_key
        self.assertEqual(len(THEMES), 20)
        self.window.theme_combo.setCurrentIndex(self.window.theme_combo.findData("nebula"))
        self.assertEqual(self.window.theme_key, "nebula")
        self.assertIn(THEME_BY_KEY["nebula"].accent, self.window.styleSheet())
        self.window.theme_combo.setCurrentIndex(self.window.theme_combo.findData(original))

    def test_duration_presets_enable_numbered_segment_export_settings(self) -> None:
        self.assertFalse(self.window.segment_checkbox.isChecked())
        index = self.window.segment_target_combo.findData(600)
        self.window.segment_target_combo.setCurrentIndex(index)
        self.window.segment_checkbox.setChecked(True)
        settings = self.window._settings()
        self.assertTrue(settings.split_enabled)
        self.assertEqual(settings.split_limit_seconds, 600)
        self.assertEqual(self.window.segment_limit_minutes_spin.value(), 10)

    def test_reduced_window_reflows_previews_and_keeps_workspace_scrollable(self) -> None:
        self.assertEqual((self.window.minimumWidth(), self.window.minimumHeight()), (720, 460))
        self.window.resize(720, 460)
        self.window.show()
        QTest.qWait(80)
        self.app.processEvents()
        self.assertEqual(self.window.preview_layout.direction().name, "TopToBottom")
        self.assertEqual(self.window.workspace_scroll._scroll_animation.duration(), 220)
        self.assertEqual(self.window.workspace_scroll.horizontalScrollBar().maximum(), 0)
        self.assertLessEqual(
            self.window.workspace_scroll.widget().width(),
            self.window.workspace_scroll.viewport().width(),
        )
        self.assertGreater(self.window.workspace_scroll.verticalScrollBar().maximum(), 0)
        self.assertFalse(self.window.original_preview.geometry().intersects(self.window.output_preview.geometry()))
        for preview in (self.window.original_preview, self.window.output_preview):
            self.assertFalse(preview.canvas.geometry().intersects(preview.caption.geometry()))

    def test_aspect_ratio_presets_and_custom_format_select_correct_sizes(self) -> None:
        self.window._select_profile("portrait")
        self.assertEqual(self.window._settings().width, 1080)
        self.assertEqual(self.window._settings().height, 1350)
        self.assertTrue(self.window.profile_buttons["portrait"].isChecked())
        self.window._select_profile("square")
        self.assertEqual(self.window._settings().width, 1080)
        self.assertEqual(self.window._settings().height, 1080)
        self.assertFalse(self.window.profile_buttons["portrait"].isChecked())
        self.window._select_profile("custom")
        self.assertFalse(self.window.custom_size_widget.isHidden())
        self.assertEqual((self.window.width_spin.value(), self.window.height_spin.value()), (1080, 1080))

    def test_crop_anchors_zoom_and_locked_custom_ratio(self) -> None:
        self.window._set_crop_position(0, 100)
        self.assertEqual(self.window._settings().crop_x, -1)
        self.assertEqual(self.window._settings().crop_y, 1)
        self.window._select_profile("custom")
        self.window.width_spin.setValue(900)
        self.assertEqual((self.window.width_spin.value(), self.window.height_spin.value()), (900, 1600))
        self.window.lock_ratio_checkbox.setChecked(False)
        self.window.height_spin.setValue(1000)
        self.assertEqual(self.window.width_spin.value(), 900)
        self.window.zoom_slider.setValue(150)
        self.assertEqual(self.window._settings().crop_zoom, 1.5)

    def test_export_progress_shows_waiting_then_live_metrics(self) -> None:
        self.window._export_started_at = time.perf_counter()
        self.window._export_progress_updated(ExportProgress(stage="Analyse des codecs"))
        self.assertEqual((self.window.progress.minimum(), self.window.progress.maximum()), (0, 0))
        self.assertEqual(self.window.status_label.text(), "Analyse des codecs")
        self.window._export_progress_updated(ExportProgress(
            stage="Encodage en cours", percent=40, encoded_seconds=120, total_seconds=300,
            speed=1.25, fps=60, output_size_bytes=1024, eta_seconds=144,
        ))
        self.assertEqual((self.window.progress.minimum(), self.window.progress.maximum()), (0, 100))
        self.assertEqual(self.window.progress.value(), 40)
        self.assertIn("Traité 02:00 / 05:00", self.window.export_metrics.text())
        self.assertIn("Vitesse 1.25×", self.window.export_metrics.text())

    def test_dragging_the_preview_crop_updates_both_sliders(self) -> None:
        canvas = self.window.original_preview.canvas
        canvas.resize(640, 420)
        canvas.set_image(QPixmap(1280, 720))
        canvas.set_crop(9 / 16, 50, 50, "fill", 1.0)
        canvas.show()
        self.app.processEvents()
        crop = canvas._crop_rect()
        start = QPoint(int(crop.center().x()), int(crop.center().y()))
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(canvas, start + QPoint(50, 20), 20)
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=start + QPoint(50, 20))
        self.assertGreater(self.window.x_slider.value(), 50)
        self.assertGreater(self.window.y_slider.value(), 50)

    def test_switching_container_filters_codecs(self) -> None:
        self.window.container_combo.setCurrentText("WebM")
        videos = {self.window.video_codec_combo.itemText(index) for index in range(self.window.video_codec_combo.count())}
        audios = {self.window.audio_codec_combo.itemText(index) for index in range(self.window.audio_codec_combo.count())}
        self.assertTrue(videos <= {"AV1", "VP8", "VP9"})
        self.assertTrue(audios <= {"Opus", "Vorbis"})
        self.assertTrue(videos)
        self.assertTrue(audios)

    def test_manual_crop_position_labels_match_axis(self) -> None:
        self.assertEqual(self.window._position_label(0, False), "Gauche")
        self.assertEqual(self.window._position_label(100, False), "Droite")
        self.assertEqual(self.window._position_label(0, True), "Haut")
        self.assertEqual(self.window._position_label(100, True), "Bas")

    def test_quality_presets_apply_their_export_values(self) -> None:
        self.window.quality_preset_combo.setCurrentIndex(self.window.quality_preset_combo.findData("high"))
        self.assertEqual(self.window.quality_spin.value(), 18)
        self.assertFalse(self.window.quality_spin.isEnabled())
        self.window.quality_preset_combo.setCurrentIndex(self.window.quality_preset_combo.findData("custom"))
        self.assertTrue(self.window.quality_spin.isEnabled())
        self.window.quality_spin.setValue(25)
        self.assertEqual(self.window._settings().quality, 25)


if __name__ == "__main__":
    unittest.main()
