# BeautyCut

BeautyCut est une application Windows de traitement vidéo local. Le projet commence par l'étape 1 de la feuille de route : inspection des médias par FFprobe et export configurable par FFmpeg. Il n'utilise ni compte, ni service distant, ni API, ni filigrane.

## Fonctions disponibles dans cette version

- Importer un fichier par glisser-déposer ou depuis l'explorateur Windows. L'application laisse FFprobe reconnaître le contenu réel du fichier ; elle ne se limite pas à l'extension.
- Lire les informations du conteneur, de la durée, de la résolution, de la cadence et des pistes audio/vidéo.
- Vérifier la présence des décodeurs FFmpeg et afficher les pistes audio multiples.
- Afficher une image repère originale et une image recadrée calculées localement par FFmpeg.
- Exporter en MP4, MOV, MKV ou WebM, avec les encodeurs réellement présents dans la version locale de FFmpeg.
- Choisir un format vertical 9:16 commun à TikTok, Reels, Shorts et Stories, ou un format portrait 4:5, carré 1:1, paysage 16:9 ou une résolution personnalisée.
- Repositionner le cadre directement à la souris dans l’image originale, utiliser neuf points d’ancrage, régler les curseurs, zoomer jusqu’à 200 %, ou conserver toute l’image avec des bandes.
- Appliquer les presets de qualité recommandée, haute qualité, fichier léger ou qualité personnalisée.
- Choisir la vitesse d’encodage rapide, équilibrée ou orientée compression, la qualité, un débit, une cadence et les codecs audio/vidéo.
- Suivre l’export avec une barre animée pendant la préparation, le pourcentage, la durée traitée, le temps écoulé, une estimation restante, la vitesse et la taille du fichier.
- Vérifier les versions publiées à chaque lancement, télécharger l’installeur depuis HTTPS, contrôler son empreinte SHA-256, puis l’installer et relancer BeautyCut automatiquement.
- Utiliser une fenêtre redimensionnable : les aperçus se réorganisent en colonne sur les petites largeurs et les panneaux restent accessibles par défilement.
- Choisir parmi 20 thèmes nature, océan et cosmos, mémorisés entre les lancements, avec un défilement animé dans les panneaux.
- Découper les vidéos en parties numérotées pendant le recadrage et l’export, avec des préréglages TikTok, Instagram Reels et YouTube Shorts en secondes et en minutes, et toute durée personnalisée de 1 seconde à 24 heures.
- Conserver une marge d’une seconde par segment pour rester sous la limite sélectionnée. Les segments terminés restent disponibles si l’export est annulé ; la partie en cours est supprimée.
- Conserver toutes les pistes audio dans l'export et normaliser la cadence seulement si l'utilisateur choisit une cadence de sortie.
- Annuler un export sans laisser de fichier de sortie partiel.
- Ajuster la position d'un recadrage statique. Le suivi automatique du sujet, le lecteur vidéo et la timeline sont les étapes suivantes de la feuille de route ; ils ne sont pas présentés comme disponibles dans cette version.
- Utiliser les encodeurs NVENC quand FFmpeg les expose ; libx264/libx265 restent disponibles en solution logicielle.

## Pré-requis

- Windows 10/11 x64.
- Python 3.10 ou plus récent.
- FFmpeg et FFprobe dans le `PATH`, dans `bin\` à côté du projet, ou définis par `BEAUTYCUT_FFMPEG` et `BEAUTYCUT_FFPROBE`.
- Pour l'accélération NVIDIA, un pilote compatible et une compilation FFmpeg contenant les encodeurs NVENC.

BeautyCut découvre les encodeurs à l'exécution. Les formats et codecs proposés dépendent de la compilation FFmpeg installée ; un codec d'entrée absent des décodeurs est signalé avant export.

Le profil Rapide privilégie le temps d'encodage sur les vidéos longues ; sa compression peut produire un fichier plus volumineux. Les médias sont traités en flux par FFmpeg, sans charger la vidéo entière en mémoire. Les métadonnées déjà lues à l'import sont réutilisées pour éviter une seconde analyse inutile.

## Installation et lancement

```powershell
cd C:\Users\Derek\Desktop\BEAUTYCUT
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m beautycut
```

Si FFmpeg n'est pas dans le `PATH`, définis les chemins pour la session PowerShell :

```powershell
$env:BEAUTYCUT_FFMPEG = 'C:\Outils\ffmpeg\bin\ffmpeg.exe'
$env:BEAUTYCUT_FFPROBE = 'C:\Outils\ffmpeg\bin\ffprobe.exe'
python -m beautycut
```

## Générer l'application Windows

```powershell
.\scripts\build_windows.ps1
```

L'exécutable et ses bibliothèques Qt sont générés dans `dist\BeautyCut\`. Le paquet utilise FFmpeg/FFprobe déjà installés sur l'ordinateur. Pour créer aussi un installateur, installe Inno Setup puis exécute `.\scripts\build_windows.ps1 -BuildInstaller` ; le fichier sera créé dans `dist\installer\`.

Le logo transparent complet est dans `assets\beautycut_logo.png`. Son emblème optimisé pour l'interface, la barre des tâches et l'installateur est dans `assets\beautycut_mark.png` et `assets\beautycut_mark.ico`.

## Publication des mises à jour

À chaque lancement, l'application lit `updates/latest.json` depuis l'adresse HTTPS configurée dans le panneau « Mise à jour automatique ». Si une version plus récente est publiée, elle télécharge l'installeur, vérifie son empreinte SHA-256, l'installe en silence et redémarre BeautyCut. Le dépôt et son manifeste doivent être accessibles publiquement ; aucun jeton GitHub n'est intégré à l'application. Le workflow GitHub Actions de ce dépôt génère l'installeur Windows, publie la release et met à jour le manifeste quand du code de l'application est poussé sur `main`. Pour la prochaine version, incrémente le numéro dans `pyproject.toml`, `beautycut/__init__.py` et `packaging/BeautyCut.iss`.

## Tests

Les tests créent des vidéos courtes avec FFmpeg dans un dossier temporaire, inspectent deux pistes audio, vérifient le recadrage et valident un export réellement relu par FFprobe.

```powershell
python -m unittest discover -s tests -v
```

## Structure

```text
beautycut/
  app.py                 Démarrage de l'application
  gui/                   Fenêtre, widgets et tâches asynchrones Qt
  media/probe.py         Lecture FFprobe et inventaire des codecs
  media/export.py        Profils, filtres et export FFmpeg
  media/tools.py         Découverte de ffmpeg.exe et ffprobe.exe
tests/                   Tests du pipeline média réel
```

## Formats et licences

Le code de BeautyCut est sous licence MIT (voir [LICENSE](LICENSE)). BeautyCut lance FFmpeg/FFprobe en processus séparés et ne les modifie pas ; aucun binaire FFmpeg n'est inclus. Il faut vérifier la licence et les options de compilation de la distribution FFmpeg choisie avant de la redistribuer : certaines compilations activent des composants GPL ou des codecs soumis à des conditions supplémentaires. PySide6 est distribué sous LGPLv3/GPLv3 ou licence commerciale. Les dépendances Python d'exécution du projet sont limitées à PySide6 ; les modules média utilisent la bibliothèque standard.

## Feuille de route

1. Compatibilité des formats d'import et d'export — en cours dans cette version.
2. Lecteur vidéo et timeline.
3. Découpe, déplacement et assemblage des clips.
4. Pistes audio, texte, transitions et fondus.
5. Intégration du recadrage intelligent avec le montage.
6. Validation de l'ensemble et optimisation des performances.

La sortie 9:16 de cette première étape utilise un recadrage statique dont la position est réglable. Le suivi fluide du sujet et la détection locale des visages/éléments importants restent à développer ; l'interface l'indique explicitement.
