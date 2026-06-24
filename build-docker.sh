#!/usr/bin/env bash
# Build, tag et push l'image energyme-monitor vers le registry privé hamasei-domo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="energyme-monitor"
REGISTRY="registry.hamasei-domo.org"
TAG=""
PUSH=false
NO_CACHE=false

# ── Helpers ────────────────────────────────────────────────────────────────────
step() { echo -e "\033[36m  → $*\033[0m"; }
ok()   { echo -e "\033[32m  ✓ $*\033[0m"; }
warn() { echo -e "\033[33m  ⚠ $*\033[0m"; }
fail() { echo -e "\033[31m  ✗ $*\033[0m"; exit 1; }

usage() {
    echo "Usage: $0 [--tag TAG] [--push] [--no-cache] [-h|--help]"
    echo ""
    echo "  --tag TAG     Tag de version explicite (défaut: git describe)"
    echo "  --push        Pousser l'image vers $REGISTRY après le build"
    echo "  --no-cache    Désactiver le cache Docker lors du build"
    echo ""
    echo "Exemples:"
    echo "  $0                       # build local uniquement"
    echo "  $0 --push                # build + push (tag auto depuis git)"
    echo "  $0 --tag 1.3.0 --push   # build + push avec tag explicite"
    exit 0
}

# ── Parsing des arguments ──────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag)      TAG="$2";      shift 2 ;;
        --push)     PUSH=true;     shift   ;;
        --no-cache) NO_CACHE=true; shift   ;;
        -h|--help)  usage ;;
        *) fail "Option inconnue : $1" ;;
    esac
done

echo ""
echo -e "\033[35m  ═══════════════════════════════════════════════\033[0m"
echo -e "\033[35m    energyme-monitor  —  Docker build & push\033[0m"
echo -e "\033[35m  ═══════════════════════════════════════════════\033[0m"
echo ""

cd "$SCRIPT_DIR"

# ── 1. Vérification Docker ────────────────────────────────────────────────────
step "Vérification de Docker..."
if ! docker info &>/dev/null; then
    fail "Docker n'est pas accessible. Vérifiez que le daemon est démarré."
fi
ok "Docker disponible"

# ── 2. Vérification du Dockerfile ────────────────────────────────────────────
[[ -f "Dockerfile" ]] || fail "Dockerfile introuvable dans $SCRIPT_DIR."

# ── 3. Calcul du tag ──────────────────────────────────────────────────────────
if [[ -z "$TAG" ]]; then
    TAG="$(git describe --tags --always --dirty 2>/dev/null || true)"
    [[ -n "$TAG" ]] || TAG="dev-$(date +%Y%m%d-%H%M)"
fi
ok "Tag de version : $TAG"

# ── 4. Noms d'image complets ──────────────────────────────────────────────────
FULL_TAG="${REGISTRY}/${IMAGE_NAME}:${TAG}"
FULL_LATEST="${REGISTRY}/${IMAGE_NAME}:latest"

step "Image cible  : $FULL_TAG"
step "Image latest : $FULL_LATEST"
echo ""

# ── 5. Authentification au registry (si nécessaire) ──────────────────────────
if [[ "$PUSH" == true ]]; then
    step "Vérification de l'authentification vers $REGISTRY..."

    DOCKER_CONFIG="${DOCKER_CONFIG:-$HOME/.docker}"
    CRED_FILE="$DOCKER_CONFIG/config.json"

    ALREADY_AUTH=false
    if [[ -f "$CRED_FILE" ]] && python3 -c "
import json, sys
cfg = json.load(open('$CRED_FILE'))
auths = cfg.get('auths', {})
creds = cfg.get('credHelpers', {})
registry = '$REGISTRY'
sys.exit(0 if (registry in auths or registry in creds) else 1)
" 2>/dev/null; then
        ALREADY_AUTH=true
    fi

    if [[ "$ALREADY_AUTH" == true ]]; then
        ok "Déjà authentifié sur $REGISTRY"
    else
        warn "Pas d'authentification enregistrée pour $REGISTRY."
        step "Connexion au registry..."
        if ! docker login "$REGISTRY"; then
            fail "Échec de l'authentification sur $REGISTRY."
        fi
        ok "Authentification réussie sur $REGISTRY"
    fi
    echo ""
fi

# ── 6. Build ──────────────────────────────────────────────────────────────────
step "Construction de l'image Docker..."
BUILD_ARGS=(
    build
    --tag "$FULL_TAG"
    --tag "$FULL_LATEST"
    --label "org.opencontainers.image.version=$TAG"
    --label "org.opencontainers.image.created=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    --label "org.opencontainers.image.source=energyme-monitor"
)
[[ "$NO_CACHE" == true ]] && BUILD_ARGS+=(--no-cache)
BUILD_ARGS+=(.)

docker "${BUILD_ARGS[@]}"
ok "Image construite avec succès"
echo ""

# ── 7. Push ───────────────────────────────────────────────────────────────────
if [[ "$PUSH" == true ]]; then
    step "Push de $FULL_TAG vers $REGISTRY..."
    docker push "$FULL_TAG"
    ok "$FULL_TAG poussé"

    step "Push de $FULL_LATEST..."
    docker push "$FULL_LATEST"
    ok "$FULL_LATEST poussé"
    echo ""
fi

# ── 8. Résumé ─────────────────────────────────────────────────────────────────
echo -e "\033[35m  ═══════════════════════════════════════════════\033[0m"
echo -e "\033[32m    Build terminé\033[0m"
echo -e "\033[35m  ═══════════════════════════════════════════════\033[0m"
echo ""
echo -e "  Image versionnée : \033[97m$FULL_TAG\033[0m"
echo -e "  Image latest     : \033[97m$FULL_LATEST\033[0m"
echo ""

if [[ "$PUSH" == true ]]; then
    echo -e "\033[33m  ─── Déploiement via dockhand ─────────────────\033[0m"
    echo ""
    echo -e "  Sur le serveur Docker :"
    echo ""
    echo -e "    \033[90m# Tirer la nouvelle image\033[0m"
    echo -e "    \033[97mdocker pull $FULL_LATEST\033[0m"
    echo ""
    echo -e "    \033[90m# Redémarrer le container (dockhand / watchtower)\033[0m"
    echo -e "    \033[97mdocker compose up -d energyme-monitor\033[0m"
    echo ""
    echo -e "    \033[90m# Ou forcer la recréation :\033[0m"
    echo -e "    \033[97mdocker compose pull && docker compose up --force-recreate -d\033[0m"
    echo ""
    echo -e "  Vérification du health check :"
    echo -e "    \033[97mdocker ps --filter name=energyme-monitor\033[0m"
    echo -e "    \033[97mdocker logs -f energyme-monitor\033[0m"
    echo ""
    echo -e "  \033[33m⚠  Pensez à copier config.ini sur le serveur si modifié.\033[0m"
else
    echo -e "\033[33m  ─── Lancement local ──────────────────────────\033[0m"
    echo ""
    echo -e "    \033[97mdocker run --rm -p 6543:6543 \\\033[0m"
    echo -e "    \033[97m  -v \"\$PWD/development.ini:/app/development.ini:ro\" \\\033[0m"
    echo -e "    \033[97m  -v \"\$PWD/config.ini:/app/config.ini:ro\" \\\033[0m"
    echo -e "    \033[97m  $FULL_TAG\033[0m"
    echo ""
    echo -e "  Ou via compose :"
    echo -e "    \033[97mdocker compose up -d\033[0m"
fi
echo ""
