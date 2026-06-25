# CLAUDE.md — energyme-monitor

## Description du projet

Application web Python de supervision temps réel du dispositif EnergyMe (ESP32 + ADE7953).
Affiche les métriques de puissance/énergie de jusqu'à 16 canaux, calcule des tendances via
une base time series SQLite, et gère le reset automatique des canaux en dérive.

---

## Stack technique

| Couche | Technologie |
|---|---|
| Framework web | Pyramid 2.0+ avec pyramid-jinja2 |
| Serveur WSGI | Waitress 3.0+ (16 threads, port 6543) |
| Templates | Jinja2 avec base.jinja2 héritage |
| Frontend | Bootstrap 5.3.3 + Bootstrap Icons 1.11.3, JavaScript vanilla |
| Base de données | SQLite 3 en mode WAL (stdlib, pas d'ORM) |
| Client HTTP | requests + HTTP Digest Auth |
| Conteneurisation | Docker (python:3.11-slim), docker-compose |

---

## Architecture

```
energyme-monitor/
├── energyme/
│   ├── __init__.py        # App factory Pyramid, init collecteur + routes
│   ├── client.py          # Client HTTP pour l'API EnergyMe (ESP32)
│   ├── collector.py       # Thread démon de collecte time series (30 s/tick)
│   ├── timeseries.py      # Store SQLite (mesures, tendances, resets, scores)
│   └── views.py           # Handlers Pyramid + endpoints API JSON
│   └── templates/
│       ├── base.jinja2    # Layout global (navbar, thème dark)
│       ├── metrics.jinja2 # Dashboard principal
│       ├── config.jinja2  # Registres de calibration ADE7953
│       └── system.jinja2  # Infos système (firmware, réseau, CPU)
├── development.ini        # Config Pyramid/Waitress (logging, threads)
├── config.ini             # Credentials et host EnergyMe (gitignored)
├── config.example.ini     # Modèle de configuration
├── Dockerfile             # Image Python 3.11-slim
├── docker-compose.yml     # Service unique, port 6543, volume config
├── build-docker.ps1       # Script PowerShell build + tag + push (dockhand)
└── start.ps1              # Lancement local Windows (.venv automatique)
```

---

## Flux de données

```
ESP32 EnergyMe
    ↓  HTTP Digest REST (Wi-Fi)
EnergyMeClient.get_channels_with_metrics()
    ↓  toutes les 30 s (BackgroundCollector)
TimeSeriesStore.record()  →  SQLite ts_measurements
    ↓
métriques et trends affichés dans metrics.jinja2
    ↓  WebSocket-like refresh toutes les N secondes (configurable)
Navigateur
```

---

## Base de données SQLite

Fichier : `energyme_ts.db` (chemin configurable via `energyme.ts_db_path`).
Toujours gitignorée (`.gitignore` contient `*.db`).

### Tables

**`ts_measurements`**
| Colonne | Type | Description |
|---|---|---|
| `ts` | REAL | Unix timestamp UTC |
| `ch` | INTEGER | Index du canal (0-15) |
| `pw` | REAL | Puissance active (W) |
| `ein` | REAL | Énergie importée (Wh) |
| `eout` | REAL | Énergie exportée (Wh) |

**`channel_resets`**
| Colonne | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `ts` | REAL | Unix timestamp UTC du reset |
| `ch` | INTEGER | Index du canal |

Rétention : 7 jours (`purge_old()` toutes les ~1 h = 120 ticks × 30 s).

---

## Auto-reset automatique

**Logique** (`collector.py` / `_check_stable_resets`) :
- Condition : canal avec `active=True` **ET** métriques présentes **ET** tendance `stable` depuis ≥ 60 min
- Action : désactivation du canal → pause 20 s → réactivation
- Cooldown : 2 h entre deux resets du même canal (stocké en mémoire, vidé par `clear_cooldowns()`)
- Guard : ignore les canaux à réponse partielle (`partial_metrics=True`)

**Toggle UI** : interrupteur Bootstrap form-switch dans la barre d'outils de `/`.  
État persisté sur le `BackgroundCollector` (en mémoire, `auto_reset_enabled`).  
Synchronisé au chargement de page via `GET /api/auto-reset`.

---

## Score de qualité signal (colonne LD)

Score composite de qualité des mesures d'un canal (0 = sain, 100 = problématique).
Firmware 2.0.3+ : le bug WDRR load-discard de l'issue #149 (2.0.1) est corrigé.
Le score reste utile pour détecter des problèmes hardware (TC mal serré, Wi-Fi instable,
capteur bruité) indépendamment du firmware.

**Calcul** (`timeseries.py` / `get_load_discard_stats`, fenêtre configurable, défaut 60 min) :

| Indicateur | Calcul | Pondération |
|---|---|---|
| `frozen_ratio` | % de paires consécutives avec Δpw < 0,5 W | 50 pts |
| `polarity_flips` | Changements de signe de `pw` (hors zone ±5 W) | 20 pts (plafonné à 10 flips) |
| `instability_cv` | Coefficient de variation de `pw` (σ/μ, si μ > 1 W) | 20 pts (plafonné à CV=2) |
| `wdrr_delta_trend` | Pente des intervalles entre lectures utiles (Δpw > 0,5 W) | 10 pts |

`load_discard_score` : entier 0-100. Si score ≥ 25, l'impact estimé en watts
(énergie moyenne sur la fenêtre × score/100) est affiché sous le badge.

**API** : `GET /api/load-discard?minutes=60` → `{channel_index: {score, frozen_ratio, ...}, ...}`

**UI** : badge coloré dans la colonne "LD" du tableau des métriques.
- Score < 25 : `success` (vert)
- 25–49 : `warning` (orange)
- ≥ 50 : `danger` (rouge)

---

## Routes / API

| Méthode | URL | Description |
|---|---|---|
| GET | `/` | Dashboard métriques |
| POST | `/channel/{ch}/update` | Modifier la config d'un canal |
| GET | `/config` | Registres ADE7953 |
| GET | `/system` | Infos système ESP32 |
| GET | `/api/trends?minutes=N` | Tendances time series |
| POST | `/api/history/clear` | Effacer tout l'historique |
| GET | `/api/history/{ch}?minutes=N` | Historique brut d'un canal |
| GET/POST | `/api/auto-reset` | Lire/modifier le toggle auto-reset |
| GET | `/api/load-discard?minutes=N` | Scores de load discard par canal |

---

## Configuration (`config.ini`)

```ini
[energyme]
host             = energyme.local    # mDNS ou IP du dispositif
username         = admin
password         = <secret>          # HTTP Digest
timeout          = 5                 # secondes
poll_delay_ms    = 200               # délai entre appels API consécutifs
collector_interval = 30              # intervalle collecte (secondes)
ts_db_path       = energyme_ts.db    # chemin SQLite
ts_retention_days = 7

[server]
host = 0.0.0.0
port = 6543
```

---

## Déploiement Docker

### Build et push local → serveur via dockhand

```powershell
.\build-docker.ps1 [-Tag "1.2.3"] [-Registry "registry.garage.local:5000"] [-Push]
```

Le script `build-docker.ps1` :
1. Calcule un tag basé sur `git describe --tags --always`
2. Build l'image `energyme-monitor:<tag>`
3. Tague aussi en `:latest`
4. Si `-Push` : push vers le registry configuré
5. Affiche les instructions de déploiement dockhand

### docker-compose.yml

- Volume `development.ini` monté en lecture seule (contient la config Waitress)
- `config.ini` **doit être copié manuellement** sur le serveur (credentials)
- Health check HTTP sur `/` toutes les 30 s

---

## Développement local (Windows)

```powershell
.\start.ps1              # crée .venv, installe dépendances, lance pserve
# ou manuellement :
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
pserve development.ini
```

---

## Conventions de code

- Pas de commentaires sauf pour les WHY non-obvieux
- Logs en français (niveau INFO pour les événements importants, DEBUG pour le bruit)
- Thread-safety : `threading.Lock` pour les écritures SQLite, WAL pour les lectures concurrentes
- Les `EnergyMeError` sont catchées dans les views et retournées en JSON `{error: ...}`
- Pas de mock DB dans les tests — la DB SQLite en mémoire (`:memory:`) suffit
