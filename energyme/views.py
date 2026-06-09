import logging
import time

from pyramid.view import view_config
from pyramid.httpexceptions import HTTPBadRequest

from .client import (
    EnergyMeClient, EnergyMeError,
    ADE7953_CONFIG_DESC, CHANNEL_EDIT_FIELDS, ROLE_LABELS,
)

log = logging.getLogger(__name__)
_start_time = time.time()


def _get_client(request) -> EnergyMeClient:
    s = request.registry.settings
    return EnergyMeClient(
        host=s.get("energyme.host",     "energyme.local"),
        username=s.get("energyme.username", "admin"),
        password=s.get("energyme.password", "energyme"),
        timeout=int(s.get("energyme.timeout", 10)),
    )


def _uptime_str() -> str:
    s = int(time.time() - _start_time)
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}h {m:02d}m {s:02d}s"


# ── Métriques ─────────────────────────────────────────────────────────────────

@view_config(route_name="metrics", renderer="metrics.jinja2")
def metrics_view(request):
    client = _get_client(request)
    error = None
    channels = []
    frequency = None

    try:
        channels = client.get_channels_with_metrics()
        frequency = client.get_grid_frequency()
    except EnergyMeError as exc:
        error = str(exc)
        log.warning("Erreur métriques : %s", exc)

    return {
        "channels":     channels,
        "frequency":    frequency,
        "edit_fields":  CHANNEL_EDIT_FIELDS,
        "role_labels":  ROLE_LABELS,
        "error":        error,
        "page":         "metrics",
    }


# ── Mise à jour d'un canal ────────────────────────────────────────────────────

@view_config(route_name="update_channel", renderer="json")
def update_channel_view(request):
    try:
        index = int(request.matchdict["channel"])
        if not (0 <= index <= 16):
            raise ValueError
    except ValueError:
        raise HTTPBadRequest("Index de canal invalide (0–16)")

    client = _get_client(request)
    fields: dict = {}

    for f in CHANNEL_EDIT_FIELDS:
        name = f["name"]
        if name not in request.POST:
            continue
        raw = request.POST[name]
        if f["type"] == "bool":
            fields[name] = raw in ("1", "true", "on")
        elif f["type"] == "select" and name == "phase":
            fields[name] = int(raw)
        elif f["type"] in ("float",):
            fields[name] = float(raw)
        else:
            fields[name] = raw

    if not fields:
        raise HTTPBadRequest("Aucun champ à mettre à jour")

    try:
        result = client.update_channel(index, fields)
        return {"status": "ok", "channel": index, "device_response": result}
    except EnergyMeError as exc:
        log.error("Erreur update canal %d : %s", index, exc)
        return {"status": "error", "message": str(exc)}


# ── Configuration ADE7953 ─────────────────────────────────────────────────────

@view_config(route_name="config", renderer="config.jinja2")
def config_view(request):
    client = _get_client(request)
    error = None
    config_rows = []

    try:
        raw = client.get_ade7953_config()
        for key, value in raw.items():
            desc, unit = ADE7953_CONFIG_DESC.get(key, (key, ""))
            config_rows.append({
                "name":        key,
                "value":       value,
                "description": desc,
                "unit":        unit,
            })
    except EnergyMeError as exc:
        error = str(exc)
        log.warning("Erreur config ADE7953 : %s", exc)

    return {"config_rows": config_rows, "error": error, "page": "config"}


# ── Système ───────────────────────────────────────────────────────────────────

@view_config(route_name="system", renderer="system.jinja2")
def system_view(request):
    client = _get_client(request)
    system_info = None
    firmware_info = None
    error = None

    try:
        system_info = client.get_system_info()
    except EnergyMeError as exc:
        error = str(exc)
        log.warning("Erreur system info : %s", exc)

    try:
        firmware_info = client.get_firmware_update_info()
    except EnergyMeError as exc:
        log.warning("Erreur firmware update info : %s", exc)

    # Extraction des champs utiles avec valeurs par défaut sûres
    static  = (system_info or {}).get("static",  {})
    dynamic = (system_info or {}).get("dynamic", {})

    fw      = static.get("firmware", {})
    device  = static.get("device",   {})
    factory = static.get("factory",  {})
    network = dynamic.get("network", {})
    memory  = dynamic.get("memory",  {})
    storage = dynamic.get("storage", {})
    perf    = dynamic.get("performance", {})
    cpu     = dynamic.get("cpu",     {})
    uptime_device = dynamic.get("time", {}).get("uptimeSeconds")

    return {
        "uptime_server":  _uptime_str(),
        "uptime_device":  uptime_device,
        "firmware":       fw,
        "device":         device,
        "factory":        factory,
        "network":        network,
        "memory":         memory,
        "storage":        storage,
        "performance":    perf,
        "cpu":            cpu,
        "firmware_info":  firmware_info,
        "error":          error,
        "page":           "system",
    }
