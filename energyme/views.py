import logging
import threading
import time
from datetime import datetime

from pyramid.view import view_config
from pyramid.httpexceptions import HTTPBadRequest

from .client import (
    EnergyMeClient, EnergyMeError,
    ADE7953_CONFIG_DESC, CHANNEL_EDIT_FIELDS, ROLE_LABELS,
)

log = logging.getLogger(__name__)
_start_time = time.time()
_thread_local = threading.local()


def _get_client(request) -> EnergyMeClient:
    """Retourne un client par thread (réutilise la session TCP, thread-safe)."""
    if not hasattr(_thread_local, "client"):
        s = request.registry.settings
        _thread_local.client = EnergyMeClient(
            host=s.get("energyme.host",          "energyme.local"),
            username=s.get("energyme.username",  "admin"),
            password=s.get("energyme.password",  "energyme"),
            timeout=int(s.get("energyme.timeout", 5)),
            poll_delay_ms=int(s.get("energyme.poll_delay_ms", 200)),
        )
        log.debug("Nouveau client EnergyMe créé pour le thread %s", threading.current_thread().name)
    return _thread_local.client


def _format_reset_ts(ts: float | None) -> str | None:
    if ts is None:
        return None
    dt = datetime.fromtimestamp(ts)
    today = datetime.now().date()
    return dt.strftime("%H:%M") if dt.date() == today else dt.strftime("%d/%m %H:%M")


def _uptime_str() -> str:
    s = int(time.time() - _start_time)
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}h {m:02d}m {s:02d}s"


# ── Métriques ─────────────────────────────────────────────────────────────────

@view_config(route_name="metrics", renderer="metrics.jinja2")
def metrics_view(request):
    client = _get_client(request)
    ts_store = getattr(request.registry, "ts_store", None)
    error = None
    channels = []
    frequency = None
    trends: dict = {}

    try:
        channels = client.get_channels_with_metrics()
        frequency = client.get_grid_frequency()
    except EnergyMeError as exc:
        error = str(exc)
        log.warning("Erreur métriques : %s", exc)

    last_resets: dict = {}
    load_discard: dict = {}
    if ts_store and channels:
        active_idx = [
            ch["index"] for ch in channels
            if ch.get("active") and ch.get("metrics") is not None
        ]
        try:
            trends = ts_store.get_all_trends(active_idx)
        except Exception:
            log.exception("Erreur lecture tendances")
        try:
            all_idx = [ch["index"] for ch in channels]
            raw_resets = ts_store.get_last_resets(all_idx)
            last_resets = {idx: _format_reset_ts(ts) for idx, ts in raw_resets.items()}
        except Exception:
            log.exception("Erreur lecture resets")
        try:
            load_discard = ts_store.get_all_load_discard_stats(active_idx)
        except Exception:
            log.exception("Erreur lecture scores load discard")

    return {
        "channels":      channels,
        "frequency":     frequency,
        "edit_fields":   CHANNEL_EDIT_FIELDS,
        "role_labels":   ROLE_LABELS,
        "error":         error,
        "page":          "metrics",
        "trends":        trends,
        "last_resets":   last_resets,
        "load_discard":  load_discard,
    }


# ── API Time Series ───────────────────────────────────────────────────────────

@view_config(route_name="trends_api", renderer="json")
def trends_api_view(request):
    """GET /api/trends?minutes=15  – tendances de tous les canaux actifs."""
    ts_store = getattr(request.registry, "ts_store", None)
    if not ts_store:
        return {"error": "Time series store indisponible"}

    minutes = min(int(request.params.get("minutes", 15)), 1440)
    try:
        client = _get_client(request)
        channels = client.get_channels_with_metrics()
        active_idx = [ch["index"] for ch in channels if ch.get("metrics") is not None]
        return ts_store.get_all_trends(active_idx, minutes=minutes)
    except EnergyMeError as exc:
        return {"error": str(exc)}


@view_config(route_name="history_api", renderer="json")
def history_api_view(request):
    """GET /api/history/{channel}?minutes=60  – historique brut d'un canal."""
    ts_store = getattr(request.registry, "ts_store", None)
    if not ts_store:
        return {"error": "Time series store indisponible"}

    try:
        channel = int(request.matchdict["channel"])
    except (ValueError, KeyError):
        return {"error": "Index de canal invalide"}

    minutes = min(int(request.params.get("minutes", 60)), 1440)
    return {"channel": channel, "data": ts_store.get_history(channel, minutes=minutes)}


# ── API Auto-reset ────────────────────────────────────────────────────────────

@view_config(route_name="auto_reset_api", renderer="json")
def auto_reset_api_view(request):
    """GET → état actuel ; POST {"enabled": true/false} → modifier."""
    collector = getattr(request.registry, "collector", None)
    if collector is None:
        return {"error": "Collecteur indisponible"}
    if request.method == "POST":
        try:
            body = request.json_body
            collector.auto_reset_enabled = bool(body.get("enabled", True))
        except (ValueError, AttributeError):
            from pyramid.httpexceptions import HTTPBadRequest
            raise HTTPBadRequest("Corps JSON invalide")
    return {"enabled": collector.auto_reset_enabled}


# ── API Load Discard (WDRR) ───────────────────────────────────────────────────

@view_config(route_name="load_discard_api", renderer="json")
def load_discard_api_view(request):
    """GET /api/load-discard?minutes=60 — scores d'instabilité WDRR par canal."""
    ts_store = getattr(request.registry, "ts_store", None)
    if not ts_store:
        return {"error": "Time series store indisponible"}

    minutes = min(int(request.params.get("minutes", 60)), 1440)
    try:
        client = _get_client(request)
        channels = client.get_channels_with_metrics()
        active_idx = [
            ch["index"] for ch in channels
            if ch.get("active") and ch.get("metrics") is not None
        ]
        return ts_store.get_all_load_discard_stats(active_idx, minutes=minutes)
    except EnergyMeError as exc:
        return {"error": str(exc)}


# ── API Effacement historique ──────────────────────────────────────────────────

@view_config(route_name="history_clear_api", renderer="json", request_method="POST")
def history_clear_api_view(request):
    """POST /api/history/clear — efface toutes les mesures et resets."""
    ts_store = getattr(request.registry, "ts_store", None)
    if not ts_store:
        return {"error": "Time series store indisponible"}
    collector = getattr(request.registry, "collector", None)
    if collector:
        collector.clear_cooldowns()
    deleted = ts_store.clear_all()
    return {"status": "ok", "deleted": deleted}


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
