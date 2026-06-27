import logging
import time

from pyramid.view import view_config
from pyramid.httpexceptions import HTTPBadRequest

import base64
import struct

from pyramid.response import Response

from .client import (
    EnergyMeClient, EnergyMeError,
    ADE7953_CONFIG_DESC, CHANNEL_EDIT_FIELDS, ROLE_LABELS, LOG_LEVELS,
)

log = logging.getLogger(__name__)
_start_time = time.time()


def _get_client(request) -> EnergyMeClient:
    s = request.registry.settings
    return EnergyMeClient(
        host=s.get("energyme.host",          "energyme.local"),
        username=s.get("energyme.username",  "admin"),
        password=s.get("energyme.password",  "energyme"),
        timeout=int(s.get("energyme.timeout", 5)),
        poll_delay_ms=int(s.get("energyme.poll_delay_ms", 200)),
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

    channels = []
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
        channels = client.get_channels()
    except EnergyMeError as exc:
        error = str(exc)
        log.warning("Erreur config ADE7953 : %s", exc)

    return {"config_rows": config_rows, "channels": channels, "role_labels": ROLE_LABELS, "error": error, "page": "config"}


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


# ── Issues ────────────────────────────────────────────────────────────────────

@view_config(route_name="issues", renderer="issues.jinja2")
def issues_view(request):
    client = _get_client(request)
    issues = []
    error = None

    try:
        issues = client.get_issues()
    except EnergyMeError as exc:
        error = str(exc)
        log.warning("Erreur issues : %s", exc)

    error_count   = sum(1 for i in issues if i.get("severity") == "error"   and "unacked" in i.get("state", ""))
    warning_count = sum(1 for i in issues if i.get("severity") == "warning" and "unacked" in i.get("state", ""))
    info_count    = sum(1 for i in issues if i.get("severity") == "info"    and "unacked" in i.get("state", ""))

    return {
        "issues":          issues,
        "error":           error,
        "error_count":     error_count,
        "warning_count":   warning_count,
        "info_count":      info_count,
        "issue_error_count": error_count,
        "page":            "issues",
    }


@view_config(route_name="issues_ack", renderer="json")
def acknowledge_view(request):
    client = _get_client(request)
    all_issues = request.POST.get("all") in ("1", "true")
    code    = request.POST.get("code") or None
    channel = request.POST.get("channel")
    channel = int(channel) if channel is not None and channel != "" else None

    try:
        result = client.acknowledge_issue(code=code, channel=channel, all_issues=all_issues)
        return {"status": "ok", "device_response": result}
    except EnergyMeError as exc:
        log.error("Erreur ack issue : %s", exc)
        return {"status": "error", "message": str(exc)}


# ── Crashes ───────────────────────────────────────────────────────────────────

@view_config(route_name="crashes", renderer="crashes.jinja2")
def crashes_view(request):
    client = _get_client(request)
    crash_info = None
    error = None

    try:
        crash_info = client.get_crash_info()
    except EnergyMeError as exc:
        error = str(exc)
        log.warning("Erreur crash info : %s", exc)

    return {
        "crash_info": crash_info,
        "error":      error,
        "page":       "crashes",
    }


@view_config(route_name="crashes_clear", renderer="json")
def clear_crash_view(request):
    client = _get_client(request)
    try:
        result = client.clear_crash()
        return {"status": "ok", "device_response": result}
    except EnergyMeError as exc:
        log.error("Erreur clear crash : %s", exc)
        return {"status": "error", "message": str(exc)}


@view_config(route_name="crashes_dump")
def crashes_dump_view(request):
    client = _get_client(request)
    offset = int(request.params.get("offset", 0))
    size   = int(request.params.get("size",   65536))

    try:
        chunk = client.get_crash_dump(offset=offset, size=size)
        data_b64 = chunk.get("data", "")
        raw = base64.b64decode(data_b64) if data_b64 else b""
        response = Response(raw, content_type="application/octet-stream")
        response.headers["Content-Disposition"] = "attachment; filename=crash_dump.bin"
        return response
    except EnergyMeError as exc:
        return Response(str(exc), status=502, content_type="text/plain")


# ── Logs ──────────────────────────────────────────────────────────────────────

@view_config(route_name="logs", renderer="logs.jinja2")
def logs_view(request):
    client = _get_client(request)
    log_level = {}
    log_content = ""
    syslog_dest = None
    error = None

    try:
        log_level   = client.get_log_level()
        syslog_dest = client.get_syslog_destination()
        log_content = client.get_log_content()
    except EnergyMeError as exc:
        error = str(exc)
        log.warning("Erreur logs : %s", exc)

    return {
        "log_level":   log_level,
        "log_content": log_content,
        "syslog_dest": syslog_dest or "",
        "log_levels":  LOG_LEVELS,
        "error":       error,
        "page":        "logs",
    }


@view_config(route_name="logs_update", renderer="json")
def update_logs_view(request):
    client = _get_client(request)
    errors = []

    print_level = request.POST.get("print_level") or None
    save_level  = request.POST.get("save_level")  or None
    syslog_ip   = request.POST.get("syslog_dest")

    if print_level or save_level:
        try:
            client.update_log_level(print_level=print_level, save_level=save_level)
        except EnergyMeError as exc:
            errors.append(str(exc))

    if syslog_ip is not None:
        try:
            client.set_syslog_destination(syslog_ip.strip())
        except EnergyMeError as exc:
            errors.append(str(exc))

    if errors:
        return {"status": "error", "message": "; ".join(errors)}
    return {"status": "ok"}


@view_config(route_name="logs_clear", renderer="json")
def clear_logs_view(request):
    client = _get_client(request)
    try:
        result = client.clear_logs()
        return {"status": "ok", "device_response": result}
    except EnergyMeError as exc:
        log.error("Erreur clear logs : %s", exc)
        return {"status": "error", "message": str(exc)}
