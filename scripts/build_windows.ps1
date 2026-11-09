param(
    [switch]$BuildInstaller
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $projectRoot
try {
    python -m pip install -e '.[build]'
    if ($LASTEXITCODE -ne 0) {
        throw "Impossible d'installer les dépendances de génération."
    }

    python -m PyInstaller --noconfirm --clean --windowed --onedir `
        --name BeautyCut `
        --icon (Join-Path $projectRoot 'assets\beautycut_mark.ico') `
        --add-data "$(Join-Path $projectRoot 'assets');assets" `
        --distpath (Join-Path $projectRoot 'dist') `
        --workpath (Join-Path $projectRoot 'build\pyinstaller') `
        --specpath (Join-Path $projectRoot 'build\spec') `
        run_beautycut.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller n'a pas pu générer l'application."
    }

    # The host PATH can expose an unrelated ICU build (for example through a
    # PDF tool). PySide6 uses Windows' ICU forwarder on Windows instead.
    $runtimeRoot = Join-Path $projectRoot 'dist\BeautyCut\_internal'
    foreach ($icuFile in @('icuuc.dll', 'icudt78.dll')) {
        $candidate = Join-Path $runtimeRoot $icuFile
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            Remove-Item -LiteralPath $candidate -Force
        }
    }

    $appExecutable = Join-Path $projectRoot 'dist\BeautyCut\BeautyCut.exe'
    if (-not (Test-Path -LiteralPath $appExecutable -PathType Leaf)) {
        throw "L'exécutable attendu est introuvable : $appExecutable"
    }
    Write-Host "Application générée : $appExecutable"
    Write-Host 'FFmpeg et FFprobe restent à installer séparément et doivent être présents dans PATH.'

    if ($BuildInstaller) {
        $compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        $compilerPath = if ($compiler) { $compiler.Source } else { $null }
        if (-not $compilerPath) {
            $programFilesX86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
            $compilerCandidates = @(
                (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
                (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
            )
            if ($programFilesX86) {
                $compilerCandidates += Join-Path $programFilesX86 'Inno Setup 6\ISCC.exe'
            }
            $compilerPath = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
        }
        if (-not $compilerPath) {
            throw "Inno Setup (ISCC.exe) n'est pas installé ; seule l'application portable a été générée."
        }
        & $compilerPath (Join-Path $projectRoot 'packaging\BeautyCut.iss')
        if ($LASTEXITCODE -ne 0) {
            throw "Inno Setup n'a pas pu générer l'installateur."
        }
        $versionText = python -c "from beautycut import __version__; print(__version__)"
        if ($LASTEXITCODE -ne 0) {
            throw "Impossible de lire la version de BeautyCut pour le manifeste."
        }
        $installerPath = Join-Path $projectRoot "dist\installer\BeautyCut-Setup-$($versionText.Trim()).exe"
        python (Join-Path $projectRoot 'scripts\write_update_manifest.py') $installerPath
        if ($LASTEXITCODE -ne 0) {
            throw "Impossible de générer le manifeste de mise à jour."
        }
        Write-Host "Installateur généré dans $(Join-Path $projectRoot 'dist\installer')."
    }
}
finally {
    Pop-Location
}
