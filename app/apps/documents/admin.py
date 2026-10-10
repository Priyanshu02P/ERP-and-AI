from django.contrib import admin

from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "owner_type", "owner_id", "kind", "size_bytes", "uploaded_by", "uploaded_at", "is_deleted")
    list_filter = ("owner_type", "kind", "is_deleted")
    readonly_fields = [f.name for f in Document._meta.fields]

    def has_add_permission(self, request):
        return False
