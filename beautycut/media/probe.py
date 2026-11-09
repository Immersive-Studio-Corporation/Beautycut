from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from .errors import MediaProbeError
from .tools import find_tool, run_text

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".wmv", ".flv", ".webm", ".mpeg", ".mpg",
    ".m4v", ".3gp", ".ts", ".mts", ".m2ts", ".vob", ".ogv", ".asf", ".mxf",
}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".m4a", ".wma", ".aiff"}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

_CODEC_LABELS = {
    "h264": "H.264 / AVC", "hevc": "H.265 / HEVC", "av1": "AV1", "vp8": "VP8",
    "vp9": "VP9", "mpeg2video": "MPEG-2", "mpeg4": "MPEG-4", "msmpeg4v3": "MPEG-4",
    "wmv1": "WMV 7", "wmv2": "WMV 8", "wmv3": "WMV 9", "mpeg1video": "MPEG-1",
    "prores": "Apple ProRes", "mjpeg": "Motion JPEG", "theora": "Theora",
    "aac": "AAC", "mp3": "MP3", "mp2": "MP2", "ac3": "Dolby Digital", "eac3": "E-AC-3",
    "flac": "FLAC", "vorbis": "Vorbis", "opus": "Opus", "alac": "Apple Lossless",
    "pcm_s16le": "PCM", "pcm_s24le": "PCM", "wmav2": "WMA", "wmav1": "WMA",
}


def parse_rate(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        result = float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None
    return result if result > 0 else None


def codec_label(codec_name: str | None) -> str:
    if not codec_name:
        return "Inconnu"
    return _CODEC_LABELS.get(codec_name.lower(), codec_name.upper())


def _seconds(value: Any) -> float | None:
    try:
        n = float(value)
    except (ValueError, TypeError):
        return None
    return n if n >= 0 else None


@dataclass(frozen=True)
class MediaStream:
    index: int
    kind: str
    codec_name: str
    codec_long_name: str
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    avg_fps: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    language: str | None = None
    title: str | None = None
    is_default: bool = False
    duration: float | None = None

    @property
    def label(self) -> str:
        name = self.title or self.language
        default = " (par défaut)" if self.is_default else ""
        if self.kind == "video":
            detail = f"{self.width} × {self.height}"
            if self.avg_fps:
                detail += f" · {self.avg_fps:.3g} i/s"
        elif self.kind == "audio":
            detail = f"{self.channels or '?'} canaux"
            if self.sample_rate:
                detail += f" · {self.sample_rate // 1000} kHz"
        else:
            detail = self.kind
        suffix = f" · {name}" if name else ""
        return f"{codec_label(self.codec_name)} · {detail}{suffix}{default}"


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    container: str
    duration: float | None
    size_bytes: int
    bitrate: int | None
    streams: tuple[MediaStream, ...]
    probable_vfr: bool
    metadata: dict[str, str]

    @property
    def video_streams(self) -> tuple[MediaStream, ...]:
        return tuple(stream for stream in self.streams if stream.kind == "video")

    @property
    def audio_streams(self) -> tuple[MediaStream, ...]:
        return tuple(stream for stream in self.streams if stream.kind == "audio")

    @property
    def primary_video(self) -> MediaStream | None:
        return next(iter(self.video_streams), None)

    @property
    def is_audio_only(self) -> bool:
        return not self.video_streams and bool(self.audio_streams)


@dataclass(frozen=True)
class CompatibilityReport:
    supported: bool
    messages: tuple[str, ...]


class MediaProbe:
    def __init__(self, ffprobe: str | None = None, ffmpeg: str | None = None):
        self.ffprobe = ffprobe or find_tool("ffprobe")
        self.ffmpeg = ffmpeg or find_tool("ffmpeg")

    def probe(self, path: str | Path) -> MediaInfo:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise MediaProbeError(f"Le fichier n'existe pas : {source}")
        args = [
            self.ffprobe, "-v", "error", "-show_format", "-show_streams", "-show_chapters",
            "-of", "json", str(source),
        ]
        try:
            result = subprocess.run(
                args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise MediaProbeError("L'analyse du fichier a dépassé 30 secondes.") from exc
        except OSError as exc:
            raise MediaProbeError(f"Impossible de lancer FFprobe : {exc}") from exc
        if result.returncode:
            detail = result.stderr.strip().splitlines()
            reason = detail[-1] if detail else "Le fichier est illisible ou endommagé."
            raise MediaProbeError(f"FFprobe n'a pas pu lire ce média : {reason}")
        try:
            raw = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise MediaProbeError("FFprobe a renvoyé des métadonnées invalides.") from exc

        raw_streams = raw.get("streams", [])
        parsed = tuple(self._parse_stream(item) for item in raw_streams if item.get("codec_type") in {"video", "audio", "subtitle"})
        parsed = tuple(stream for stream in parsed if stream is not None)
        if not parsed:
            raise MediaProbeError("Aucune piste vidéo ou audio exploitable n'a été trouvée.")
        fmt = raw.get("format", {})
        duration = _seconds(fmt.get("duration"))
        rates = [s for s in parsed if s.kind == "video" and s.fps and s.avg_fps]
        probable_vfr = any(abs(s.fps - s.avg_fps) / max(s.fps, s.avg_fps) > 0.015 for s in rates)
        metadata = fmt.get("tags") or {}
        return MediaInfo(
            path=source,
            container=fmt.get("format_name", "inconnu"),
            duration=duration,
            size_bytes=source.stat().st_size,
            bitrate=_int_or_none(fmt.get("bit_rate")),
            streams=parsed,
            probable_vfr=probable_vfr,
            metadata={str(k): str(v) for k, v in metadata.items()},
        )

    @staticmethod
    def _parse_stream(item: dict[str, Any]) -> MediaStream | None:
        kind = item.get("codec_type")
        codec_name = item.get("codec_name") or "unknown"
        tags = item.get("tags") or {}
        disposition = item.get("disposition") or {}
        return MediaStream(
            index=int(item.get("index", 0)),
            kind=kind,
            codec_name=codec_name,
            codec_long_name=item.get("codec_long_name") or codec_label(codec_name),
            width=_int_or_none(item.get("width")),
            height=_int_or_none(item.get("height")),
            fps=parse_rate(item.get("r_frame_rate")),
            avg_fps=parse_rate(item.get("avg_frame_rate")),
            sample_rate=_int_or_none(item.get("sample_rate")),
            channels=_int_or_none(item.get("channels")),
            language=tags.get("language"),
            title=tags.get("title"),
            is_default=bool(disposition.get("default", 0)),
            duration=_seconds(item.get("duration")),
        )

    def available_codecs(self) -> "CodecInventory":
        decoder_text = run_text([self.ffmpeg, "-hide_banner", "-decoders"])
        encoder_text = run_text([self.ffmpeg, "-hide_banner", "-encoders"])
        muxer_text = run_text([self.ffmpeg, "-hide_banner", "-muxers"])
        return CodecInventory(
            decoders=_listed_names(decoder_text),
            video_encoders=_listed_names(encoder_text, "V"),
            audio_encoders=_listed_names(encoder_text, "A"),
            muxers=_listed_names(muxer_text, "M", flag_length=1),
        )

    def check_compatibility(self, info: MediaInfo, inventory: "CodecInventory | None" = None) -> CompatibilityReport:
        inventory = inventory or self.available_codecs()
        messages: list[str] = []
        for stream in info.streams:
            if stream.kind == "subtitle":
                continue
            if not _decoder_available(stream.codec_name, inventory.decoders):
                messages.append(
                    f"Décodeur FFmpeg indisponible pour {stream.kind} : {codec_label(stream.codec_name)}."
                )
        if messages:
            return CompatibilityReport(False, tuple(messages))
        if info.probable_vfr:
            messages.append("Fréquence d'images variable probable : FFmpeg la normalisera si une cadence de sortie est choisie.")
        if len(info.audio_streams) > 1:
            messages.append(f"{len(info.audio_streams)} pistes audio détectées ; elles seront conservées à l'export.")
        if info.is_audio_only:
            messages.append("Audio seul détecté ; l'export conservera les pistes audio sans créer d'image.")
        if not messages:
            messages.append("Média lisible par les décodeurs FFmpeg disponibles.")
        return CompatibilityReport(True, tuple(messages))


@dataclass(frozen=True)
class CodecInventory:
    decoders: frozenset[str]
    video_encoders: frozenset[str]
    audio_encoders: frozenset[str]
    muxers: frozenset[str]

    @property
    def nvenc_available(self) -> bool:
        return any(name.endswith("_nvenc") for name in self.video_encoders)


def _int_or_none(value: Any) -> int | None:
    try:
        n = int(value)
    except (ValueError, TypeError):
        return None
    return n if n >= 0 else None


def _listed_names(output: str, media_kind: str | None = None, *, flag_length: int = 6) -> frozenset[str]:
    names: set[str] = set()
    flags_pattern = rf"[.A-Za-z]{{{flag_length}}}"
    for line in output.splitlines():
        match = re.match(rf"^\s*({flags_pattern})\s+(\S+)", line)
        if not match:
            continue
        flags, name = match.groups()
        if media_kind == "V" and "V" not in flags:
            continue
        if media_kind == "A" and "A" not in flags:
            continue
        if media_kind == "M" and "E" not in flags:
            continue
        names.add(name.lower())
    return frozenset(names)


_DECODER_ALIASES = {
    "h264": {"h264", "h264_cuvid"}, "hevc": {"hevc", "hevc_cuvid"},
    "av1": {"av1", "av1_cuvid", "libdav1d"}, "vp8": {"vp8"}, "vp9": {"vp9", "vp9_cuvid"},
    "mpeg2video": {"mpeg2video", "mpeg2_cuvid"}, "mpeg4": {"mpeg4"},
}


def _decoder_available(codec: str, decoders: frozenset[str]) -> bool:
    aliases = _DECODER_ALIASES.get(codec, {codec})
    return any(name in decoders for name in aliases)
