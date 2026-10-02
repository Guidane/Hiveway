from django.apps import AppConfig


class BuilderConfig(AppConfig):
    name = 'builder'
    verbose_name = 'Builder'

    def ready(self):
        from . import signals  # noqa: F401
