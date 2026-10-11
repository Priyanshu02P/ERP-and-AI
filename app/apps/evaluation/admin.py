from django.contrib import admin

from .models import Evaluation, EvaluationEntry


class EntryInline(admin.TabularInline):
    model = EvaluationEntry
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in EvaluationEntry._meta.fields]


@admin.register(Evaluation)
class EvaluationAdmin(admin.ModelAdmin):
    """Read-only: evaluations change only through the workflow (audited)."""

    list_display = ("rfq", "status", "created_by", "submitted_by", "reviewed_by", "updated_at")
    list_filter = ("status",)
    inlines = [EntryInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
