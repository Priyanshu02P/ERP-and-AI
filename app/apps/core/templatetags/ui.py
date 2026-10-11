"""Template helpers for consistent UI."""
from django import template
from django.utils.html import format_html

register = template.Library()

_COLORS = {
    "DRAFT": "secondary", "IN_APPROVAL": "warning", "APPROVED": "success", "REJECTED": "danger",
    "RETURNED": "info", "RFQ_CREATED": "primary", "CANCELLED": "dark", "CLOSED": "dark",
    "ISSUED": "primary", "RESPONSES_CLOSED": "secondary", "RECEIVED": "info",
    "UNDER_REVIEW": "warning", "SHORTLISTED": "success", "SUPERSEDED": "light",
    "PENDING": "warning", "SKIPPED": "light", "INVITED": "secondary", "RESPONDED": "success",
}


@register.filter
def badge(status):
    """{{ obj.status|badge }} -> coloured pill with a humanised label."""
    color = _COLORS.get(str(status), "secondary")
    return format_html('<span class="badge text-bg-{}">{}</span>', color, str(status).replace("_", " ").title())


@register.filter
def get_item(mapping, key):
    """{{ some_dict|get_item:key }} for dicts keyed by variables."""
    return mapping.get(key, [])
