"""
Base de données time series pour les métriques EnergyMe.
SQLite en mode WAL – stockage append-only, rétention 7 jours.

Pas de dépendance externe : sqlite3 est dans la stdlib Python.
"""

import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS ts_measurements (
    ts   REAL    NOT NULL,   -- Unix timestamp UTC
    ch   INTEGER NOT NULL,   -- channel index
    pw   REAL,               -- active power (W)
    ein  REAL,               -- energy imported (Wh)
    eout REAL                -- energy exported (Wh)
);
CREATE INDEX IF NOT EXISTS idx_ts_ch ON ts_measurements (ts, ch);

CREATE TABLE IF NOT EXISTS channel_resets (
    id  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts  REAL    NOT NULL,   -- Unix timestamp UTC du reset
    ch  INTEGER NOT NULL    -- channel index
);
CREATE INDEX IF NOT EXISTS idx_resets_ch ON channel_resets (ch, ts);
"""

_RETENTION_DAYS = 7
_TREND_THRESHOLD_W_MIN = 5.0  # seuil (W/min) pour distinguer up/down/stable


class TimeSeriesStore:
    """
    Stockage append-only des mesures EnergyMe avec calcul de tendance.
    Thread-safe : un verrou pour les écritures, WAL pour les lectures concurrentes.
    """

    def __init__(self, db_path: str | Path = "energyme_ts.db"):
        self._db_path = str(db_path)
        self._write_lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_DDL)
        log.info("TimeSeriesStore initialisé → %s", self._db_path)

    # ── Connexion ─────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        return c

    # ── Écriture ──────────────────────────────────────────────────────────────

    def record(self, channels: list[dict]) -> None:
        """Enregistre un snapshot pour tous les canaux ayant des métriques."""
        ts = datetime.now(timezone.utc).timestamp()
        rows = [
            (
                ts, ch["index"],
                m.get("activePower"),
                m.get("activeEnergyImported"),
                m.get("activeEnergyExported"),
            )
            for ch in channels
            if (m := ch.get("metrics")) is not None
        ]
        if not rows:
            return
        with self._write_lock, self._conn() as c:
            c.executemany(
                "INSERT INTO ts_measurements (ts, ch, pw, ein, eout) VALUES (?,?,?,?,?)",
                rows,
            )

    def purge_old(self, days: int = _RETENTION_DAYS) -> int:
        """Supprime les mesures antérieures à `days` jours. Retourne le nombre de lignes."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
        with self._write_lock, self._conn() as c:
            cur = c.execute("DELETE FROM ts_measurements WHERE ts < ?", (cutoff,))
            return cur.rowcount

    # ── Lecture ───────────────────────────────────────────────────────────────

    def get_all_trends(
        self, channel_indexes: list[int], minutes: int = 15
    ) -> dict[int, dict]:
        """
        Calcule la tendance (régression linéaire) pour tous les canaux listés.
        Retourne un dict {channel_index: trend_dict}.

        trend_dict contient :
          direction          : 'up' | 'down' | 'stable' | 'unknown'
          slope_per_min      : pente en W/min (float ou None)
          points             : nombre de points utilisés
          e_in_delta         : variation énergie importée sur la fenêtre (Wh, float ou None)
          e_out_delta        : variation énergie exportée sur la fenêtre (Wh, float ou None)
          direction_since_min: minutes depuis le dernier changement de direction (int ou None)
        """
        if not channel_indexes:
            return {}

        now_ts = datetime.now(timezone.utc).timestamp()
        cutoff = now_ts - minutes * 60
        ph = ",".join("?" * len(channel_indexes))

        with self._conn() as c:
            rows = c.execute(
                f"SELECT ch, ts, pw, ein, eout FROM ts_measurements "
                f"WHERE ch IN ({ph}) AND ts >= ? ORDER BY ch, ts",
                (*channel_indexes, cutoff),
            ).fetchall()

            by_ch: dict[int, list] = {i: [] for i in channel_indexes}
            for r in rows:
                by_ch[r["ch"]].append(r)

            result = {}
            for idx, pts in by_ch.items():
                trend = _compute_trend(pts)
                if trend["direction"] not in ("unknown",):
                    trend["direction_since_min"] = _find_direction_since(
                        c, idx, trend["direction"], now_ts, minutes
                    )
                else:
                    trend["direction_since_min"] = None
                result[idx] = trend

        return result

    def get_history(self, channel_index: int, minutes: int = 60) -> list[dict]:
        """Retourne les mesures brutes d'un canal sur les `minutes` dernières minutes."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).timestamp()
        with self._conn() as c:
            rows = c.execute(
                "SELECT ts, pw, ein, eout FROM ts_measurements "
                "WHERE ch = ? AND ts >= ? ORDER BY ts",
                (channel_index, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_reset(self, ch_idx: int) -> None:
        """Enregistre un événement de reset (off/on) pour un canal."""
        ts = datetime.now(timezone.utc).timestamp()
        with self._write_lock, self._conn() as c:
            c.execute("INSERT INTO channel_resets (ts, ch) VALUES (?, ?)", (ts, ch_idx))

    def get_last_resets(self, channel_indexes: list[int]) -> dict[int, float | None]:
        """Retourne le timestamp du dernier reset par canal (None si jamais réinitialisé)."""
        if not channel_indexes:
            return {}
        ph = ",".join("?" * len(channel_indexes))
        with self._conn() as c:
            rows = c.execute(
                f"SELECT ch, MAX(ts) AS last_ts FROM channel_resets "
                f"WHERE ch IN ({ph}) GROUP BY ch",
                channel_indexes,
            ).fetchall()
        result: dict[int, float | None] = {i: None for i in channel_indexes}
        for r in rows:
            result[r["ch"]] = r["last_ts"]
        return result

    def get_load_discard_stats(
        self, channel_index: int, minutes: int = 60
    ) -> dict:
        """
        Calcule un score de qualité signal pour un canal (0 = sain, 100 = problématique).

        Indicateurs combinés :
          frozen_ratio     – % de paires consécutives avec Δpw < 0.5 W        → 50 pts max
          polarity_flips   – changements de signe de pw (hors ±5 W)           → 20 pts max
          instability_cv   – coefficient de variation de pw                    → 20 pts max
          wdrr_delta_trend – pente des intervalles entre lectures avec Δpw>0.5 → 10 pts max
                             (détecte les lectures de plus en plus espacées,
                              quelle qu'en soit la cause : Wi-Fi, hardware, firmware)
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).timestamp()
        with self._conn() as c:
            rows = c.execute(
                "SELECT ts, pw FROM ts_measurements WHERE ch=? AND ts>=? ORDER BY ts",
                (channel_index, cutoff),
            ).fetchall()

        base: dict = {
            "score": 0,
            "frozen_ratio": None,
            "polarity_flips": 0,
            "instability_cv": None,
            "wdrr_delta_trend": None,
            "points": len(rows),
        }
        if len(rows) < 3:
            return base

        pw_vals = [r["pw"] for r in rows if r["pw"] is not None]
        ts_vals = [r["ts"] for r in rows if r["pw"] is not None]
        if len(pw_vals) < 3:
            return base

        # ── frozen_ratio ──────────────────────────────────────────────────────
        frozen_pairs = sum(
            1 for a, b in zip(pw_vals, pw_vals[1:]) if abs(b - a) < 0.5
        )
        total_pairs = len(pw_vals) - 1
        frozen_ratio = frozen_pairs / total_pairs
        base["frozen_ratio"] = round(frozen_ratio, 3)

        # ── polarity_flips ────────────────────────────────────────────────────
        # On filtre les valeurs hors zone neutre ±5 W pour ignorer le bruit
        significant = [(t, p) for t, p in zip(ts_vals, pw_vals) if abs(p) > 5.0]
        flips = 0
        for i in range(1, len(significant)):
            if significant[i][1] * significant[i - 1][1] < 0:
                flips += 1
        base["polarity_flips"] = flips

        # ── instability_cv ────────────────────────────────────────────────────
        n = len(pw_vals)
        mean_pw = sum(pw_vals) / n
        cv: float | None = None
        if abs(mean_pw) > 1.0:
            variance = sum((p - mean_pw) ** 2 for p in pw_vals) / n
            std_pw = variance ** 0.5
            cv = round(std_pw / abs(mean_pw), 3)
        base["instability_cv"] = cv

        # ── wdrr_delta_trend ──────────────────────────────────────────────────
        # Intervalles entre lectures où la valeur a effectivement changé (Δ > 0.5 W)
        useful_ts = [
            ts_vals[i]
            for i in range(1, len(pw_vals))
            if abs(pw_vals[i] - pw_vals[i - 1]) > 0.5
        ]
        wdrr_slope: float | None = None
        if len(useful_ts) >= 4:
            intervals = [b - a for a, b in zip(useful_ts, useful_ts[1:])]
            xi = list(range(len(intervals)))
            xm = sum(xi) / len(xi)
            ym = sum(intervals) / len(intervals)
            denom = sum((x - xm) ** 2 for x in xi)
            if denom:
                wdrr_slope = round(
                    sum((x - xm) * (y - ym) for x, y in zip(xi, intervals)) / denom,
                    2,
                )
        base["wdrr_delta_trend"] = wdrr_slope

        # ── score composite ───────────────────────────────────────────────────
        s = 0.0
        s += frozen_ratio * 50
        s += min(flips, 10) * 2.0                          # 20 pts max sur 10 flips
        if cv is not None:
            s += min(cv, 2.0) * 10                         # 20 pts max sur CV=2
        if wdrr_slope is not None and wdrr_slope > 0:
            s += min(wdrr_slope / 30, 1.0) * 10            # 10 pts max

        base["score"] = min(100, round(s))
        return base

    def get_all_load_discard_stats(
        self, channel_indexes: list[int], minutes: int = 60
    ) -> dict[int, dict]:
        """Calcule le score de load discard pour une liste de canaux."""
        return {idx: self.get_load_discard_stats(idx, minutes) for idx in channel_indexes}

    def clear_all(self) -> int:
        """Supprime toutes les mesures et tous les resets. Retourne le nombre de lignes supprimées."""
        with self._write_lock, self._conn() as c:
            n1 = c.execute("DELETE FROM ts_measurements").rowcount
            n2 = c.execute("DELETE FROM channel_resets").rowcount
        log.info("Historique effacé : %d mesures + %d resets supprimés", n1, n2)
        return n1 + n2

    def stats(self) -> dict:
        """Statistiques rapides sur le contenu de la base."""
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) FROM ts_measurements").fetchone()[0]
            oldest = c.execute("SELECT MIN(ts) FROM ts_measurements").fetchone()[0]
            newest = c.execute("SELECT MAX(ts) FROM ts_measurements").fetchone()[0]
        return {
            "total_rows": total,
            "oldest_ts": oldest,
            "newest_ts": newest,
        }


# ── Calcul de tendance ────────────────────────────────────────────────────────

def _compute_trend(pts: list) -> dict:
    """
    Régression linéaire (moindres carrés) sur (ts, pw).
    Calcule aussi le delta d'énergie importée/exportée sur la fenêtre.
    """
    base: dict = {
        "direction": "unknown",
        "slope_per_min": None,
        "points": 0,
        "e_in_delta": None,
        "e_out_delta": None,
        "direction_since_min": None,
    }
    if not pts:
        return base

    # Points valides pour la régression puissance
    valid = [(r["ts"], r["pw"]) for r in pts if r["pw"] is not None]
    n = len(valid)
    base["points"] = n

    if n >= 2:
        xs = [p[0] for p in valid]
        ys = [p[1] for p in valid]
        x_mean = sum(xs) / n
        y_mean = sum(ys) / n
        denom = sum((x - x_mean) ** 2 for x in xs)

        slope_min: float
        if denom:
            slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denom
            slope_min = round(slope * 60.0, 1)  # W/s → W/min
        else:
            slope_min = 0.0

        base["slope_per_min"] = slope_min
        if slope_min > _TREND_THRESHOLD_W_MIN:
            base["direction"] = "up"
        elif slope_min < -_TREND_THRESHOLD_W_MIN:
            base["direction"] = "down"
        else:
            base["direction"] = "stable"

    # Delta énergie sur la fenêtre (premier et dernier point valides)
    ein_vals  = [r["ein"]  for r in pts if r["ein"]  is not None]
    eout_vals = [r["eout"] for r in pts if r["eout"] is not None]
    if len(ein_vals) >= 2:
        base["e_in_delta"]  = round(ein_vals[-1]  - ein_vals[0],  3)
    if len(eout_vals) >= 2:
        base["e_out_delta"] = round(eout_vals[-1] - eout_vals[0], 3)

    return base


def _find_direction_since(
    conn: sqlite3.Connection,
    ch_idx: int,
    current_dir: str,
    now_ts: float,
    window_min: int = 15,
    max_lookback_hours: int = 4,
) -> int | None:
    """
    Remonte dans les données historiques pour trouver depuis combien de minutes
    la direction `current_dir` est maintenue en continu.

    Stratégie : découpe la période [now - max_lookback, now] en sous-fenêtres
    de `window_min` minutes, de la plus récente vers la plus ancienne. S'arrête
    dès qu'une sous-fenêtre donne une direction différente.
    Retourne le nombre de minutes depuis le début de la séquence identique,
    ou None si on ne peut pas déterminer.
    """
    far_cutoff = now_ts - max_lookback_hours * 3600
    rows = conn.execute(
        "SELECT ts, pw, ein, eout FROM ts_measurements WHERE ch=? AND ts >= ? ORDER BY ts",
        (ch_idx, far_cutoff),
    ).fetchall()

    if not rows:
        return None

    step = window_min * 60
    # Nombre de sous-fenêtres complètes qu'on peut remonter (au-delà de la fenêtre courante)
    consistent_since_ts = now_ts - step  # début de la fenêtre courante (déjà identique)

    # On découpe de now vers le passé
    window_end = now_ts - step  # on part de la fin de la fenêtre précédente
    while window_end > far_cutoff:
        window_start = window_end - step
        pts = [r for r in rows if window_start <= r["ts"] < window_end]
        if not pts:
            break
        sub = _compute_trend(pts)
        if sub["direction"] != current_dir:
            break
        consistent_since_ts = window_start
        window_end = window_start

    elapsed_min = int((now_ts - consistent_since_ts) / 60)
    return elapsed_min if elapsed_min >= window_min else window_min
