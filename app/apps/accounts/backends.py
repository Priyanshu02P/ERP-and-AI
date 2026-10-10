"""
Authentication backend for the HARD-CODED Admin account.

Credentials live in settings.HARDCODED_ADMIN. On a successful login a matching User row
is created/normalised (role=ADMIN, unusable DB password) so foreign keys and the audit
trail work. All other users authenticate via Django's ModelBackend.
"""
from hmac import compare_digest

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend

from .roles import Role


class HardcodedAdminBackend(BaseBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        cfg = settings.HARDCODED_ADMIN
        if not username or not password or username != cfg["username"]:
            return None  # not our user -> let the next backend try
        if not compare_digest(password.encode(), cfg["password"].encode()):
            return None

        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=cfg["username"],
            defaults={k: cfg[k] for k in ("email", "first_name", "last_name")},
        )
        if created or user.role != Role.ADMIN or not user.is_active or user.has_usable_password():
            user.role, user.is_active = Role.ADMIN, True
            user.set_unusable_password()  # the real password exists only in settings
            user.save()
        return user

    def get_user(self, user_id):
        user = get_user_model().objects.filter(pk=user_id).first()
        return user if user and user.is_active else None
