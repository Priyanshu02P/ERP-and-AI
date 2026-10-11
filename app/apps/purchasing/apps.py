from django.apps import AppConfig


class PurchasingConfig(AppConfig):
    name = "apps.purchasing"
    label = "purchasing"

    def ready(self):
        from . import documents_integration  # noqa: F401
