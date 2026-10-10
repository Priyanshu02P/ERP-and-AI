from django.contrib import admin

from .models import Category, Department, Vendor


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("code", "name")
    search_fields = ("code", "name")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_capex")


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "email", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name", "tax_id")
