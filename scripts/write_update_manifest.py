from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from beautycut import __version__


REPOSITORY = "https://github.com/Immersive-Studio-Corporation/Beautycut"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Génère le manifeste HTTPS de mise à jour BeautyCut.")
    parser.add_argument("installer", type=Path, help="Installeur Windows compilé")
    parser.add_argument(
        "--url",
        help="URL HTTPS de l'asset de release (par défaut, release GitHub BeautyCut)",
    )
    parser.add_argument("--output", type=Path, default=Path("updates/latest.json"))
    args = parser.parse_args()
    installer = args.installer.resolve()
    if not installer.is_file():
        parser.error(f"Installeur introuvable : {installer}")
    installer_url = args.url or (
        f"{REPOSITORY}/releases/download/v{__version__}/{installer.name}"
    )
    if not installer_url.startswith("https://"):
        parser.error("L'URL de l'installeur doit commencer par https://")
    manifest = {
        "version": __version__,
        "installer_url": installer_url,
        "sha256": sha256_file(installer),
        "release_notes": "Comparaison avant/après déplaçable, poignées de cadrage redimensionnables et raccourcis clavier. Tous les thèmes, réglages et aperçus FFmpeg restent synchronisés.",
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Manifeste créé : {output}")
    print(f"Version {__version__} · SHA-256 {manifest['sha256']}")
    print("Publiez le manifeste à l’adresse configurée et ajoutez l’installeur comme asset de release.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
