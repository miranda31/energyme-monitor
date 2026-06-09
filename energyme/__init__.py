import configparser
import logging
import os

from pyramid.config import Configurator

log = logging.getLogger(__name__)

_CONFIG_PATHS = [
    os.path.join(os.getcwd(), "config.ini"),
    os.path.join(os.path.dirname(__file__), "..", "config.ini"),
]


def _load_app_config() -> dict:
    """Charge config.ini (chemin relatif au répertoire courant)."""
    cfg = configparser.ConfigParser()
    loaded = cfg.read(_CONFIG_PATHS)
    if not loaded:
        log.warning(
            "config.ini introuvable – utilisez config.example.ini comme modèle. "
            "L'application démarrera en mode dégradé."
        )
        return {}

    section = "energyme"
    if section not in cfg:
        log.warning("Section [energyme] absente de config.ini.")
        return {}

    return {
        "energyme.host":     cfg.get(section, "host",     fallback="energyme.local"),
        "energyme.username": cfg.get(section, "username", fallback="admin"),
        "energyme.password": cfg.get(section, "password", fallback="energyme"),
        "energyme.timeout":  cfg.get(section, "timeout",  fallback="10"),
    }


def main(global_config, **settings):
    app_cfg = _load_app_config()
    settings.update(app_cfg)

    config = Configurator(settings=settings)
    config.include("pyramid_jinja2")
    config.add_jinja2_search_path("energyme:templates")
    config.add_static_view("static", "energyme:static", cache_max_age=3600)

    config.add_route("metrics",        "/")
    config.add_route("update_channel", "/channel/{channel}/update", request_method="POST")
    config.add_route("config",         "/config")
    config.add_route("system",         "/system")

    config.scan(".views")
    return config.make_wsgi_app()
