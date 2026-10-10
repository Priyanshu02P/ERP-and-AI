"""
Owner-type registry: lets the documents app stay independent of PR/RFQ/Quotation code.

Each owning app calls `register(OwnerType(...))` from its AppConfig.ready(). The documents
app then asks the registry how to load an owner and whether a user may view / attach.
"""
from dataclasses import dataclass
from typing import Callable

_REGISTRY: dict = {}


@dataclass(frozen=True)
class OwnerType:
    code: str                                  # e.g. "PR"
    label: str                                 # human-readable
    default_kind: str                          # Document.kind used for uploads
    load: Callable[[int], object]              # pk -> owner instance or None
    reference: Callable[[object], str]         # owner -> short ref used in the storage key
    can_view: Callable[[object, object], bool]    # (user, owner) -> bool
    can_attach: Callable[[object, object], bool]  # (user, owner) -> bool


def register(owner_type: OwnerType) -> None:
    _REGISTRY[owner_type.code] = owner_type


def get(code: str) -> OwnerType:
    try:
        return _REGISTRY[code]
    except KeyError:
        raise KeyError(f"Unknown document owner type: {code}")
