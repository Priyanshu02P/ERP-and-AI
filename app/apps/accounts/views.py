"""Admin-only user & role management (login/logout use Django's built-in views)."""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from apps.audit.services import record

from .forms import UserForm
from .models import User
from .permissions import role_required
from .roles import Role


@role_required(Role.ADMIN)
def user_list(request):
    users = User.objects.select_related("department").order_by("username")
    return render(request, "accounts/user_list.html", {"users": users})


@role_required(Role.ADMIN)
def user_create(request):
    form = UserForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        record(request.user, "USER_CREATED", user, data={"role": user.role})
        messages.success(request, f"User {user.username} created as {user.get_role_display()}.")
        return redirect("accounts:user_list")
    return render(request, "accounts/user_form.html", {"form": form, "title": "New user"})


@role_required(Role.ADMIN)
def user_update(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user.is_hardcoded_admin:
        messages.error(request, "The built-in Admin is defined in settings and cannot be edited here.")
        return redirect("accounts:user_list")

    before = {"role": user.role, "is_active": user.is_active, "approval_limit": user.approval_limit}
    form = UserForm(request.POST or None, instance=user)
    if user.pk == request.user.pk:  # guard against an admin demoting/disabling themselves
        form.fields["role"].disabled = True
        form.fields["is_active"].disabled = True
    if request.method == "POST" and form.is_valid():
        user = form.save()
        after = {"role": user.role, "is_active": user.is_active, "approval_limit": user.approval_limit}
        record(request.user, "USER_UPDATED", user, data={"before": before, "after": after})
        messages.success(request, f"User {user.username} updated.")
        return redirect("accounts:user_list")
    return render(request, "accounts/user_form.html", {"form": form, "title": f"Edit {user.username}"})
