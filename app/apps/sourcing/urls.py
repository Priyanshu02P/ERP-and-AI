from django.urls import path

from . import views

app_name = "sourcing"

urlpatterns = [
    path("rfqs/", views.rfq_list, name="rfq_list"),
    path("rfqs/from-pr/<int:pr_id>/", views.rfq_create, name="rfq_create"),
    path("rfqs/<int:pk>/", views.rfq_detail, name="rfq_detail"),
    path("rfqs/<int:pk>/issue/", views.rfq_issue, name="rfq_issue"),
    path("rfqs/<int:pk>/close/", views.rfq_close, name="rfq_close"),
    path("rfqs/<int:pk>/cancel/", views.rfq_cancel, name="rfq_cancel"),
    path("rfqs/<int:rfq_id>/quotations/new/", views.quotation_create, name="quotation_create"),
    path("quotations/<int:pk>/", views.quotation_detail, name="quotation_detail"),
    path("quotations/<int:pk>/status/", views.quotation_status, name="quotation_status"),
]
