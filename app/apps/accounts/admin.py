from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User

_PROC = ("Procurement", {"fields": ("role", "department", "approval_limit")})


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (_PROC,)
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (_PROC,)
    list_display = ("username", "email", "role", "department", "is_active")
    list_filter = ("role", "is_active", "department")
