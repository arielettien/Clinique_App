from django.urls import path

from attachments import views

urlpatterns = [
    path("media/upload", views.upload_media),
    path("media/<str:media_id>", views.get_media),
]
