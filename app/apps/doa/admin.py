from django.contrib import admin

from .models import DoARule, DoARuleStep


class DoARuleStepInline(admin.TabularInline):
    model = DoARuleStep
    extra = 2


@admin.register(DoARule)
class DoARuleAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "department", "min_amount", "max_amount", "priority", "is_active")
    list_filter = ("is_active", "category", "department")
    inlines = [DoARuleStepInline]
