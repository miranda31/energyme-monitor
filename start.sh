#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
INI_FILE="$SCRIPT_DIR/development.ini"
REQ_FILE="$SCRIPT_DIR/requirements.txt"

PORT=6543
BIND_HOST="0.0.0.0"
NO_BROWSER=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)       PORT="$2";       shift 2 ;;
        --host)       BIND_HOST="$2";  shift 2 ;;
        --no-browser) NO_BROWSER=true; shift   ;;
        -h|--help)
            echo "Usage: $0 [--port PORT] [--host HOST] [--no-browser]"
            exit 0
            ;;
        *) echo "Option inconnue : $1"; exit 1 ;;
    esac
done

step() { echo -e "\033[36m  ► $*\033[0m"; }
ok()   { echo -e "\033[32m  ✓ $*\033[0m"; }
warn() { echo -e "\033[33m  ⚠ $*\033[0m"; }
fail() { echo -e "\033[31m  ✗ $*\033[0m"; }

echo ""
echo -e "\033[34m  ╔═══════════════════════════════════╗\033[0m"
echo -e "\033[34m  ║     EnergyMe Monitor – Démarrage  ║\033[0m"
echo -e "\033[34m  ╚═══════════════════════════════════╝\033[0m"
echo ""

# ── 1. Vérification Python ─────────────────────────────────────────────────────
step "Vérification de Python..."
if ! command -v python3 &>/dev/null; then
    fail "Python3 n'est pas installé ou absent du PATH."
    echo "    sudo apt install python3 python3-venv python3-pip"
    exit 1
fi
ok "Python trouvé : $(python3 --version)"

# ── 2. Création du venv si absent ─────────────────────────────────────────────
if [[ ! -d "$VENV_DIR" ]]; then
    step "Création de l'environnement virtuel dans .venv ..."
    python3 -m venv "$VENV_DIR"
    ok "Environnement virtuel créé."
else
    ok "Environnement virtuel existant trouvé."
fi

# ── 3. Activation du venv ─────────────────────────────────────────────────────
step "Activation du venv..."
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
ok "Venv activé."

# ── 4. Installation / mise à jour des dépendances ─────────────────────────────
step "Installation des dépendances (requirements.txt)..."
pip install -q -r "$REQ_FILE"
ok "Dépendances OK."

# ── 5. Mise à jour du fichier ini si port/host personnalisés ──────────────────
if [[ "$PORT" != "6543" || "$BIND_HOST" != "0.0.0.0" ]]; then
    warn "Port ou hôte personnalisé détecté – adaptation du fichier ini..."
    sed -i -E "s/port[[:space:]]*=[[:space:]]*[0-9]+/port = $PORT/"        "$INI_FILE"
    sed -i -E "s/host[[:space:]]*=[[:space:]]*[0-9.]+/host = $BIND_HOST/"  "$INI_FILE"
    ok "Ini mis à jour (host=$BIND_HOST port=$PORT)."
fi

# ── 6. Ouverture du navigateur (sans effet sur serveur headless) ───────────────
if [[ "$NO_BROWSER" == false ]]; then
    URL="http://localhost:$PORT"
    (sleep 3 && (xdg-open "$URL" 2>/dev/null || python3 -m webbrowser "$URL" 2>/dev/null || true)) &
    ok "Navigateur prévu sur $URL dans 3 secondes."
fi

# ── 7. Lancement du serveur ───────────────────────────────────────────────────
echo ""
echo -e "\033[90m  ──────────────────────────────────────\033[0m"
echo -e "\033[32m  Serveur démarré → http://localhost:$PORT\033[0m"
echo -e "\033[90m  Appuyez sur Ctrl+C pour arrêter.\033[0m"
echo -e "\033[90m  ──────────────────────────────────────\033[0m"
echo ""

export PYTHONPATH="$SCRIPT_DIR"
exec pserve "$INI_FILE"
