from django.apps import AppConfig


class EvaluationConfig(AppConfig):
    name = "apps.evaluation"
    label = "evaluation"

    def ready(self):
        from . import documents_integration  # noqa: F401  (evidence attachments)
