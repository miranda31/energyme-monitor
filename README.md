# energyme-monitor

Application web de supervision temps réel du dispositif **EnergyMe** (ESP32 + ADE7953).  
Affiche les métriques de puissance et d'énergie de jusqu'à 16 canaux, calcule des tendances
via une base time series SQLite, détecte les canaux en instabilité WDRR et gère le reset
automatique des capteurs en dérive.

---

## Fonctionnalités

- **Dashboard métriques** : tension, courant, puissance active/réactive/apparente, facteur de puissance, énergie importée/exportée — mis à jour en continu (intervalle configurable)
- **Tendances temps réel** : régression linéaire sur fenêtre glissante (15 min), indicateurs ↑ / ↓ / stable avec pente en W/min et delta énergie
- **Score Load Discard (WDRR)** : détecte les canaux en instabilité liés au bug firmware energyme #149 — valeurs figées, flip de polarité, bruit ADC, dégradation de l'intervalle de lecture
- **Auto-reset** : désactive/réactive automatiquement les canaux stables depuis ≥ 60 min (toggle on/off dans l'interface)
- **Historique** : 7 jours de données SQLite, bouton d'effacement depuis l'interface
- **Configuration ADE7953** : visualisation des registres de calibration
- **Infos système** : firmware, température ESP32, mémoire, réseau, uptime

---

## Prérequis

- Python 3.11+
- Dispositif EnergyMe accessible sur le réseau (mDNS `energyme.local` ou adresse IP)
- Docker (optionnel, pour le déploiement conteneurisé)

---

## Installation et démarrage

### Windows — démarrage rapide

```powershell
.\start.ps1
```

Le script crée automatiquement le `.venv`, installe les dépendances et lance le serveur.

### Manuel

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .

# Copier et adapter la configuration
Copy-Item config.example.ini config.ini
# Éditer config.ini : host, username, password

pserve development.ini
```

L'application est accessible sur **http://localhost:6543**.

---

## Configuration

Copiez `config.example.ini` en `config.ini` (gitignorée) et adaptez :

```ini
[energyme]
host             = energyme.local    # mDNS ou adresse IP du dispositif
username         = admin
password         = energyme          # mot de passe HTTP Digest
timeout          = 5                 # secondes (recommandé : 3-5)
poll_delay_ms    = 200               # délai entre appels API (évite de flood le Wi-Fi)
collector_interval = 30              # collecte time series toutes les N secondes
ts_db_path       = energyme_ts.db    # chemin SQLite (à monter en volume Docker)
ts_retention_days = 7                # rétention des données

[server]
host = 0.0.0.0
port = 6543
```

> `config.ini` contient les identifiants — ne le committez jamais.

---

## Déploiement Docker

### Build de l'image

```powershell
# Build local uniquement
.\build-docker.ps1

# Build + push vers un registry privé (ex: serveur dédié)
.\build-docker.ps1 -Registry "registry.garage.local:5000" -Push

# Avec tag explicite
.\build-docker.ps1 -Tag "1.3.0" -Registry "registry.garage.local:5000" -Push
```

Le tag est calculé automatiquement depuis `git describe --tags --always` si `-Tag` est omis.

### Lancement via docker compose

```bash
# Sur le serveur cible
docker compose up -d
```

**Important** : `config.ini` doit être présent sur le serveur — il n'est pas inclus dans l'image.  
La base SQLite (`energyme_ts.db`) doit être montée en volume pour persister les données :

```yaml
# Ajout recommandé dans docker-compose.yml
volumes:
  - ./energyme_ts.db:/app/energyme_ts.db
  - ./development.ini:/app/development.ini:ro
```

---

## Interface

### Barre d'outils

| Élément | Description |
|---|---|
| **Paramètres** | Affiche/masque les colonnes d'édition des canaux (TC, rôle, groupe…) |
| **Auto-reset** | Active/désactive le reset automatique des canaux stables (toggle synchronisé avec le backend) |
| **Historique** | Efface toutes les données time series (demande confirmation) |
| **Sélecteur de refresh** | Intervalle de rafraîchissement automatique (Manuel / 5 s / 10 s / 30 s / 1 min / 5 min) |

### Tableau des métriques — colonnes

| Colonne | Description |
|---|---|
| **#** | Index du canal (0–15) |
| **Canal** | Nom, groupe, icône rôle. ⚠ si réponse partielle de l'API |
| **Rôle** | Badge coloré (grid / pv / inverter / battery / load) |
| **V / I** | Tension rms (V) et courant rms (A) |
| **P active** | Puissance active (W) avec indicateur de tendance ↑↓ et pente W/min |
| **P réactive / P app.** | VAR et VA |
| **PF** | Facteur de puissance |
| **↓ Import / ↑ Export** | Énergie cumulée (Wh) avec delta Wh sur la fenêtre de tendance |
| **Off/On** | Horodatage du dernier reset automatique |
| **LD** | **Score load discard WDRR** (0-100) — vert < 25, orange 25-49, rouge ≥ 50 |

### Score Load Discard (colonne LD)

Le survol du badge affiche le détail :

| Indicateur | Interprétation |
|---|---|
| **Figé %** | % de lectures consécutives sans changement → le WDRR ne traite plus le canal |
| **Flips polarité** | Inversions de signe de la puissance → auto polarity flip ADE7953 |
| **CV instabilité** | Coefficient de variation de la puissance → bruit ADC / mesures erratiques |
| **Δ lecture (s/tick)** | Pente des intervalles entre lectures utiles → dégradation progressive du service WDRR |

---

## API JSON

| Méthode | Endpoint | Description |
|---|---|---|
| GET | `/api/trends?minutes=N` | Tendances de tous les canaux actifs |
| GET | `/api/history/{ch}?minutes=N` | Historique brut d'un canal |
| GET/POST | `/api/auto-reset` | Lire / modifier le toggle auto-reset |
| POST | `/api/history/clear` | Effacer tout l'historique |
| GET | `/api/load-discard?minutes=N` | Scores load discard WDRR par canal |

---

## Stack technique

| Couche | Technologie |
|---|---|
| Framework web | [Pyramid](https://trypyramid.com/) 2.0+ |
| Serveur WSGI | [Waitress](https://docs.pylonsproject.org/projects/waitress/) 3.0+ — 16 threads |
| Templates | Jinja2 + Bootstrap 5.3.3 |
| Base de données | SQLite 3 (mode WAL, stdlib Python) |
| Client HTTP | requests + HTTP Digest Auth |
| Firmware cible | EnergyMe ESP32 + ADE7953 |
