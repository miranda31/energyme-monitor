"""
Thread de collecte en arrière-plan pour la base time series EnergyMe.
Sonde le dispositif à intervalle fixe, indépendamment des requêtes web.
"""

import logging
import threading
import time

from .client import EnergyMeClient, EnergyMeError
from .timeseries import TimeSeriesStore

log = logging.getLogger(__name__)

_PURGE_EVERY_TICKS   = 120  # purge toutes les 120 collectes ≈ 1 h à 30 s/tick
_STABLE_RESET_MIN    = 60   # stabilité requise avant reset automatique (min)
_RESET_COOLDOWN_SEC  = 7200 # cooldown entre deux resets du même canal (2 h)
_RESET_PAUSE_SEC     = 20   # pause entre désactivation et réactivation


class BackgroundCollector:
    """
    Sonde l'ESP32 à intervalle fixe et stocke les mesures dans TimeSeriesStore.
    Le thread est un daemon : il s'arrête automatiquement quand le processus se termine.
    """

    def __init__(
        self,
        client: EnergyMeClient,
        store: TimeSeriesStore,
        interval: int = 30,
    ):
        self._client = client
        self._store = store
        self._interval = interval
        self._stop = threading.Event()
        self._last_reset_ts: dict[int, float] = {}  # cooldown en mémoire par canal
        self._auto_reset_enabled = True
        self._config_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="EnergyMe-Collector",
        )

    def start(self) -> None:
        log.info(
            "Collecteur time series démarré (intervalle=%ds, db=%s)",
            self._interval,
            self._store._db_path,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def auto_reset_enabled(self) -> bool:
        with self._config_lock:
            return self._auto_reset_enabled

    @auto_reset_enabled.setter
    def auto_reset_enabled(self, value: bool) -> None:
        with self._config_lock:
            self._auto_reset_enabled = bool(value)
        log.info("Auto-reset %s", "activé" if value else "désactivé")

    def clear_cooldowns(self) -> None:
        """Vide les cooldowns en mémoire (utile après un effacement d'historique)."""
        self._last_reset_ts.clear()

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        tick = 0
        # Premier tick après un court délai pour laisser l'app démarrer
        self._stop.wait(timeout=5)

        while not self._stop.is_set():
            try:
                channels = self._client.get_channels_with_metrics()
                self._store.record(channels)
                tick += 1
                if tick % _PURGE_EVERY_TICKS == 0:
                    removed = self._store.purge_old()
                    if removed:
                        log.debug("Purge time series : %d lignes supprimées", removed)
                self._check_stable_resets(channels)
            except EnergyMeError as exc:
                log.debug("Collecteur – dispositif injoignable : %s", exc)
            except Exception:
                log.exception("Collecteur – erreur inattendue")

            self._stop.wait(timeout=self._interval)

    def _check_stable_resets(self, channels: list[dict]) -> None:
        """Déclenche un off/on pour les canaux stables depuis ≥1 h sans réponse partielle."""
        if not self.auto_reset_enabled:
            return

        now = time.time()

        # Seuls les canaux actifs (flag active=True) ET avec des métriques sont candidats
        active_idx = [
            ch["index"] for ch in channels
            if ch.get("active") and ch.get("metrics") is not None
        ]
        if not active_idx:
            return

        try:
            trends = self._store.get_all_trends(active_idx)
        except Exception:
            log.exception("_check_stable_resets – erreur lecture trends")
            return

        for ch in channels:
            idx = ch["index"]
            if idx not in active_idx or ch.get("partial_metrics"):
                continue
            tr = trends.get(idx)
            if not tr or tr["direction"] != "stable":
                continue
            if (tr.get("direction_since_min") or 0) < _STABLE_RESET_MIN:
                continue
            # Cooldown : pas de reset si un reset récent existe en mémoire
            if now - self._last_reset_ts.get(idx, 0) < _RESET_COOLDOWN_SEC:
                continue

            self._last_reset_ts[idx] = now
            threading.Thread(
                target=self._do_reset,
                args=(idx,),
                daemon=True,
                name=f"EnergyMe-Reset-ch{idx}",
            ).start()

    def _do_reset(self, ch_idx: int) -> None:
        """Désactive puis réactive un canal avec une pause de 20 s."""
        try:
            log.info("Auto-reset canal %d : désactivation", ch_idx)
            self._client.update_channel(ch_idx, {"active": False})
            time.sleep(_RESET_PAUSE_SEC)
            log.info("Auto-reset canal %d : réactivation", ch_idx)
            self._client.update_channel(ch_idx, {"active": True})
            self._store.record_reset(ch_idx)
            log.info("Auto-reset canal %d terminé", ch_idx)
        except Exception:
            log.exception("Erreur auto-reset canal %d", ch_idx)
