from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("upload/", views.upload, name="upload"),
    path("<int:pk>/download/", views.download, name="download"),
    path("<int:pk>/delete/", views.delete, name="delete"),
]
