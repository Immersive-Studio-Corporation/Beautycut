from __future__ import annotations

import os
import math
import re
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from .errors import ExportCancelled, MediaExportError
from .probe import CodecInventory, MediaInfo, MediaProbe
from .tools import find_tool


@dataclass(frozen=True)
class ExportProfile:
    key: str
    label: str
    width: int
    height: int


PROFILES = (
    ExportProfile("vertical", "Vertical · TikTok / Reels / Shorts · 9:16", 1080, 1920),
    ExportProfile("portrait", "Portrait · fil social · 4:5", 1080, 1350),
    ExportProfile("square", "Carré · fil social · 1:1", 1080, 1080),
    ExportProfile("landscape", "Paysage · 16:9", 1920, 1080),
)
CUSTOM_PROFILE = ExportProfile("custom", "Personnalisé", 1080, 1920)
CONTAINERS = {"MP4": "mp4", "MOV": "mov", "MKV": "matroska", "WebM": "webm"}
CONTAINER_SUFFIXES = {"mp4": ".mp4", "mov": ".mov", "matroska": ".mkv", "webm": ".webm"}
VIDEO_CODEC_ENCODERS = {
    "H.264 / AVC": ("h264_nvenc", "libx264", "h264_mf", "h264_qsv", "h264_amf"),
    "H.265 / HEVC": ("hevc_nvenc", "libx265", "hevc_mf", "hevc_qsv", "hevc_amf"),
    "AV1": ("av1_nvenc", "libsvtav1", "libaom-av1", "av1_qsv", "av1_amf"),
    "VP9": ("libvpx-vp9",),
    "VP8": ("libvpx",),
    "MPEG-4": ("mpeg4",),
    "MPEG-2": ("mpeg2video",),
}
AUDIO_CODEC_ENCODERS = {
    "AAC": ("aac",), "MP3": ("libmp3lame", "libshine"), "Opus": ("libopus", "opus"),
    "Vorbis": ("libvorbis", "vorbis"), "FLAC": ("flac",), "PCM": ("pcm_s16le",),
    "ALAC": ("alac",),
}
VIDEO_FORMATS = {
    "mp4": {"H.264 / AVC", "H.265 / HEVC", "AV1", "MPEG-4"},
    "mov": {"H.264 / AVC", "H.265 / HEVC", "AV1"},
    "matroska": set(VIDEO_CODEC_ENCODERS),
    "webm": {"AV1", "VP9", "VP8"},
}
AUDIO_FORMATS = {
    "mp4": {"AAC", "MP3", "ALAC"}, "mov": {"AAC", "MP3", "ALAC", "PCM"},
    "matroska": set(AUDIO_CODEC_ENCODERS), "webm": {"Opus", "Vorbis"},
}


@dataclass(frozen=True)
class ExportSettings:
    profile: str = "vertical"
    width: int = 1080
    height: int = 1920
    container: str = "mp4"
    video_codec: str = "H.264 / AVC"
    audio_codec: str = "AAC"
    quality: int = 22
    bitrate_kbps: int | None = None
    frame_rate: str | None = None
    crop_x: float = 0.0
    crop_y: float = 0.0
    crop_zoom: float = 1.0
    fit_mode: str = "fill"
    flip_horizontal: bool = False
    speed: str = "fast"
    split_enabled: bool = False
    split_limit_seconds: int = 180

    def validate(self) -> None:
        if self.width < 16 or self.height < 16 or self.width > 7680 or self.height > 7680:
            raise MediaExportError("La résolution doit être comprise entre 16 et 7680 pixels par côté.")
        if self.width % 2 or self.height % 2:
            raise MediaExportError("La largeur et la hauteur doivent être des nombres pairs pour l'encodage courant.")
        if self.container not in CONTAINER_SUFFIXES:
            raise MediaExportError(f"Conteneur non pris en charge : {self.container}")
        if self.video_codec not in VIDEO_FORMATS[self.container]:
            raise MediaExportError(f"{self.video_codec} n'est pas compatible avec le conteneur {self.container}.")
        if self.audio_codec not in AUDIO_FORMATS[self.container]:
            raise MediaExportError(f"{self.audio_codec} n'est pas compatible avec le conteneur {self.container}.")
        if not 0 <= self.quality <= 51:
            raise MediaExportError("La qualité vidéo doit être comprise entre 0 et 51.")
        if self.bitrate_kbps is not None and not 64 <= self.bitrate_kbps <= 200_000:
            raise MediaExportError("Le débit vidéo doit être compris entre 64 et 200 000 kbit/s.")
        if not -1 <= self.crop_x <= 1 or not -1 <= self.crop_y <= 1:
            raise MediaExportError("La position du cadrage est hors limites.")
        if not 1.0 <= self.crop_zoom <= 2.0:
            raise MediaExportError("Le zoom doit être compris entre 100 % et 200 %.")
        if self.fit_mode not in {"fill", "contain"}:
            raise MediaExportError("Le mode de cadrage doit être Remplir ou Tout conserver.")
        if self.speed not in {"fast", "balanced", "quality"}:
            raise MediaExportError("Le préréglage de vitesse est inconnu.")
        if self.split_enabled and not 1 <= self.split_limit_seconds <= 86400:
            raise MediaExportError("La durée d’une partie doit être comprise entre 1 seconde et 24 heures.")
        if self.frame_rate:
            try:
                value = float(self.frame_rate)
            except ValueError as exc:
                raise MediaExportError("La fréquence d'images doit être un nombre valide.") from exc
            if not 1 <= value <= 120:
                raise MediaExportError("La fréquence d'images doit être comprise entre 1 et 120 i/s.")

    @property
    def suffix(self) -> str:
        return CONTAINER_SUFFIXES[self.container]


def split_output_paths(destination: str | Path, total_seconds: float, limit_seconds: int) -> list[Path]:
    if total_seconds <= limit_seconds or limit_seconds < 1:
        return []
    target = Path(destination)
    part_seconds = max(1, limit_seconds - 1)
    count = math.ceil(total_seconds / part_seconds)
    digits = max(3, len(str(count)))
    return [
        target.with_name(f"{target.stem}_partie_{index:0{digits}d}{target.suffix}")
        for index in range(1, count + 1)
    ]


def usable_video_codecs(container: str, inventory: CodecInventory) -> list[str]:
    return [
        label for label in VIDEO_CODEC_ENCODERS
        if label in VIDEO_FORMATS[container] and any(enc in inventory.video_encoders for enc in VIDEO_CODEC_ENCODERS[label])
    ]


def usable_audio_codecs(container: str, inventory: CodecInventory) -> list[str]:
    return [
        label for label in AUDIO_CODEC_ENCODERS
        if label in AUDIO_FORMATS[container] and any(enc in inventory.audio_encoders for enc in AUDIO_CODEC_ENCODERS[label])
    ]


def choose_encoder(codec_label: str, available: frozenset[str], *, prefer_hardware: bool = True) -> str:
    choices = VIDEO_CODEC_ENCODERS.get(codec_label, ())
    if not prefer_hardware:
        choices = tuple(encoder for encoder in choices if not encoder.endswith("_nvenc"))
    for encoder in choices:
        if encoder in available:
            return encoder
    raise MediaExportError(f"Aucun encodeur FFmpeg disponible pour {codec_label}.")


def choose_audio_encoder(codec_label: str, available: frozenset[str]) -> str:
    for encoder in AUDIO_CODEC_ENCODERS.get(codec_label, ()):
        if encoder in available:
            return encoder
    raise MediaExportError(f"Aucun encodeur audio FFmpeg disponible pour {codec_label}.")


def crop_filter(settings: ExportSettings) -> str:
    flip = "hflip," if settings.flip_horizontal else ""
    if settings.fit_mode == "contain":
        return (
            f"{flip}scale={settings.width}:{settings.height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={settings.width}:{settings.height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p"
        )
    x = _ffmpeg_float(settings.crop_x)
    y = _ffmpeg_float(settings.crop_y)
    crop_x = f"(iw-ow)/2+(iw-ow)*{x}/2"
    crop_y = f"(ih-oh)/2+(ih-oh)*{y}/2"
    scaled_width = int(round(settings.width * settings.crop_zoom / 2) * 2)
    scaled_height = int(round(settings.height * settings.crop_zoom / 2) * 2)
    return (
        f"{flip}scale={scaled_width}:{scaled_height}:force_original_aspect_ratio=increase:force_divisible_by=2,"
        f"crop={settings.width}:{settings.height}:{crop_x}:{crop_y},setsar=1,format=yuv420p"
    )


@dataclass(frozen=True)
class ExportProgress:
    stage: str
    percent: int | None = None
    elapsed_seconds: float = 0.0
    encoded_seconds: float = 0.0
    total_seconds: float | None = None
    speed: float | None = None
    fps: float | None = None
    frame: int | None = None
    output_size_bytes: int | None = None
    eta_seconds: float | None = None


def _parse_ffmpeg_time(value: str | None) -> float | None:
    if not value or value in {"N/A", "-1"}:
        return None
    try:
        hours, minutes, seconds = value.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (ValueError, TypeError):
        return None


def _parse_ffmpeg_number(value: str | None) -> float | None:
    if not value or value in {"N/A", "-1"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(match.group()) if match else None


def _apply_encoder_speed(args: list[str], encoder: str, speed: str) -> None:
    if encoder.endswith("_nvenc"):
        preset = {"fast": "p1", "balanced": "p4", "quality": "p7"}[speed]
        args += ["-preset", preset, "-tune", "hq"]
    elif encoder in {"libx264", "libx265"}:
        preset = {"fast": "veryfast", "balanced": "medium", "quality": "slow"}[speed]
        args += ["-preset", preset]
    elif encoder.endswith("_qsv"):
        preset = {"fast": "veryfast", "balanced": "medium", "quality": "slow"}[speed]
        args += ["-preset", preset]
    elif encoder.endswith("_amf"):
        args += ["-quality", {"fast": "speed", "balanced": "balanced", "quality": "quality"}[speed]]
    elif encoder == "libsvtav1":
        args += ["-preset", {"fast": "10", "balanced": "6", "quality": "4"}[speed]]
    elif encoder == "libaom-av1":
        args += ["-cpu-used", {"fast": "8", "balanced": "4", "quality": "1"}[speed]]
    elif encoder in {"libvpx", "libvpx-vp9"}:
        deadline, cpu_used = {
            "fast": ("realtime", "8"),
            "balanced": ("good", "2"),
            "quality": ("good", "0"),
        }[speed]
        args += ["-deadline", deadline, "-cpu-used", cpu_used]


def build_export_command(
    source: str | Path,
    destination: str | Path,
    settings: ExportSettings,
    inventory: CodecInventory,
    *,
    ffmpeg: str | None = None,
    has_video: bool = True,
    prefer_hardware: bool = True,
    start_seconds: float = 0.0,
    duration_seconds: float | None = None,
) -> list[str]:
    settings.validate()
    source_path = str(Path(source).resolve())
    destination_path = str(Path(destination).resolve())
    video_encoder = choose_encoder(settings.video_codec, inventory.video_encoders, prefer_hardware=prefer_hardware) if has_video else None
    audio_encoder = choose_audio_encoder(settings.audio_codec, inventory.audio_encoders)
    muxer = settings.container
    muxer_name = "matroska" if muxer == "matroska" else muxer
    if muxer_name not in inventory.muxers:
        raise MediaExportError(f"FFmpeg ne peut pas écrire le conteneur {muxer}.")

    args = [ffmpeg or find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-y"]
    if start_seconds > 0:
        args += ["-ss", f"{start_seconds:.3f}"]
    args += [
        "-i", source_path,
        "-map", "0:v:0?", "-map", "0:a?", "-map_metadata", "0", "-map_chapters", "0",
    ]
    if settings.container == "webm" and settings.video_codec not in VIDEO_FORMATS["webm"]:
        raise MediaExportError("WebM exige un codec vidéo VP8, VP9 ou AV1.")
    if has_video:
        assert video_encoder is not None
        args += ["-c:v", video_encoder, "-vf", crop_filter(settings)]
    if has_video and video_encoder and video_encoder.endswith("_nvenc"):
        _apply_encoder_speed(args, video_encoder, settings.speed)
        if settings.bitrate_kbps:
            args += ["-b:v", f"{settings.bitrate_kbps}k", "-maxrate", f"{int(settings.bitrate_kbps * 1.25)}k"]
        else:
            args += ["-rc", "vbr", "-cq", str(settings.quality), "-b:v", "0"]
    elif has_video and video_encoder:
        _apply_encoder_speed(args, video_encoder, settings.speed)
        if settings.bitrate_kbps:
            args += ["-b:v", f"{settings.bitrate_kbps}k"]
        elif video_encoder in {"libx264", "libx265", "libsvtav1", "libaom-av1", "libvpx", "libvpx-vp9"}:
            args += ["-crf", str(settings.quality)]
            if video_encoder in {"libvpx", "libvpx-vp9", "libaom-av1"}:
                args += ["-b:v", "0"]
        elif video_encoder in {"mpeg4", "mpeg2video"}:
            quantizer = max(2, min(31, round(settings.quality * 31 / 51)))
            args += ["-q:v", str(quantizer)]
        elif video_encoder.endswith("_qsv"):
            args += ["-global_quality", str(settings.quality)]
        elif video_encoder.endswith("_amf"):
            args += ["-rc", "cqp", "-qp_i", str(settings.quality), "-qp_p", str(settings.quality)]
        else:
            # Media Foundation encoders expose bitrate control; estimate a sensible
            # bitrate from the resolution and the common quality slider.
            scale = settings.width * settings.height / (1920 * 1080)
            estimated_mbps = max(0.5, scale * (12 - 0.16 * settings.quality))
            args += ["-b:v", f"{int(estimated_mbps * 1000)}k"]
    args += ["-c:a", audio_encoder]
    if settings.container in {"mp4", "mov"}:
        args += ["-movflags", "+faststart"]
    if settings.frame_rate:
        args += ["-r", settings.frame_rate, "-fps_mode", "cfr"]
    if duration_seconds is not None:
        args += ["-t", f"{duration_seconds:.3f}"]
    args += ["-stats_period", "0.5", "-progress", "pipe:1", "-nostats", "-f", muxer, destination_path]
    return args


def _ffmpeg_float(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


class MediaExporter:
    def __init__(self, ffmpeg: str | None = None, probe: MediaProbe | None = None):
        self.ffmpeg = ffmpeg or find_tool("ffmpeg")
        self.probe = probe or MediaProbe(ffmpeg=self.ffmpeg)

    def export(
        self,
        source: str | Path,
        destination: str | Path,
        settings: ExportSettings,
        *,
        progress: Callable[[ExportProgress], None] | None = None,
        cancel: threading.Event | None = None,
        overwrite: bool = False,
        prefer_hardware: bool = True,
        known_info: MediaInfo | None = None,
        known_inventory: CodecInventory | None = None,
        start_seconds: float = 0.0,
        duration_seconds: float | None = None,
    ) -> Path | list[Path]:
        src = Path(source).expanduser().resolve()
        target = Path(destination).expanduser().resolve()
        if not src.is_file():
            raise MediaExportError("Le fichier source n'existe plus.")
        if src == target:
            raise MediaExportError("Le fichier de sortie ne peut pas remplacer le fichier source.")
        if target.exists() and not overwrite and not settings.split_enabled:
            raise MediaExportError("Le fichier de destination existe déjà.")
        if target.suffix.lower() != settings.suffix:
            raise MediaExportError(f"L'extension de sortie doit être {settings.suffix}.")
        target.parent.mkdir(parents=True, exist_ok=True)
        source_size = src.stat().st_size
        reusing_probe = bool(
            known_info is not None
            and known_info.path.resolve() == src
            and known_info.size_bytes == source_size
        )
        if progress:
            stage = "Vérification des pistes déjà analysées" if reusing_probe and known_inventory else "Lecture des pistes et des codecs"
            progress(ExportProgress(stage=stage))
        if reusing_probe:
            info = known_info
        else:
            info = self.probe.probe(src)
        inventory = known_inventory or self.probe.available_codecs()
        report = self.probe.check_compatibility(info, inventory)
        if not report.supported:
            raise MediaExportError("Ce fichier ne peut pas être décodé : " + " ".join(report.messages))
        if not inventory.muxers:
            raise MediaExportError("Aucun conteneur d'exportation n'a été détecté dans FFmpeg.")

        if settings.split_enabled:
            if not info.duration or info.duration <= 0:
                raise MediaExportError("La durée du média est inconnue : le découpage automatique ne peut pas être calculé.")
            if info.duration > settings.split_limit_seconds:
                return self._export_segments(
                    src, target, settings, info, inventory, progress=progress, cancel=cancel,
                    overwrite=overwrite, prefer_hardware=prefer_hardware,
                )
            if target.exists() and not overwrite:
                raise MediaExportError("Le fichier de destination existe déjà.")

        temp = target.with_name(f".{target.stem}.beautycut-tmp-{uuid.uuid4().hex}{target.suffix}")
        args = build_export_command(
            src, temp, settings, inventory, ffmpeg=self.ffmpeg,
            has_video=bool(info.primary_video), prefer_hardware=prefer_hardware,
            start_seconds=start_seconds, duration_seconds=duration_seconds,
        )
        duration = info.duration
        cancelled = cancel or threading.Event()
        error_file_path: str | None = None
        if progress:
            progress(ExportProgress(stage="Préparation du moteur vidéo", total_seconds=duration))
        try:
            with tempfile.NamedTemporaryFile(prefix="beautycut-ffmpeg-", suffix=".log", delete=False) as error_file:
                error_file_path = error_file.name
                process = subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=error_file,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                progress_values: dict[str, str] = {}
                started_at = time.perf_counter()
                last_snapshot = ExportProgress(stage="Encodage vidéo", total_seconds=duration)
                assert process.stdout is not None
                try:
                    for line in process.stdout:
                        if cancelled.is_set():
                            process.terminate()
                            try:
                                process.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait()
                            raise ExportCancelled("Export annulé.")
                        key, separator, value = line.strip().partition("=")
                        if not separator:
                            continue
                        progress_values[key] = value
                        if key != "progress":
                            continue
                        elapsed = max(0.0, time.perf_counter() - started_at)
                        encoded = _parse_ffmpeg_time(progress_values.get("out_time"))
                        if encoded is None:
                            raw_time = progress_values.get("out_time_us") or progress_values.get("out_time_ms")
                            try:
                                encoded = int(raw_time) / 1_000_000 if raw_time else 0.0
                            except ValueError:
                                encoded = 0.0
                        speed = _parse_ffmpeg_number(progress_values.get("speed"))
                        if not speed and elapsed > 0 and encoded > 0:
                            speed = encoded / elapsed
                        percent = min(99, max(0, int(100 * encoded / duration))) if duration and duration > 0 else None
                        eta = max(0.0, duration - encoded) / speed if duration and speed and speed > 0 else None
                        raw_size = progress_values.get("total_size")
                        try:
                            output_size = int(raw_size) if raw_size and int(raw_size) >= 0 else None
                        except ValueError:
                            output_size = None
                        try:
                            frame = int(progress_values.get("frame", ""))
                        except ValueError:
                            frame = None
                        last_snapshot = ExportProgress(
                            stage="Encodage en cours",
                            percent=percent,
                            elapsed_seconds=elapsed,
                            encoded_seconds=encoded,
                            total_seconds=duration,
                            speed=speed,
                            fps=_parse_ffmpeg_number(progress_values.get("fps")),
                            frame=frame,
                            output_size_bytes=output_size,
                            eta_seconds=eta,
                        )
                        if progress:
                            progress(last_snapshot)
                finally:
                    process.stdout.close()
                return_code = process.wait()
            if cancelled.is_set():
                raise ExportCancelled("Export annulé.")
            if return_code:
                detail = Path(error_file_path).read_text(encoding="utf-8", errors="replace") if error_file_path else ""
                clean = [line.strip() for line in detail.splitlines() if line.strip()]
                reason = clean[-1] if clean else "Erreur FFmpeg non détaillée."
                encoder = choose_encoder(settings.video_codec, inventory.video_encoders) if info.primary_video else ""
                if prefer_hardware and encoder.endswith("_nvenc") and _is_nvenc_runtime_error(detail):
                    # NVENC can be compiled into FFmpeg but unavailable at runtime (driver, device or VRAM).
                    # Retry locally with the matching software encoder when one is present.
                    if progress:
                        progress(ExportProgress(stage="Accélération GPU indisponible · reprise logicielle", total_seconds=duration))
                    return self.export(
                        src, target, settings, progress=progress, cancel=cancelled,
                        overwrite=overwrite, prefer_hardware=False, known_info=info,
                        known_inventory=inventory, start_seconds=start_seconds,
                        duration_seconds=duration_seconds,
                    )
                raise MediaExportError(f"Échec de l'export : {reason}")
            if not temp.exists() or temp.stat().st_size == 0:
                raise MediaExportError("FFmpeg n'a pas produit de fichier exploitable.")
            if progress:
                progress(ExportProgress(
                    stage="Finalisation du fichier",
                    percent=99,
                    elapsed_seconds=max(0.0, time.perf_counter() - started_at),
                    encoded_seconds=last_snapshot.encoded_seconds,
                    total_seconds=duration,
                    speed=last_snapshot.speed,
                    fps=last_snapshot.fps,
                    frame=last_snapshot.frame,
                    output_size_bytes=temp.stat().st_size,
                ))
            if target.exists() and overwrite:
                target.unlink()
            os.replace(temp, target)
            if progress:
                progress(ExportProgress(
                    stage="Export terminé",
                    percent=100,
                    elapsed_seconds=max(0.0, time.perf_counter() - started_at),
                    encoded_seconds=duration or last_snapshot.encoded_seconds,
                    total_seconds=duration,
                    speed=last_snapshot.speed,
                    fps=last_snapshot.fps,
                    frame=last_snapshot.frame,
                    output_size_bytes=target.stat().st_size,
                ))
            return target
        except ExportCancelled:
            raise
        except MediaExportError:
            raise
        except OSError as exc:
            raise MediaExportError(f"Impossible d'écrire le fichier : {exc}") from exc
        finally:
            if error_file_path:
                try:
                    Path(error_file_path).unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _export_segments(
        self,
        source: Path,
        target: Path,
        settings: ExportSettings,
        info: MediaInfo,
        inventory: CodecInventory,
        *,
        progress: Callable[[ExportProgress], None] | None,
        cancel: threading.Event | None,
        overwrite: bool,
        prefer_hardware: bool,
    ) -> list[Path]:
        total_duration = float(info.duration or 0)
        segment_duration = max(1, settings.split_limit_seconds - 1)
        outputs = split_output_paths(target, total_duration, settings.split_limit_seconds)
        count = len(outputs)
        if not overwrite:
            existing = next((path for path in outputs if path.exists()), None)
            if existing:
                raise MediaExportError(f"Le fichier {existing.name} existe déjà.")

        one_part_settings = replace(settings, split_enabled=False)
        completed: list[Path] = []
        for index, output_path in enumerate(outputs):
            if cancel and cancel.is_set():
                raise ExportCancelled("Export annulé.")
            start = index * segment_duration
            part_duration = min(segment_duration, total_duration - start)
            completed_size = sum(path.stat().st_size for path in completed if path.exists())

            def part_progress(snapshot: ExportProgress, *, part_index=index, part_start=start,
                              part_length=part_duration, part_path=output_path,
                              already_written=completed_size) -> None:
                if not progress:
                    return
                local_encoded = max(0.0, min(part_length, snapshot.encoded_seconds))
                all_encoded = min(total_duration, part_start + local_encoded)
                percent = min(99, max(0, int(100 * all_encoded / total_duration)))
                if part_index == count - 1 and snapshot.stage == "Export terminé":
                    percent = 100
                remaining = max(0.0, total_duration - all_encoded)
                eta = remaining / snapshot.speed if snapshot.speed and snapshot.speed > 0 else None
                current_size = snapshot.output_size_bytes or (part_path.stat().st_size if part_path.exists() else 0)
                progress(ExportProgress(
                    stage=f"Partie {part_index + 1}/{count} · {snapshot.stage}",
                    percent=percent,
                    elapsed_seconds=snapshot.elapsed_seconds,
                    encoded_seconds=all_encoded,
                    total_seconds=total_duration,
                    speed=snapshot.speed,
                    fps=snapshot.fps,
                    frame=snapshot.frame,
                    output_size_bytes=already_written + current_size,
                    eta_seconds=eta,
                ))

            try:
                self.export(
                    source,
                    output_path,
                    one_part_settings,
                    progress=part_progress,
                    cancel=cancel,
                    overwrite=overwrite,
                    prefer_hardware=prefer_hardware,
                    known_info=info,
                    known_inventory=inventory,
                    start_seconds=start,
                    duration_seconds=part_duration,
                )
            except ExportCancelled:
                raise
            except Exception as exc:
                if completed:
                    raise MediaExportError(
                        f"La partie {index + 1}/{count} a échoué. Les {len(completed)} partie(s) terminée(s) restent dans {target.parent}. {exc}"
                    ) from exc
                raise
            completed.append(output_path)

        if progress:
            progress(ExportProgress(
                stage=f"Export terminé · {count} parties",
                percent=100,
                encoded_seconds=total_duration,
                total_seconds=total_duration,
                output_size_bytes=sum(path.stat().st_size for path in completed),
            ))
        return completed


def extract_preview(
    source: str | Path,
    info: MediaInfo,
    settings: ExportSettings,
    *,
    vertical: bool,
    ffmpeg: str | None = None,
) -> bytes:
    if not info.primary_video:
        return b""
    tool = ffmpeg or find_tool("ffmpeg")
    at = min(max((info.duration or 0) * 0.15, 0.0), max((info.duration or 0) - 0.05, 0.0))
    vf = crop_filter(settings) if vertical else "scale=640:480:force_original_aspect_ratio=decrease"
    args = [
        tool, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{at:.3f}", "-i", str(Path(source)),
        "-frames:v", "1", "-vf", vf, "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
    ]
    try:
        result = subprocess.run(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaExportError(f"Impossible de créer l'aperçu : {exc}") from exc
    if result.returncode or not result.stdout:
        detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        reason = detail[-1] if detail else "aucune image vidéo disponible"
        raise MediaExportError(f"Aperçu indisponible : {reason}")
    return result.stdout


def _is_nvenc_runtime_error(detail: str) -> bool:
    text = detail.lower()
    markers = (
        "no capable devices found", "cannot load nvcuda", "failed to load nvenc",
        "cannot load libcuda", "initialization error", "failed to open nvenc",
        "no nvenc capable devices", "out of memory", "driver does not support",
    )
    return any(marker in text for marker in markers)
