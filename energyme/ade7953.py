"""
ADE7953 energy metering IC interface – 16-channel support.
Falls back to simulated data when hardware is not available.
"""

import random
import time
import math

try:
    import smbus2  # type: ignore
    HAS_HW = True
except ImportError:
    HAS_HW = False

NUM_CHANNELS = 16

CHANNEL_MODES = {0: "Normal", 1: "Inversé", 2: "Tamper", 3: "Désactivé"}

PGA_GAINS = {0: "×1", 1: "×2", 2: "×4", 3: "×8", 4: "×16", 5: "×22", 6: "×32"}

# Paramètres éditables par canal
CHANNEL_PARAMS = [
    {"name": "active",     "label": "Actif",        "type": "bool"},
    {"name": "mode",       "label": "Mode",          "type": "select", "options": CHANNEL_MODES},
    {"name": "reverse",    "label": "Inversion",     "type": "bool"},
    {"name": "gain",       "label": "Gain PGA",      "type": "select", "options": {k: v for k, v in enumerate(PGA_GAINS.values())}},
    {"name": "phase_cal",  "label": "Cal. Phase",    "type": "int", "min": -32, "max": 31},
    {"name": "igain",      "label": "Gain Courant",  "type": "int", "min": -2097152, "max": 2097151},
    {"name": "irmsos",     "label": "Offset RMS I",  "type": "int", "min": -512, "max": 511},
    {"name": "no_load",    "label": "No-Load",       "type": "bool"},
]

CONFIG_PARAMS = [
    {"name": "PGA_V",      "description": "Gain de l'amplificateur du canal tension. Valeurs: 0=×1, 1=×2, 2=×4.", "unit": "", "type": "select", "options": {0: "×1", 1: "×2", 2: "×4"}},
    {"name": "PGA_IA",     "description": "Gain de l'amplificateur du canal courant A. Valeurs: 0=×1 à 6=×32.", "unit": "", "type": "select", "options": {k: v for k, v in enumerate(PGA_GAINS.values())}},
    {"name": "PGA_IB",     "description": "Gain de l'amplificateur du canal courant B. Valeurs: 0=×1 à 6=×32.", "unit": "", "type": "select", "options": {k: v for k, v in enumerate(PGA_GAINS.values())}},
    {"name": "VGAIN",      "description": "Registre de calibration du gain tension. Permet d'ajuster la mesure de tension RMS.", "unit": "LSB", "type": "int"},
    {"name": "AIGAIN",     "description": "Registre de calibration du gain courant canal A. Corrige les erreurs de gain du capteur.", "unit": "LSB", "type": "int"},
    {"name": "BIGAIN",     "description": "Registre de calibration du gain courant canal B. Corrige les erreurs de gain du capteur.", "unit": "LSB", "type": "int"},
    {"name": "APHCAL",     "description": "Correction de phase canal A. Compense le déphasage entre tension et courant introduit par le capteur.", "unit": "LSB", "type": "int"},
    {"name": "BPHCAL",     "description": "Correction de phase canal B. Compense le déphasage entre tension et courant introduit par le capteur.", "unit": "LSB", "type": "int"},
    {"name": "VRMSOS",     "description": "Offset de correction RMS tension. Élimine les erreurs d'offset dans la mesure de tension.", "unit": "LSB", "type": "int"},
    {"name": "IRMSOS_A",   "description": "Offset de correction RMS courant canal A.", "unit": "LSB", "type": "int"},
    {"name": "IRMSOS_B",   "description": "Offset de correction RMS courant canal B.", "unit": "LSB", "type": "int"},
    {"name": "WTHR",       "description": "Seuil d'énergie active (Watt-heure). Nombre de LSB d'énergie avant incrémentation du compteur.", "unit": "LSB", "type": "int"},
    {"name": "VARTHR",     "description": "Seuil d'énergie réactive (VAR-heure).", "unit": "LSB", "type": "int"},
    {"name": "VATHR",      "description": "Seuil d'énergie apparente (VA-heure).", "unit": "LSB", "type": "int"},
    {"name": "SAGLVL",     "description": "Niveau de détection de creux de tension (sag). En dessous de ce seuil, une alarme sag est déclenchée.", "unit": "LSB", "type": "int"},
    {"name": "SAGCYC",     "description": "Nombre de cycles secteur consécutifs en dessous de SAGLVL avant déclenchement de l'alarme sag.", "unit": "cycles", "type": "int"},
    {"name": "OILVL",      "description": "Seuil de détection de sur-courant (overcurrent). Déclenche une interruption si dépassé.", "unit": "LSB", "type": "int"},
    {"name": "LCYCMODE",   "description": "Mode d'accumulation d'énergie sur un nombre entier de cycles secteur (line-cycle).", "unit": "", "type": "int"},
    {"name": "DISNOLOAD",  "description": "Désactive la détection de charge nulle (no-load). Bit 0=désactive puissance active, Bit 1=réactive, Bit 2=apparente.", "unit": "", "type": "int"},
    {"name": "CONFIG",     "description": "Registre de configuration général. Contrôle le mode de sortie CF, la polarité, le mode haute-passe, etc.", "unit": "", "type": "int"},
    {"name": "CF1DEN",     "description": "Diviseur de la sortie impulsionnelle CF1 pour la mesure d'énergie active canal A.", "unit": "", "type": "int"},
    {"name": "CF2DEN",     "description": "Diviseur de la sortie impulsionnelle CF2 pour la mesure d'énergie active canal B.", "unit": "", "type": "int"},
    {"name": "MASK",       "description": "Masque des interruptions. Chaque bit active/désactive une source d'interruption spécifique.", "unit": "", "type": "int"},
]

_DEFAULT_SETTINGS = {
    "active":    True,
    "mode":      0,
    "reverse":   False,
    "gain":      0,
    "phase_cal": 0,
    "igain":     0,
    "irmsos":    0,
    "no_load":   False,
}

# Base load profiles for 16 channels (simulated)
_BASE_LOADS = [
    (5.2, 0.92), (3.1, 0.87), (8.4, 0.95), (1.2, 0.78),
    (6.7, 0.91), (2.3, 0.83), (9.1, 0.97), (0.8, 0.70),
    (4.5, 0.89), (7.3, 0.93), (1.9, 0.81), (5.8, 0.90),
    (3.6, 0.85), (10.2, 0.98), (0.5, 0.65), (6.1, 0.88),
]


class ADE7953:
    def __init__(self, i2c_bus=1, i2c_addr=0x38):
        self.i2c_bus = i2c_bus
        self.i2c_addr = i2c_addr
        self._start = time.time()
        self._channel_settings = {
            ch: dict(_DEFAULT_SETTINGS) for ch in range(1, NUM_CHANNELS + 1)
        }

    def _sim_noise(self, base, pct=0.02):
        return base * (1 + random.uniform(-pct, pct))

    def get_metrics(self):
        t = time.time()
        elapsed = t - self._start
        freq = self._sim_noise(50.0, 0.001)
        vrms = self._sim_noise(230.0)
        channels = {}

        for idx, (base_i, base_pf) in enumerate(_BASE_LOADS, start=1):
            cfg = self._channel_settings[idx]
            if not cfg["active"] or cfg["mode"] == 3:
                channels[idx] = {
                    "id": idx,
                    "online": False,
                    "active": cfg["active"],
                    "mode": cfg["mode"],
                    "settings": dict(cfg),
                    **{k: None for k in ("vrms","irms","active_power","reactive_power",
                                         "apparent_power","power_factor","frequency",
                                         "active_energy","reactive_energy","apparent_energy")},
                }
                continue

            phase = (t % (20 + idx)) / (20 + idx) * 2 * math.pi
            irms = self._sim_noise(base_i + 0.5 * math.sin(phase + idx))
            pf = self._sim_noise(base_pf)
            pf = max(0.0, min(1.0, pf))
            sign = -1 if cfg["reverse"] else 1

            awatt = sign * vrms * irms * pf
            avar  = sign * awatt * math.tan(math.acos(pf))
            ava   = vrms * irms

            channels[idx] = {
                "id":              idx,
                "online":          True,
                "active":          cfg["active"],
                "mode":            cfg["mode"],
                "vrms":            round(vrms, 2),
                "irms":            round(irms, 4),
                "active_power":    round(awatt, 2),
                "reactive_power":  round(avar, 2),
                "apparent_power":  round(ava, 2),
                "power_factor":    round(pf, 4),
                "frequency":       round(freq, 3),
                "active_energy":   round(awatt * elapsed / 3600, 4),
                "reactive_energy": round(avar  * elapsed / 3600, 4),
                "apparent_energy": round(ava   * elapsed / 3600, 4),
                "settings":        dict(cfg),
            }
        return channels

    def get_ade7953_config(self):
        return {
            "PGA_V": 0, "PGA_IA": 0, "PGA_IB": 0,
            "VGAIN": 0, "AIGAIN": 0, "BIGAIN": 0,
            "APHCAL": 0, "BPHCAL": 0,
            "VRMSOS": 0, "IRMSOS_A": 0, "IRMSOS_B": 0,
            "WTHR": 3, "VARTHR": 3, "VATHR": 3,
            "SAGLVL": 0, "SAGCYC": 0, "OILVL": 0,
            "LCYCMODE": 0x78, "DISNOLOAD": 0,
            "CONFIG": 0x8004,
            "CF1DEN": 83, "CF2DEN": 83,
            "MASK": 0,
        }

    def update_channel_setting(self, channel, param, value):
        if channel in self._channel_settings and param in self._channel_settings[channel]:
            self._channel_settings[channel][param] = value
            return True
        return False

    def get_system_info(self):
        return {
            "hardware":         HAS_HW,
            "ic":               "ADE7953",
            "manufacturer":     "Analog Devices",
            "i2c_bus":          self.i2c_bus,
            "i2c_addr":         hex(self.i2c_addr),
            "firmware_version": "1.0.0",
            "num_channels":     NUM_CHANNELS,
            "simulation_mode":  not HAS_HW,
        }


_device = None


def get_device():
    global _device
    if _device is None:
        _device = ADE7953()
    return _device
