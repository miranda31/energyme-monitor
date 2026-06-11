#Requires -Version 7
<#
.SYNOPSIS
    Build et publie l'image Docker energyme-monitor pour déploiement via dockhand.

.PARAMETER Tag
    Tag de version explicite. Si omis, calculé depuis "git describe --tags --always".

.PARAMETER Registry
    Registry Docker cible (ex: "registry.garage.local:5000" ou "ghcr.io/miranda31").
    Par défaut : chaîne vide → image locale uniquement, pas de push.

.PARAMETER Push
    Si présent, pousse l'image vers le Registry après le build.

.PARAMETER NoBuildCache
    Désactive le cache Docker lors du build (équivalent --no-cache).

.EXAMPLE
    # Build local uniquement
    .\build-docker.ps1

    # Build + push vers un registry privé
    .\build-docker.ps1 -Registry "registry.garage.local:5000" -Push

    # Build avec tag explicite + push
    .\build-docker.ps1 -Tag "1.3.0" -Registry "registry.garage.local:5000" -Push
#>
[CmdletBinding()]
param(
    [string] $Tag      = "",
    [string] $Registry = "",
    [switch] $Push,
    [switch] $NoBuildCache
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Constantes ─────────────────────────────────────────────────────────────────
$IMAGE_NAME = "energyme-monitor"

# ── Couleurs helpers ───────────────────────────────────────────────────────────
function Write-Step([string]$msg)  { Write-Host "  → $msg" -ForegroundColor Cyan }
function Write-OK([string]$msg)    { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Warn([string]$msg)  { Write-Host "  ⚠ $msg" -ForegroundColor Yellow }
function Write-Err([string]$msg)   { Write-Host "  ✗ $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "═══════════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "  energyme-monitor  —  Docker build & push" -ForegroundColor Magenta
Write-Host "═══════════════════════════════════════════════" -ForegroundColor Magenta
Write-Host ""

# ── Vérifications préalables ───────────────────────────────────────────────────
Write-Step "Vérification de Docker..."
try {
    $null = docker info 2>&1
} catch {
    Write-Err "Docker n'est pas accessible. Vérifiez que le daemon est démarré."
    exit 1
}
Write-OK "Docker disponible"

# ── Calcul du tag ──────────────────────────────────────────────────────────────
if (-not $Tag) {
    try {
        $Tag = (git describe --tags --always --dirty 2>&1).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $Tag) {
            $Tag = "dev-$(Get-Date -Format 'yyyyMMdd-HHmm')"
        }
    } catch {
        $Tag = "dev-$(Get-Date -Format 'yyyyMMdd-HHmm')"
    }
}
Write-OK "Tag de version : $Tag"

# ── Construction des noms d'image ──────────────────────────────────────────────
if ($Registry) {
    $Registry = $Registry.TrimEnd("/")
    $fullTag    = "${Registry}/${IMAGE_NAME}:${Tag}"
    $fullLatest = "${Registry}/${IMAGE_NAME}:latest"
} else {
    $fullTag    = "${IMAGE_NAME}:${Tag}"
    $fullLatest = "${IMAGE_NAME}:latest"
}

Write-Step "Image cible  : $fullTag"
Write-Step "Image latest : $fullLatest"
Write-Host ""

# ── Vérifier la présence du Dockerfile ────────────────────────────────────────
if (-not (Test-Path "Dockerfile")) {
    Write-Err "Dockerfile introuvable dans le répertoire courant."
    exit 1
}

# ── Build ──────────────────────────────────────────────────────────────────────
Write-Step "Construction de l'image Docker..."
$buildArgs = @(
    "build",
    "--tag", $fullTag,
    "--tag", $fullLatest,
    "--label", "org.opencontainers.image.version=$Tag",
    "--label", "org.opencontainers.image.created=$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ' -AsUTC)",
    "--label", "org.opencontainers.image.source=energyme-monitor"
)
if ($NoBuildCache) { $buildArgs += "--no-cache" }
$buildArgs += "."

docker @buildArgs
if ($LASTEXITCODE -ne 0) {
    Write-Err "Échec du build Docker (code $LASTEXITCODE)."
    exit $LASTEXITCODE
}
Write-OK "Image construite avec succès"
Write-Host ""

# ── Push ───────────────────────────────────────────────────────────────────────
if ($Push) {
    if (-not $Registry) {
        Write-Warn "-Push spécifié mais aucun -Registry fourni. Push ignoré."
    } else {
        Write-Step "Push de $fullTag vers $Registry..."
        docker push $fullTag
        if ($LASTEXITCODE -ne 0) {
            Write-Err "Échec du push de $fullTag"
            exit $LASTEXITCODE
        }
        Write-OK "$fullTag poussé"

        Write-Step "Push de $fullLatest..."
        docker push $fullLatest
        if ($LASTEXITCODE -ne 0) {
            Write-Err "Échec du push de $fullLatest"
            exit $LASTEXITCODE
        }
        Write-OK "$fullLatest poussé"
    }
}

# ── Résumé et instructions dockhand ───────────────────────────────────────────
Write-Host ""
Write-Host "═══════════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "  Build terminé" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════" -ForegroundColor Magenta
Write-Host ""
Write-Host "  Image locale  : $fullTag" -ForegroundColor White
Write-Host "  Image latest  : $fullLatest" -ForegroundColor White
Write-Host ""

if ($Registry -and $Push) {
    Write-Host "─── Déploiement via dockhand ─────────────────" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Sur le serveur Docker (garage) :" -ForegroundColor Gray
    Write-Host ""
    Write-Host "    # Tirer la nouvelle image" -ForegroundColor DarkGray
    Write-Host "    docker pull $fullLatest" -ForegroundColor White
    Write-Host ""
    Write-Host "    # Redémarrer le container (dockhand / watchtower)" -ForegroundColor DarkGray
    Write-Host "    docker compose up -d energyme-monitor" -ForegroundColor White
    Write-Host ""
    Write-Host "    # Ou forcer la recréation :" -ForegroundColor DarkGray
    Write-Host "    docker compose pull && docker compose up --force-recreate -d" -ForegroundColor White
    Write-Host ""
    Write-Host "  Vérification du health check :" -ForegroundColor Gray
    Write-Host "    docker ps --filter name=energyme-monitor" -ForegroundColor White
    Write-Host "    docker logs -f energyme-monitor" -ForegroundColor White
    Write-Host ""
    Write-Host "  ⚠  Pensez à copier config.ini sur le serveur si modifié." -ForegroundColor Yellow
} else {
    Write-Host "─── Lancement local ──────────────────────────" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "    docker run --rm -p 6543:6543 \" -ForegroundColor White
    Write-Host "      -v `"`$PWD/development.ini:/app/development.ini:ro`" \" -ForegroundColor White
    Write-Host "      -v `"`$PWD/config.ini:/app/config.ini:ro`" \" -ForegroundColor White
    Write-Host "      $fullTag" -ForegroundColor White
    Write-Host ""
    Write-Host "  Ou via compose :" -ForegroundColor Gray
    Write-Host "    docker compose up -d" -ForegroundColor White
}
Write-Host ""
