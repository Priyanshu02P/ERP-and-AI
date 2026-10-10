"""Gap-tolerant, race-safe document numbering (PR-2026-000123)."""
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import NumberSequence


def next_number(prefix: str, width: int = 6) -> str:
    """
    Return the next number for `prefix` in the current year.

    Must be called inside the same transaction that persists the document: the
    sequence row is locked with SELECT ... FOR UPDATE, so concurrent requests serialise
    and a rollback also rolls the counter back (no gaps from failed saves).
    """
    key = f"{prefix}-{timezone.now().year}"
    try:
        seq = NumberSequence.objects.select_for_update().get(key=key)
    except NumberSequence.DoesNotExist:
        try:
            with transaction.atomic():
                NumberSequence.objects.create(key=key, next_value=1)
        except IntegrityError:  # another request created it first
            pass
        seq = NumberSequence.objects.select_for_update().get(key=key)
    value = seq.next_value
    seq.next_value = value + 1
    seq.save(update_fields=["next_value"])
    return f"{key}-{value:0{width}d}"
