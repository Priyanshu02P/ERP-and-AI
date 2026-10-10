"""Public API of the audit app: `record(...)`."""
from .middleware import get_current_request
from .models import AuditLog


def _client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    return (forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")) or None


def record(actor, action, entity=None, *, data=None):
    """
    Write one audit entry.

    actor:  the acting user (or None for system actions, e.g. cron)
    action: short verb code such as "PR_SUBMITTED"
    entity: the model instance affected (optional)
    data:   JSON-serialisable context (before/after values, comments ...)
    """
    return AuditLog.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        actor_name=getattr(actor, "username", "system"),
        action=action,
        entity_type=entity.__class__.__name__ if entity is not None else "",
        entity_id=str(entity.pk) if entity is not None else "",
        entity_label=str(entity)[:200] if entity is not None else "",
        data=data or {},
        ip=_client_ip(get_current_request()),
    )
