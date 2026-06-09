"""
Thread de collecte en arrière-plan pour la base time series EnergyMe.
Sonde le dispositif à intervalle fixe, indépendamment des requêtes web.
"""

import logging
import threading

from .client import EnergyMeClient, EnergyMeError
from .timeseries import TimeSeriesStore

log = logging.getLogger(__name__)

_PURGE_EVERY_TICKS = 120  # purge toutes les 120 collectes ≈ 1 h à 30 s/tick


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
            except EnergyMeError as exc:
                log.debug("Collecteur – dispositif injoignable : %s", exc)
            except Exception:
                log.exception("Collecteur – erreur inattendue")

            self._stop.wait(timeout=self._interval)
