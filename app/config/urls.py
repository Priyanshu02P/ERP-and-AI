"""Root URL configuration."""
from django.contrib import admin
from django.urls import include, path

from apps.core import views as core_views
from apps.sourcing import cron as sourcing_cron

urlpatterns = [
    path("", core_views.home, name="home"),
    path("", include("apps.accounts.urls")),
    path("requisitions/", include("apps.requisitions.urls")),
    path("sourcing/", include("apps.sourcing.urls")),
    path("documents/", include("apps.documents.urls")),
    # Django admin = configuration console (departments, categories, vendors, DoA, audit).
    path("manage/", admin.site.urls),
    # Vercel Cron (token protected)
    path("internal/cron/close-rfqs", sourcing_cron.close_rfqs, name="cron_close_rfqs"),
]
