from django.urls import path

from . import views

app_name = "purchasing"

urlpatterns = [
    path("", views.po_list, name="po_list"),
    path("new/", views.po_create, name="po_create"),
    path("renewals/", views.renewals, name="renewals"),
    path("inbox/", views.inbox, name="inbox"),
    path("from-evaluation/<int:evaluation_id>/", views.po_from_evaluation, name="po_from_evaluation"),
    path("steps/<int:step_id>/decide/", views.decide, name="decide"),
    path("<int:pk>/", views.po_detail, name="po_detail"),
    path("<int:pk>/edit/", views.po_edit, name="po_edit"),
    path("<int:pk>/print/", views.po_print, name="po_print"),
    path("<int:pk>/submit/", views.po_submit, name="po_submit"),
    path("<int:pk>/cancel/", views.po_cancel, name="po_cancel"),
    path("<int:pk>/issue/", views.po_issue, name="po_issue"),
    path("<int:pk>/close/", views.po_close, name="po_close"),
    path("<int:pk>/pause/", views.po_pause, name="po_pause"),
    path("<int:pk>/resume/", views.po_resume, name="po_resume"),
    path("<int:pk>/renew/", views.po_renew, name="po_renew"),
]
