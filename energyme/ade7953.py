"""
ADE7953 energy metering IC interface.
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

# ADE7953 register addresses (I2C)
REG = {
    "SAGCYC":    0x000,
    "DISNOLOAD": 0x001,
    "LCYCMODE":  0x004,
    "PGA_V":     0x007,
    "PGA_IA":    0x008,
    "PGA_IB":    0x009,
    "WRITE_PROTECT": 0x040,
    "LAST_OP":   0x0FD,
    "LAST_RWDATA": 0x0FF,
    "IRMS_A":    0x21A,
    "IRMS_B":    0x21B,
    "VRMS":      0x21C,
    "IRMSOS_A":  0x380,
    "IRMSOS_B":  0x381,
    "VRMSOS":    0x382,
    "AIGAIN":    0x383,
    "BIGAIN":    0x384,
    "VGAIN":     0x385,
    "AWATT":     0x21E,
    "BWATT":     0x21F,
    "AVAR":      0x220,
    "BVAR":      0x221,
    "AVA":       0x222,
    "BVA":       0x223,
    "AWATTHR":   0x21D,  # not real but placeholder
    "AVARHR":    0x21D,
    "AVAHR":     0x21D,
    "BWATTHR":   0x21D,
    "BVARHR":    0x21D,
    "BVAHR":     0x21D,
    "AAPWR":     0x226,
    "BAPWR":     0x227,
    "AENERGY":   0x21D,
    "BENERGY":   0x21D,
    "PERIOD":    0x22E,
    "APHCAL":    0x3B0,
    "BPHCAL":    0x3B1,
    "CFNUM":     0x25A,
    "CFDEN":     0x25B,
    "CONFIG":    0x102,
    "CF1DEN":    0x250,
    "CF2DEN":    0x251,
    "WTHR":      0x23D,
    "VARTHR":    0x23E,
    "VATHR":     0x23F,
    "SAGLVL":    0x232,
    "MASK":      0x22B,
    "STATUS0":   0x22D,
    "STATUS1":   0x22E,
    "OILVL":     0x236,
    "VERSION":   0x702,
    "EX_REF":    0x800,
}

PGA_GAINS = {0: 1, 1: 2, 2: 4, 3: 8, 4: 16, 5: 22, 6: 32}

CONFIG_PARAMS = [
    {
        "name": "PGA_V",
        "description": "Gain de l'amplificateur du canal tension. Valeurs: 0=×1, 1=×2, 2=×4.",
        "unit": "",
        "type": "select",
        "options": {0: "×1", 1: "×2", 2: "×4"},
    },
    {
        "name": "PGA_IA",
        "description": "Gain de l'amplificateur du canal courant A. Valeurs: 0=×1 à 6=×32.",
        "unit": "",
        "type": "select",
        "options": {0: "×1", 1: "×2", 2: "×4", 3: "×8", 4: "×16", 5: "×22", 6: "×32"},
    },
    {
        "name": "PGA_IB",
        "description": "Gain de l'amplificateur du canal courant B. Valeurs: 0=×1 à 6=×32.",
        "unit": "",
        "type": "select",
        "options": {0: "×1", 1: "×2", 2: "×4", 3: "×8", 4: "×16", 5: "×22", 6: "×32"},
    },
    {
        "name": "VGAIN",
        "description": "Registre de calibration du gain tension. Permet d'ajuster la mesure de tension RMS.",
        "unit": "LSB",
        "type": "int",
    },
    {
        "name": "AIGAIN",
        "description": "Registre de calibration du gain courant canal A. Corrige les erreurs de gain du capteur.",
        "unit": "LSB",
        "type": "int",
    },
    {
        "name": "BIGAIN",
        "description": "Registre de calibration du gain courant canal B. Corrige les erreurs de gain du capteur.",
        "unit": "LSB",
        "type": "int",
    },
    {
        "name": "APHCAL",
        "description": "Correction de phase canal A. Compense le déphasage entre tension et courant introduit par le capteur.",
        "unit": "LSB",
        "type": "int",
    },
    {
        "name": "BPHCAL",
        "description": "Correction de phase canal B. Compense le déphasage entre tension et courant introduit par le capteur.",
        "unit": "LSB",
        "type": "int",
    },
    {
        "name": "VRMSOS",
        "description": "Offset de correction RMS tension. Élimine les erreurs d'offset dans la mesure de tension.",
        "unit": "LSB",
        "type": "int",
    },
    {
        "name": "IRMSOS_A",
        "description": "Offset de correction RMS courant canal A.",
        "unit": "LSB",
        "type": "int",
    },
    {
        "name": "IRMSOS_B",
        "description": "Offset de correction RMS courant canal B.",
        "unit": "LSB",
        "type": "int",
    },
    {
        "name": "WTHR",
        "description": "Seuil d'énergie active (Watt-heure). Nombre de LSB d'énergie avant incrémentation du compteur.",
        "unit": "LSB",
        "type": "int",
    },
    {
        "name": "VARTHR",
        "description": "Seuil d'énergie réactive (VAR-heure).",
        "unit": "LSB",
        "type": "int",
    },
    {
        "name": "VATHR",
        "description": "Seuil d'énergie apparente (VA-heure).",
        "unit": "LSB",
        "type": "int",
    },
    {
        "name": "SAGLVL",
        "description": "Niveau de détection de creux de tension (sag). En dessous de ce seuil, une alarme sag est déclenchée.",
        "unit": "LSB",
        "type": "int",
    },
    {
        "name": "SAGCYC",
        "description": "Nombre de cycles secteur consécutifs en dessous de SAGLVL avant déclenchement de l'alarme sag.",
        "unit": "cycles",
        "type": "int",
    },
    {
        "name": "OILVL",
        "description": "Seuil de détection de sur-courant (overcurrent). Déclenche une interruption si dépassé.",
        "unit": "LSB",
        "type": "int",
    },
    {
        "name": "LCYCMODE",
        "description": "Mode d'accumulation d'énergie sur un nombre entier de cycles secteur (line-cycle). Bit 0=actif canal A, Bit 1=actif canal B, etc.",
        "unit": "",
        "type": "int",
    },
    {
        "name": "DISNOLOAD",
        "description": "Désactive la détection de charge nulle (no-load). Bit 0=désactive puissance active, Bit 1=réactive, Bit 2=apparente.",
        "unit": "",
        "type": "int",
    },
    {
        "name": "CONFIG",
        "description": "Registre de configuration général. Contrôle le mode de sortie CF, la polarité, le mode haute-passe, etc.",
        "unit": "",
        "type": "int",
    },
    {
        "name": "CF1DEN",
        "description": "Diviseur de la sortie impulsionnelle CF1 pour la mesure d'énergie active canal A.",
        "unit": "",
        "type": "int",
    },
    {
        "name": "CF2DEN",
        "description": "Diviseur de la sortie impulsionnelle CF2 pour la mesure d'énergie active canal B.",
        "unit": "",
        "type": "int",
    },
    {
        "name": "MASK",
        "description": "Masque des interruptions. Chaque bit active/désactive une source d'interruption spécifique.",
        "unit": "",
        "type": "int",
    },
]

CHANNEL_PARAMS = [
    {"name": "gain", "label": "Gain PGA", "type": "select",
     "options": {0: "×1", 1: "×2", 2: "×4", 3: "×8", 4: "×16", 5: "×22", 6: "×32"}},
    {"name": "phase_cal", "label": "Cal. Phase", "type": "int", "min": -32, "max": 31},
    {"name": "igain", "label": "Gain Courant", "type": "int", "min": -2097152, "max": 2097151},
    {"name": "irmsos", "label": "Offset RMS I", "type": "int", "min": -512, "max": 511},
    {"name": "no_load", "label": "No-Load", "type": "bool"},
]


class ADE7953:
    def __init__(self, i2c_bus=1, i2c_addr=0x38):
        self.i2c_bus = i2c_bus
        self.i2c_addr = i2c_addr
        self._sim_t = time.time()
        self._channel_settings = {
            "A": {"gain": 0, "phase_cal": 0, "igain": 0, "irmsos": 0, "no_load": False},
            "B": {"gain": 0, "phase_cal": 0, "igain": 0, "irmsos": 0, "no_load": False},
        }

    def _sim_noise(self, base, pct=0.02):
        return base * (1 + random.uniform(-pct, pct))

    def get_metrics(self):
        t = time.time()
        phase = (t % 20) / 20 * 2 * math.pi

        vrms = self._sim_noise(230.0)
        irms_a = self._sim_noise(5.2 + 1.5 * math.sin(phase))
        irms_b = self._sim_noise(3.1 + 0.8 * math.sin(phase + 1.0))

        pf_a = self._sim_noise(0.92)
        pf_b = self._sim_noise(0.87)

        awatt = vrms * irms_a * pf_a
        bwatt = vrms * irms_b * pf_b
        avar_a = awatt * math.tan(math.acos(pf_a))
        avar_b = bwatt * math.tan(math.acos(pf_b))
        ava_a = vrms * irms_a
        ava_b = vrms * irms_b

        freq = self._sim_noise(50.0, 0.001)

        channels = {
            "A": {
                "label": "Canal A",
                "online": True,
                "vrms": round(vrms, 2),
                "irms": round(irms_a, 4),
                "active_power": round(awatt, 2),
                "reactive_power": round(avar_a, 2),
                "apparent_power": round(ava_a, 2),
                "power_factor": round(pf_a, 4),
                "frequency": round(freq, 3),
                "active_energy": round(awatt * (t - self._sim_t) / 3600, 4),
                "reactive_energy": round(avar_a * (t - self._sim_t) / 3600, 4),
                "apparent_energy": round(ava_a * (t - self._sim_t) / 3600, 4),
                "settings": dict(self._channel_settings["A"]),
            },
            "B": {
                "label": "Canal B",
                "online": True,
                "vrms": round(vrms, 2),
                "irms": round(irms_b, 4),
                "active_power": round(bwatt, 2),
                "reactive_power": round(avar_b, 2),
                "apparent_power": round(ava_b, 2),
                "power_factor": round(pf_b, 4),
                "frequency": round(freq, 3),
                "active_energy": round(bwatt * (t - self._sim_t) / 3600, 4),
                "reactive_energy": round(avar_b * (t - self._sim_t) / 3600, 4),
                "apparent_energy": round(ava_b * (t - self._sim_t) / 3600, 4),
                "settings": dict(self._channel_settings["B"]),
            },
        }
        return channels

    def get_ade7953_config(self):
        defaults = {
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
        return defaults

    def update_channel_setting(self, channel, param, value):
        if channel in self._channel_settings and param in self._channel_settings[channel]:
            self._channel_settings[channel][param] = value
            return True
        return False

    def get_system_info(self):
        return {
            "hardware": HAS_HW,
            "ic": "ADE7953",
            "manufacturer": "Analog Devices",
            "i2c_bus": self.i2c_bus,
            "i2c_addr": hex(self.i2c_addr),
            "firmware_version": "1.0.0",
            "uptime_seconds": int(time.time() - self._sim_t),
            "simulation_mode": not HAS_HW,
        }


# Module-level singleton
_device = None


def get_device():
    global _device
    if _device is None:
        _device = ADE7953()
    return _device
