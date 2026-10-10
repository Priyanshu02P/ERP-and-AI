from django.urls import path

from . import views

app_name = "requisitions"

urlpatterns = [
    path("", views.pr_list, name="pr_list"),
    path("new/", views.pr_create, name="pr_create"),
    path("inbox/", views.inbox, name="inbox"),
    path("<int:pk>/", views.pr_detail, name="pr_detail"),
    path("<int:pk>/edit/", views.pr_edit, name="pr_edit"),
    path("<int:pk>/submit/", views.pr_submit, name="pr_submit"),
    path("<int:pk>/cancel/", views.pr_cancel, name="pr_cancel"),
    path("steps/<int:step_id>/decide/", views.decide, name="decide"),
]
