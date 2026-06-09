"""
Client HTTP pour l'API EnergyMe (ESP32 + ADE7953).
Authentification HTTP Digest. Toutes les méthodes lèvent EnergyMeError
si le dispositif est injoignable ou retourne une erreur.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import requests
from requests.auth import HTTPDigestAuth
from requests.exceptions import ConnectionError, Timeout, RequestException

log = logging.getLogger(__name__)

# Libellés des rôles de canal
ROLE_LABELS = {
    "grid":     "Réseau (Grid)",
    "pv":       "Solaire (PV)",
    "inverter": "Onduleur / Batterie",
    "battery":  "Batterie",
    "load":     "Charge",
}

ROLE_COLORS = {
    "grid":     "primary",
    "pv":       "warning",
    "inverter": "success",
    "battery":  "info",
    "load":     "secondary",
}

ROLE_ICONS = {
    "grid":     "bi-lightning-charge",
    "pv":       "bi-sun",
    "inverter": "bi-arrow-repeat",
    "battery":  "bi-battery-charging",
    "load":     "bi-plug",
}

# Descriptions des champs de calibration ADE7953
ADE7953_CONFIG_DESC = {
    "aVGain":    ("Gain tension canal A",              "LSB"),
    "aIGain":    ("Gain courant canal A",              "LSB"),
    "bIGain":    ("Gain courant canal B",              "LSB"),
    "aIRmsOs":   ("Offset RMS courant A",              "LSB"),
    "bIRmsOs":   ("Offset RMS courant B",              "LSB"),
    "aWGain":    ("Gain puissance active A",           "LSB"),
    "bWGain":    ("Gain puissance active B",           "LSB"),
    "aWattOs":   ("Offset puissance active A",         "LSB"),
    "bWattOs":   ("Offset puissance active B",         "LSB"),
    "aVarGain":  ("Gain puissance réactive A",         "LSB"),
    "bVarGain":  ("Gain puissance réactive B",         "LSB"),
    "aVarOs":    ("Offset puissance réactive A",       "LSB"),
    "bVarOs":    ("Offset puissance réactive B",       "LSB"),
    "aVaGain":   ("Gain puissance apparente A",        "LSB"),
    "bVaGain":   ("Gain puissance apparente B",        "LSB"),
    "aVaOs":     ("Offset puissance apparente A",      "LSB"),
    "bVaOs":     ("Offset puissance apparente B",      "LSB"),
    "phCalA":    ("Calibration de phase canal A",      "LSB"),
    "phCalB":    ("Calibration de phase canal B",      "LSB"),
}

# Champs éditables d'un canal (envoyés via PATCH)
CHANNEL_EDIT_FIELDS = [
    {"name": "label",        "label": "Nom",           "type": "text",   "max_length": 64},
    {"name": "active",       "label": "Actif",         "type": "bool"},
    {"name": "reverse",      "label": "Inversion I",   "type": "bool"},
    {"name": "role",         "label": "Rôle",          "type": "select",
     "options": {k: v for k, v in ROLE_LABELS.items()}},
    {"name": "phase",        "label": "Phase",         "type": "select",
     "options": {1: "Phase 1", 2: "Phase 2", 3: "Phase 3", 4: "240V Split"}},
    {"name": "groupLabel",   "label": "Groupe",        "type": "text",   "max_length": 64},
    {"name": "ct_rating",    "label": "TC – Calibre (A)", "type": "float",
     "min": 1, "max": 300, "path": "ctSpecification.currentRating"},
    {"name": "ct_voltage",   "label": "TC – Sortie (V)",  "type": "float",
     "min": 0.1, "max": 1.0, "path": "ctSpecification.voltageOutput"},
    {"name": "ct_scaling",   "label": "TC – Correction",  "type": "float",
     "min": -0.5, "max": 0.5, "path": "ctSpecification.scalingFraction"},
]


class EnergyMeError(Exception):
    """Erreur de communication avec le dispositif EnergyMe."""


class EnergyMeClient:
    """Client HTTP vers l'API REST EnergyMe (ESP32)."""

    def __init__(self, host: str, username: str, password: str, timeout: int = 10):
        self.base_url = f"http://{host}"
        self._auth = HTTPDigestAuth(username, password)
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    def _get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        try:
            r = self._session.get(url, auth=self._auth, params=params, timeout=self._timeout)
            r.raise_for_status()
            return r.json()
        except Timeout:
            raise EnergyMeError(f"Délai dépassé pour {url}")
        except ConnectionError:
            raise EnergyMeError(f"Impossible de joindre le dispositif ({self.base_url})")
        except RequestException as exc:
            raise EnergyMeError(f"Erreur HTTP {exc}")

    def _patch(self, path: str, body: dict) -> Any:
        url = f"{self.base_url}{path}"
        try:
            r = self._session.patch(
                url, auth=self._auth, json=body, timeout=self._timeout,
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            return r.json()
        except Timeout:
            raise EnergyMeError(f"Délai dépassé pour {url}")
        except ConnectionError:
            raise EnergyMeError(f"Impossible de joindre le dispositif ({self.base_url})")
        except RequestException as exc:
            raise EnergyMeError(f"Erreur HTTP {exc}")

    # ── Canaux ────────────────────────────────────────────────────────────────

    def get_channels(self) -> list[dict]:
        """Retourne la configuration de tous les canaux."""
        data = self._get("/api/v1/ade7953/channel")
        return data.get("channels", data) if isinstance(data, dict) else data

    def get_meter_values(self) -> list[dict]:
        """Retourne les mesures temps réel de tous les canaux actifs."""
        data = self._get("/api/v1/ade7953/meter-values")
        return data if isinstance(data, list) else [data]

    def get_channels_with_metrics(self) -> list[dict]:
        """
        Fusionne config + mesures. Retourne une liste de dicts avec :
        - tous les champs de configuration du canal
        - clé 'metrics' avec les valeurs de mesure (ou None si inactif)
        """
        channels = {ch["index"]: ch for ch in self.get_channels()}
        try:
            meter_values = {mv["index"]: mv.get("data", mv) for mv in self.get_meter_values()}
        except EnergyMeError:
            meter_values = {}

        result = []
        for idx in sorted(channels.keys()):
            ch = dict(channels[idx])
            ch["metrics"] = meter_values.get(idx)
            ch["role_label"] = ROLE_LABELS.get(ch.get("role", "load"), ch.get("role", "—"))
            ch["role_color"] = ROLE_COLORS.get(ch.get("role", "load"), "secondary")
            ch["role_icon"]  = ROLE_ICONS.get(ch.get("role", "load"), "bi-plug")
            result.append(ch)
        return result

    def update_channel(self, index: int, fields: dict) -> dict:
        """
        Met à jour partiellement un canal via PATCH.
        `fields` peut contenir : label, active, reverse, role, phase,
        groupLabel, ct_rating, ct_voltage, ct_scaling.
        """
        body: dict = {"index": index}
        ct: dict = {}

        for key, value in fields.items():
            if key == "ct_rating":
                ct["currentRating"] = value
            elif key == "ct_voltage":
                ct["voltageOutput"] = value
            elif key == "ct_scaling":
                ct["scalingFraction"] = value
            else:
                body[key] = value

        if ct:
            body["ctSpecification"] = ct

        return self._patch("/api/v1/ade7953/channel", body)

    # ── Calibration ADE7953 ───────────────────────────────────────────────────

    def get_ade7953_config(self) -> dict:
        """Retourne la configuration de calibration de l'ADE7953."""
        return self._get("/api/v1/ade7953/config")

    # ── Système ───────────────────────────────────────────────────────────────

    def get_system_info(self) -> dict:
        """Retourne les informations système (firmware, réseau, CPU, mémoire…)."""
        return self._get("/api/v1/system/info")

    def get_firmware_update_info(self) -> dict:
        """Retourne les informations de mise à jour firmware."""
        return self._get("/api/v1/firmware/update-info")

    def get_grid_frequency(self) -> float | None:
        """Retourne la fréquence réseau en Hz."""
        try:
            data = self._get("/api/v1/ade7953/grid-frequency")
            return data.get("gridFrequency")
        except EnergyMeError:
            return None
