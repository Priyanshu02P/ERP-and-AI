from django.apps import AppConfig


class SourcingConfig(AppConfig):
    name = "apps.sourcing"
    label = "sourcing"

    def ready(self):
        from . import documents_integration  # noqa: F401
