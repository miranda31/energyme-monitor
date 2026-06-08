from pyramid.config import Configurator


def main(global_config, **settings):
    config = Configurator(settings=settings)
    config.include("pyramid_jinja2")
    config.add_jinja2_search_path("energyme:templates")

    config.add_static_view("static", "energyme:static", cache_max_age=3600)

    config.add_route("metrics", "/")
    config.add_route("update_channel", "/channel/{channel}/update", request_method="POST")
    config.add_route("config", "/config")
    config.add_route("system", "/system")

    config.scan(".views")
    return config.make_wsgi_app()
