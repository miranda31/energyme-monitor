#Requires -Version 5.1
<#
.SYNOPSIS
    Lance EnergyMe Monitor dans un environnement virtuel Python.
.DESCRIPTION
    Crée le venv si absent, installe les dépendances, et démarre le serveur Pyramid.
.PARAMETER Port
    Port d'écoute (défaut : 6543)
.PARAMETER Host
    Adresse d'écoute (défaut : 0.0.0.0)
.PARAMETER NoBrowser
    Ne pas ouvrir le navigateur automatiquement.
.EXAMPLE
    .\start.ps1
    .\start.ps1 -Port 8080 -NoBrowser
#>
param(
    [int]   $Port      = 6543,
    [string]$BindHost  = "0.0.0.0",
    [switch]$NoBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$VenvDir  = Join-Path $PSScriptRoot ".venv"
$IniFile  = Join-Path $PSScriptRoot "development.ini"

# ── Couleurs helpers ──────────────────────────────────────────────────────────
function Write-Step  { param($msg) Write-Host "  ► $msg" -ForegroundColor Cyan   }
function Write-OK    { param($msg) Write-Host "  ✓ $msg" -ForegroundColor Green  }
function Write-Warn  { param($msg) Write-Host "  ⚠ $msg" -ForegroundColor Yellow }
function Write-Fail  { param($msg) Write-Host "  ✗ $msg" -ForegroundColor Red    }

Write-Host ""
Write-Host "  ╔═══════════════════════════════════╗" -ForegroundColor Blue
Write-Host "  ║     EnergyMe Monitor – Démarrage  ║" -ForegroundColor Blue
Write-Host "  ╚═══════════════════════════════════╝" -ForegroundColor Blue
Write-Host ""

# ── 1. Vérification Python ────────────────────────────────────────────────────
Write-Step "Vérification de Python..."
try {
    $pyver = & python --version 2>&1
    Write-OK "Python trouvé : $pyver"
} catch {
    Write-Fail "Python n'est pas installé ou absent du PATH."
    Write-Host "    Téléchargez-le sur https://www.python.org/downloads/" -ForegroundColor Gray
    exit 1
}

# ── 2. Création du venv si absent ────────────────────────────────────────────
if (-not (Test-Path $VenvDir)) {
    Write-Step "Création de l'environnement virtuel dans .venv ..."
    python -m venv $VenvDir
    Write-OK "Environnement virtuel créé."
} else {
    Write-OK "Environnement virtuel existant trouvé."
}

# ── 3. Activation du venv ────────────────────────────────────────────────────
$Activate = Join-Path $VenvDir "Scripts\Activate.ps1"
if (-not (Test-Path $Activate)) {
    Write-Fail "Script d'activation introuvable : $Activate"
    exit 1
}
Write-Step "Activation du venv..."
& $Activate
Write-OK "Venv activé."

# ── 4. Installation / mise à jour des dépendances ────────────────────────────
$ReqFile = Join-Path $PSScriptRoot "requirements.txt"
Write-Step "Installation des dépendances (requirements.txt)..."
pip install -q -r $ReqFile
Write-OK "Dépendances OK."

# ── 5. Mise à jour du fichier ini si port/host personnalisés ─────────────────
if ($Port -ne 6543 -or $BindHost -ne "0.0.0.0") {
    Write-Warn "Port ou hôte personnalisé détecté – adaptation du fichier ini..."
    $ini = Get-Content $IniFile -Raw
    $ini = $ini -replace "port\s*=\s*\d+",  "port = $Port"
    $ini = $ini -replace "host\s*=\s*[\d.]+","host = $BindHost"
    $ini | Set-Content $IniFile -Encoding UTF8
    Write-OK "Ini mis à jour (host=$BindHost port=$Port)."
}

# ── 6. Ouverture du navigateur ───────────────────────────────────────────────
if (-not $NoBrowser) {
    $url = "http://localhost:$Port"
    Start-Job -ScriptBlock {
        param($u)
        Start-Sleep -Seconds 3
        Start-Process $u
    } -ArgumentList $url | Out-Null
    Write-OK "Navigateur prévu sur $url dans 3 secondes."
}

# ── 7. Lancement du serveur ──────────────────────────────────────────────────
Write-Host ""
Write-Host "  ──────────────────────────────────────" -ForegroundColor DarkGray
Write-Host "  Serveur démarré → http://localhost:$Port" -ForegroundColor Green
Write-Host "  Appuyez sur Ctrl+C pour arrêter." -ForegroundColor Gray
Write-Host "  ──────────────────────────────────────" -ForegroundColor DarkGray
Write-Host ""

$env:PYTHONPATH = $PSScriptRoot
pserve $IniFile
