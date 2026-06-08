import json
import time
import requests

from pyramid.view import view_config
from pyramid.httpexceptions import HTTPFound, HTTPBadRequest

from .ade7953 import get_device, CONFIG_PARAMS, CHANNEL_PARAMS, PGA_GAINS

GITHUB_REPO = "miranda31/energyme-monitor"
_start_time = time.time()


def _fetch_latest_release():
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        r = requests.get(url, timeout=3, headers={"Accept": "application/vnd.github+json"})
        if r.status_code == 200:
            data = r.json()
            return {
                "tag": data.get("tag_name", "—"),
                "name": data.get("name", "—"),
                "published": data.get("published_at", "—")[:10] if data.get("published_at") else "—",
                "url": data.get("html_url", "#"),
                "body": data.get("body", ""),
            }
        if r.status_code == 404:
            return {"tag": "Aucune release", "name": "—", "published": "—", "url": "#", "body": ""}
    except Exception:
        pass
    return None


@view_config(route_name="metrics", renderer="metrics.jinja2")
def metrics_view(request):
    device = get_device()
    channels = device.get_metrics()
    return {
        "channels": channels,
        "channel_params": CHANNEL_PARAMS,
        "pga_gains": PGA_GAINS,
        "page": "metrics",
    }


@view_config(route_name="update_channel", renderer="json")
def update_channel_view(request):
    channel = request.matchdict["channel"].upper()
    if channel not in ("A", "B"):
        raise HTTPBadRequest("Canal invalide")
    device = get_device()
    updated = {}
    for param_def in CHANNEL_PARAMS:
        name = param_def["name"]
        if name in request.POST:
            raw = request.POST[name]
            if param_def["type"] == "bool":
                value = raw in ("1", "true", "on")
            elif param_def["type"] == "select":
                value = int(raw)
            else:
                value = int(raw)
            device.update_channel_setting(channel, name, value)
            updated[name] = value
    return {"status": "ok", "channel": channel, "updated": updated}


@view_config(route_name="config", renderer="config.jinja2")
def config_view(request):
    device = get_device()
    raw_config = device.get_ade7953_config()
    config_rows = []
    for p in CONFIG_PARAMS:
        name = p["name"]
        val = raw_config.get(name, "—")
        display = val
        if p["type"] == "select" and isinstance(val, int):
            display = p["options"].get(val, val)
        config_rows.append({
            "name": name,
            "value": val,
            "display": display,
            "description": p["description"],
            "unit": p.get("unit", ""),
            "type": p["type"],
        })
    return {"config_rows": config_rows, "page": "config"}


@view_config(route_name="system", renderer="system.jinja2")
def system_view(request):
    device = get_device()
    info = device.get_system_info()

    uptime_s = int(time.time() - _start_time)
    h, rem = divmod(uptime_s, 3600)
    m, s = divmod(rem, 60)
    uptime_str = f"{h:02d}h {m:02d}m {s:02d}s"

    latest = _fetch_latest_release()

    current_version = info["firmware_version"]
    up_to_date = None
    if latest and latest["tag"] not in ("Aucune release", "—"):
        tag = latest["tag"].lstrip("v")
        up_to_date = tag == current_version

    return {
        "info": info,
        "uptime": uptime_str,
        "latest_release": latest,
        "current_version": current_version,
        "up_to_date": up_to_date,
        "github_repo": GITHUB_REPO,
        "page": "system",
    }
