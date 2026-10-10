from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only viewer."""

    list_display = ("created_at", "actor_name", "action", "entity_type", "entity_label", "ip")
    list_filter = ("action", "entity_type")
    search_fields = ("actor_name", "entity_label", "action")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
