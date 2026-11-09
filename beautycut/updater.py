"""HTTPS update manifest and installer download helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from . import __version__

DEFAULT_UPDATE_MANIFEST_URL = (
    "https://raw.githubusercontent.com/Immersive-Studio-Corporation/Beautycut/main/updates/latest.json"
)
MAX_MANIFEST_BYTES = 128_000
MAX_INSTALLER_BYTES = 512 * 1024 * 1024
VERSION_PATTERN = re.compile(r"^\d+(?:\.\d+){1,3}$")
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class UpdateError(RuntimeError):
    """An update manifest or download could not be trusted or retrieved."""


@dataclass(frozen=True)
class UpdateManifest:
    version: str
    installer_url: str
    sha256: str
    release_notes: str = ""


def _version_key(value: str) -> tuple[int, ...]:
    if not VERSION_PATTERN.fullmatch(value):
        raise UpdateError("Le manifeste contient un numéro de version invalide.")
    parts = tuple(int(part) for part in value.split("."))
    return parts + (0,) * (4 - len(parts))


def _require_https(url: str, field: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise UpdateError(f"L’adresse {field} doit utiliser HTTPS.")


def parse_update_manifest(payload: bytes | str | dict, current_version: str = __version__) -> UpdateManifest | None:
    """Parse a small JSON manifest and return it only when it is newer."""
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8-sig")
        data = json.loads(payload) if isinstance(payload, str) else payload
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("Le manifeste de mise à jour n’est pas un JSON valide.") from exc
    if not isinstance(data, dict):
        raise UpdateError("Le manifeste de mise à jour doit être un objet JSON.")

    version = str(data.get("version", ""))
    if _version_key(version) <= _version_key(current_version):
        return None
    installer_url = str(data.get("installer_url", ""))
    _require_https(installer_url, "de l’installeur")
    digest = str(data.get("sha256", ""))
    if not SHA256_PATTERN.fullmatch(digest):
        raise UpdateError("Le manifeste ne contient pas d’empreinte SHA-256 valide.")
    notes = data.get("release_notes", "")
    if not isinstance(notes, str):
        raise UpdateError("Les notes de version ont un format invalide.")
    return UpdateManifest(version, installer_url, digest.lower(), notes[:4000])


def check_for_update(manifest_url: str, current_version: str = __version__) -> UpdateManifest | None:
    _require_https(manifest_url, "du manifeste")
    request = Request(
        manifest_url,
        headers={"Accept": "application/json", "User-Agent": f"BeautyCut/{current_version}"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            _require_https(response.geturl(), "finale du manifeste")
            payload = response.read(MAX_MANIFEST_BYTES + 1)
    except HTTPError as exc:
        if exc.code == 404:
            raise UpdateError(
                "Manifeste introuvable (404). Vérifiez que le dépôt et le fichier latest.json sont publiquement accessibles."
            ) from exc
        raise UpdateError(f"Le serveur de mises à jour a répondu HTTP {exc.code}.") from exc
    except (OSError, TimeoutError) as exc:
        raise UpdateError(f"Impossible de joindre le serveur de mises à jour : {exc}") from exc
    if len(payload) > MAX_MANIFEST_BYTES:
        raise UpdateError("Le manifeste de mise à jour est trop volumineux.")
    return parse_update_manifest(payload, current_version)


def download_update(
    manifest: UpdateManifest,
    *,
    progress: Callable[[int], None] | None = None,
    cancel: threading.Event | None = None,
    destination_dir: str | Path | None = None,
) -> Path:
    """Download an installer, then verify its SHA-256 before exposing it."""
    _require_https(manifest.installer_url, "de l’installeur")
    root = Path(destination_dir) if destination_dir else Path(
        os.environ.get("LOCALAPPDATA", tempfile.gettempdir())
    ) / "BeautyCut" / "Updates"
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"BeautyCut-Setup-{manifest.version}.exe"
    partial = root / f".{destination.name}.download"
    digest = hashlib.sha256()
    written = 0
    cancelled = cancel or threading.Event()
    request = Request(manifest.installer_url, headers={"User-Agent": f"BeautyCut/{__version__}"})
    try:
        with urlopen(request, timeout=30) as response, partial.open("wb") as output:
            _require_https(response.geturl(), "finale de l’installeur")
            content_length = response.headers.get("Content-Length")
            expected_length = int(content_length) if content_length and content_length.isdigit() else None
            if expected_length is not None and expected_length > MAX_INSTALLER_BYTES:
                raise UpdateError("L’installeur dépasse la taille autorisée.")
            if progress:
                progress(0 if expected_length else -1)
            while True:
                if cancelled.is_set():
                    raise UpdateError("Téléchargement annulé.")
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_INSTALLER_BYTES:
                    raise UpdateError("L’installeur dépasse la taille autorisée.")
                output.write(chunk)
                digest.update(chunk)
                if progress and expected_length:
                    progress(min(99, int(written * 100 / expected_length)))
        if expected_length is not None and written != expected_length:
            raise UpdateError("Le téléchargement de l’installeur est incomplet.")
        if digest.hexdigest().lower() != manifest.sha256:
            raise UpdateError("L’empreinte SHA-256 de l’installeur ne correspond pas au manifeste.")
        os.replace(partial, destination)
        if progress:
            progress(100)
        return destination
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    except (OSError, TimeoutError, ValueError) as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Échec du téléchargement : {exc}") from exc
