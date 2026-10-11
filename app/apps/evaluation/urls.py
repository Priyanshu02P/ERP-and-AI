from django.urls import path

from . import views

app_name = "evaluation"

urlpatterns = [
    path("rfq/<int:rfq_id>/compare/", views.compare, name="compare"),
    path("rfq/<int:rfq_id>/start/", views.start, name="start"),
    path("rfq/<int:rfq_id>/edit/", views.edit, name="edit"),
    path("rfq/<int:rfq_id>/", views.detail, name="detail"),
    path("rfq/<int:rfq_id>/review/", views.review, name="review"),
]
