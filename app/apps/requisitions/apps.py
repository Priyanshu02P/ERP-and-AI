from django.apps import AppConfig


class RequisitionsConfig(AppConfig):
    name = "apps.requisitions"
    label = "requisitions"

    def ready(self):
        from . import documents_integration  # noqa: F401  (registers PR as a document owner)
