from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..media.export import ExportSettings, MediaExporter, extract_preview
from ..media.probe import CompatibilityReport, CodecInventory, MediaInfo, MediaProbe
from ..updater import UpdateManifest, check_for_update, download_update


class AnalyzeMediaThread(QThread):
    loaded = Signal(int, object, object, object)
    failed = Signal(int, str)

    def __init__(self, path: str | Path, generation: int, parent=None):
        super().__init__(parent)
        self.path = str(path)
        self.generation = generation

    def run(self) -> None:
        try:
            probe = MediaProbe()
            info = probe.probe(self.path)
            inventory = probe.available_codecs()
            report = probe.check_compatibility(info, inventory)
            self.loaded.emit(self.generation, info, report, inventory)
        except Exception as exc:
            self.failed.emit(self.generation, str(exc))


class PreviewMediaThread(QThread):
    ready = Signal(int, bytes, bytes)
    failed = Signal(int, str)

    def __init__(self, generation: int, info: MediaInfo, settings: ExportSettings, parent=None):
        super().__init__(parent)
        self.generation = generation
        self.info = info
        self.settings = settings

    def run(self) -> None:
        try:
            original = extract_preview(self.info.path, self.info, self.settings, vertical=False)
            vertical = extract_preview(self.info.path, self.info, self.settings, vertical=True)
            self.ready.emit(self.generation, original, vertical)
        except Exception as exc:
            self.failed.emit(self.generation, str(exc))


class ExportMediaThread(QThread):
    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self, source: str, destination: str, settings: ExportSettings, overwrite: bool, parent=None,
        *, media_info: MediaInfo | None = None, inventory: CodecInventory | None = None,
    ):
        super().__init__(parent)
        self.source = source
        self.destination = destination
        self.settings = settings
        self.overwrite = overwrite
        self.media_info = media_info
        self.inventory = inventory
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            exporter = MediaExporter()
            result = exporter.export(
                self.source,
                self.destination,
                self.settings,
                progress=self.progress.emit,
                cancel=self.cancel_event,
                overwrite=self.overwrite,
                known_info=self.media_info,
                known_inventory=self.inventory,
            )
            if isinstance(result, list):
                self.completed.emit([str(path) for path in result])
            else:
                self.completed.emit(str(result))
        except Exception as exc:
            from ..media.errors import ExportCancelled

            if isinstance(exc, ExportCancelled):
                self.cancelled.emit()
            else:
                self.failed.emit(str(exc))


class CheckForUpdateThread(QThread):
    checked = Signal(object)
    failed = Signal(str)

    def __init__(self, manifest_url: str, parent=None):
        super().__init__(parent)
        self.manifest_url = manifest_url

    def run(self) -> None:
        try:
            self.checked.emit(check_for_update(self.manifest_url))
        except Exception as exc:
            self.failed.emit(str(exc))


class DownloadUpdateThread(QThread):
    progress = Signal(int)
    downloaded = Signal(str)
    failed = Signal(str)

    def __init__(self, manifest: UpdateManifest, parent=None):
        super().__init__(parent)
        self.manifest = manifest

    def run(self) -> None:
        try:
            path = download_update(self.manifest, progress=self.progress.emit)
            self.downloaded.emit(str(path))
        except Exception as exc:
            self.failed.emit(str(exc))
