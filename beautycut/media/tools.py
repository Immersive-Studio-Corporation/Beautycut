from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .errors import ToolNotFoundError


def _candidate_roots() -> list[Path]:
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
    else:
        app_dir = Path(__file__).resolve().parents[2]
    return [app_dir, app_dir / "bin", app_dir / "ffmpeg", Path(sys.executable).resolve().parent]


def find_tool(name: str) -> str:
    """Trouve ffmpeg/ffprobe dans la config, le dossier portable ou le PATH."""
    env_name = "BEAUTYCUT_FFMPEG" if name == "ffmpeg" else "BEAUTYCUT_FFPROBE"
    configured = os.environ.get(env_name)
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path)
        found = shutil.which(configured)
        if found:
            return found
        raise ToolNotFoundError(f"Le chemin configuré pour {name} est introuvable : {configured}")

    executable = f"{name}.exe" if os.name == "nt" else name
    for root in _candidate_roots():
        candidate = root / executable
        if candidate.is_file():
            return str(candidate)
    on_path = shutil.which(name)
    if on_path:
        return on_path
    raise ToolNotFoundError(
        f"{name}.exe est introuvable. Installe FFmpeg ou place ffmpeg.exe et ffprobe.exe "
        "dans le dossier bin de BeautyCut."
    )


def run_text(args: list[str], *, timeout: float = 20) -> str:
    try:
        result = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolNotFoundError(f"Impossible d'exécuter {Path(args[0]).name} : {exc}") from exc
    if result.returncode:
        detail = result.stderr.strip().splitlines()
        message = detail[-1] if detail else "aucun détail fourni"
        raise ToolNotFoundError(f"{Path(args[0]).name} a échoué : {message}")
    return result.stdout
