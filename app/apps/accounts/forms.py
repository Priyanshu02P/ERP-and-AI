"""Admin-side user management forms."""
from django import forms
from django.conf import settings
from django.contrib.auth.password_validation import validate_password

from apps.core.forms import BootstrapFormMixin

from .models import User


class UserForm(BootstrapFormMixin, forms.ModelForm):
    """Create or edit a user, including their role. Password is required only on create."""

    password1 = forms.CharField(label="Password", widget=forms.PasswordInput(render_value=False), required=False)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput(render_value=False), required=False)

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "role", "department", "approval_limit", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_create = self.instance.pk is None
        if self.is_create:
            self.fields["password1"].required = self.fields["password2"].required = True
        else:
            self.fields["password1"].help_text = "Leave blank to keep the current password."

    def clean_username(self):
        username = self.cleaned_data["username"]
        if self.is_create and username.lower() == settings.HARDCODED_ADMIN["username"].lower():
            raise forms.ValidationError("This username is reserved for the built-in Admin.")
        return username

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 or p2:
            if p1 != p2:
                self.add_error("password2", "Passwords do not match.")
            else:
                try:
                    validate_password(p1, self.instance)
                except forms.ValidationError as exc:
                    self.add_error("password1", exc)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get("password1"):
            user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user
