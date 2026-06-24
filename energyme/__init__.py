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
        "energyme.host":               cfg.get(section, "host",               fallback="energyme.local"),
        "energyme.username":           cfg.get(section, "username",           fallback="admin"),
        "energyme.password":           cfg.get(section, "password",           fallback="energyme"),
        "energyme.timeout":            cfg.get(section, "timeout",            fallback="5"),
        "energyme.poll_delay_ms":      cfg.get(section, "poll_delay_ms",      fallback="200"),
        "energyme.ts_db_path":         cfg.get(section, "ts_db_path",         fallback="energyme_ts.db"),
        "energyme.collector_interval": cfg.get(section, "collector_interval", fallback="30"),
        "energyme.ts_retention_days":  cfg.get(section, "ts_retention_days",  fallback="7"),
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
    config.add_route("trends_api",        "/api/trends")
    config.add_route("history_clear_api", "/api/history/clear")   # avant history_api
    config.add_route("history_api",       "/api/history/{channel}")
    config.add_route("auto_reset_api",    "/api/auto-reset")
    config.add_route("load_discard_api",  "/api/load-discard")

    _init_timeseries(config, settings)

    config.scan(".views")
    return config.make_wsgi_app()


def _init_timeseries(config, settings: dict) -> None:
    """Initialise le store time series et démarre le collecteur en arrière-plan."""
    try:
        from .timeseries import TimeSeriesStore
        from .collector import BackgroundCollector
        from .client import EnergyMeClient

        db_path = settings.get("energyme.ts_db_path", "energyme_ts.db")
        ts_store = TimeSeriesStore(db_path=db_path)
        config.registry.ts_store = ts_store

        collector_client = EnergyMeClient(
            host=settings.get("energyme.host",          "energyme.local"),
            username=settings.get("energyme.username",  "admin"),
            password=settings.get("energyme.password",  "energyme"),
            timeout=int(settings.get("energyme.timeout", "5")),
            poll_delay_ms=int(settings.get("energyme.poll_delay_ms", "200")),
        )
        interval = int(settings.get("energyme.collector_interval", "30"))
        collector = BackgroundCollector(collector_client, ts_store, interval=interval)
        collector.start()
        config.registry.collector = collector

    except Exception:
        log.exception("Impossible d'initialiser le collecteur time series — tendances désactivées")
        config.registry.ts_store = None
