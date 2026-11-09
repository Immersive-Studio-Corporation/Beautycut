from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from beautycut.media.errors import MediaExportError
from beautycut.media.export import ExportSettings, MediaExporter, build_export_command, crop_filter, split_output_paths
from beautycut.media.probe import MediaProbe
from beautycut.media.tools import find_tool


class MediaPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="beautycut-tests-")
        cls.root = Path(cls.temp_dir.name)
        cls.source = cls.root / "source_multi_audio.mkv"
        cls.audio_source = cls.root / "audio_only.wav"
        ffmpeg = find_tool("ffmpeg")
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=1.2",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1.2",
            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=1.2",
            "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0", "-t", "1.2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-metadata:s:a:0", "language=fra", "-metadata:s:a:1", "language=eng", str(cls.source),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        if result.returncode:
            raise RuntimeError(f"Impossible de créer le média de test : {result.stderr}")
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2", str(cls.audio_source)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode:
            raise RuntimeError(f"Impossible de créer le fichier audio de test : {result.stderr}")
        cls.probe = MediaProbe(ffmpeg=ffmpeg)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_probe_reads_video_vfr_metadata_and_multiple_audio_tracks(self) -> None:
        info = self.probe.probe(self.source)
        self.assertEqual(info.primary_video.width, 640)
        self.assertEqual(info.primary_video.height, 360)
        self.assertEqual(info.primary_video.codec_name, "h264")
        self.assertEqual(len(info.audio_streams), 2)
        self.assertGreater(info.duration, 1.0)
        self.assertIn("matroska", info.container)

    def test_inventory_detects_real_ffmpeg_decoders_encoders_and_muxers(self) -> None:
        inventory = self.probe.available_codecs()
        self.assertIn("h264", inventory.decoders)
        self.assertTrue(inventory.video_encoders)
        self.assertIn("mp4", inventory.muxers)
        self.assertIn("webm", inventory.muxers)

    def test_compatibility_report_recognizes_multitrack_audio(self) -> None:
        info = self.probe.probe(self.source)
        report = self.probe.check_compatibility(info)
        self.assertTrue(report.supported)
        self.assertTrue(any("2 pistes audio" in message for message in report.messages))

    def test_crop_filter_has_requested_dimensions_and_manual_position(self) -> None:
        settings = ExportSettings(profile="custom", width=360, height=640, crop_x=0.5, crop_y=-0.25)
        result = crop_filter(settings)
        self.assertTrue(result.startswith("scale=360:640:"))
        self.assertIn("crop=360:640:", result)
        self.assertIn("0.5", result)
        self.assertIn("-0.25", result)

    def test_crop_filter_supports_zoom_and_full_frame_fit(self) -> None:
        zoomed = crop_filter(ExportSettings(profile="custom", width=360, height=640, crop_zoom=1.5))
        self.assertTrue(zoomed.startswith("scale=540:960:"))
        self.assertIn("crop=360:640:", zoomed)
        contained = crop_filter(ExportSettings(profile="custom", width=360, height=640, fit_mode="contain"))
        self.assertIn("force_original_aspect_ratio=decrease", contained)
        self.assertIn("pad=360:640", contained)

    def test_export_keeps_both_audio_tracks_and_outputs_requested_size(self) -> None:
        info = self.probe.probe(self.source)
        inventory = self.probe.available_codecs()
        settings = ExportSettings(profile="custom", width=360, height=640, quality=28, crop_zoom=1.25)
        target = self.root / "vertical.mp4"
        progress = []
        known_info = self.probe.probe(self.source)
        known_inventory = self.probe.available_codecs()
        output = MediaExporter(ffmpeg=find_tool("ffmpeg"), probe=self.probe).export(
            self.source, target, settings, overwrite=False, progress=progress.append, prefer_hardware=False,
            known_info=known_info, known_inventory=known_inventory,
        )
        self.assertEqual(output, target.resolve())
        exported = self.probe.probe(target)
        self.assertEqual((exported.primary_video.width, exported.primary_video.height), (360, 640))
        self.assertEqual(len(exported.audio_streams), 2)
        self.assertGreater(target.stat().st_size, 1000)
        self.assertEqual(progress[-1].stage, "Export terminé")
        self.assertEqual(progress[-1].percent, 100)
        self.assertGreaterEqual(progress[-1].elapsed_seconds, 0)
        self.assertTrue(any(item.total_seconds for item in progress))
        self.assertTrue(any(item.stage == "Vérification des pistes déjà analysées" for item in progress))

    def test_export_can_keep_entire_landscape_frame_in_portrait_canvas(self) -> None:
        target = self.root / "portrait_contain.mp4"
        MediaExporter(ffmpeg=find_tool("ffmpeg"), probe=self.probe).export(
            self.source, target,
            ExportSettings(profile="custom", width=360, height=640, fit_mode="contain"),
            prefer_hardware=False,
        )
        exported = self.probe.probe(target)
        self.assertEqual((exported.primary_video.width, exported.primary_video.height), (360, 640))

    def test_speed_preset_selects_a_faster_software_encoder_mode(self) -> None:
        inventory = self.probe.available_codecs()
        args = build_export_command(
            self.source, self.root / "fast.mp4", ExportSettings(speed="fast"), inventory, prefer_hardware=False,
        )
        self.assertIn("libx264", args)
        self.assertIn("veryfast", args)

    def test_export_command_maps_all_audio_streams(self) -> None:
        inventory = self.probe.available_codecs()
        args = build_export_command(self.source, self.root / "out.mp4", ExportSettings(), inventory)
        self.assertIn("0:a?", args)
        self.assertIn("-map_metadata", args)
        self.assertIn("-map_chapters", args)

    def test_segment_command_seeks_and_limits_each_output_part(self) -> None:
        inventory = self.probe.available_codecs()
        args = build_export_command(
            self.source, self.root / "part.mp4", ExportSettings(profile="custom", width=360, height=640),
            inventory, start_seconds=59, duration_seconds=60,
        )
        self.assertLess(args.index("-ss"), args.index("-i"))
        self.assertGreater(args.index("-t"), args.index("-i"))
        self.assertEqual(args[args.index("-ss") + 1], "59.000")
        self.assertEqual(args[args.index("-t") + 1], "60.000")

    def test_long_video_exports_as_numbered_parts_under_the_duration_limit(self) -> None:
        ffmpeg = find_tool("ffmpeg")
        source = self.root / "long_source.mp4"
        generated = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=5:duration=61.2",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=61.2",
                "-map", "0:v:0", "-map", "1:a:0", "-t", "61.2", "-threads", "1",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35",
                "-c:a", "aac", "-b:a", "48k", str(source),
            ],
            capture_output=True, text=True, timeout=60, check=False,
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        target = self.root / "long_export.mp4"
        settings = ExportSettings(
            profile="custom", width=180, height=320, quality=35,
            split_enabled=True, split_limit_seconds=60,
        )
        paths = split_output_paths(target, 61.2, 60)
        self.assertEqual([path.name for path in paths], [
            "long_export_partie_001.mp4", "long_export_partie_002.mp4",
        ])
        progress = []
        outputs = MediaExporter(ffmpeg=ffmpeg, probe=self.probe).export(
            source, target, settings, progress=progress.append, prefer_hardware=False,
        )
        self.assertEqual(outputs, paths)
        durations = [self.probe.probe(path).duration for path in outputs]
        self.assertTrue(all(duration is not None and duration <= 60.1 for duration in durations))
        self.assertGreater(sum(duration or 0 for duration in durations), 60.5)
        self.assertFalse(target.exists())
        self.assertEqual(progress[-1].stage, "Export terminé · 2 parties")
        self.assertEqual(progress[-1].percent, 100)

    def test_export_refuses_to_replace_existing_file_by_default(self) -> None:
        target = self.root / "existing.mp4"
        target.write_bytes(b"keep this")
        with self.assertRaises(MediaExportError):
            MediaExporter(ffmpeg=find_tool("ffmpeg"), probe=self.probe).export(
                self.source, target, ExportSettings(profile="custom", width=360, height=640),
            )
        self.assertEqual(target.read_bytes(), b"keep this")

    def test_export_rejects_odd_dimensions(self) -> None:
        with self.assertRaises(MediaExportError):
            ExportSettings(profile="custom", width=359, height=640).validate()

    def test_audio_only_import_and_export_do_not_create_a_video_stream(self) -> None:
        info = self.probe.probe(self.audio_source)
        self.assertTrue(info.is_audio_only)
        target = self.root / "audio_only.mp4"
        MediaExporter(ffmpeg=find_tool("ffmpeg"), probe=self.probe).export(
            self.audio_source, target, ExportSettings(),
        )
        exported = self.probe.probe(target)
        self.assertIsNone(exported.primary_video)
        self.assertEqual(len(exported.audio_streams), 1)


if __name__ == "__main__":
    unittest.main()
