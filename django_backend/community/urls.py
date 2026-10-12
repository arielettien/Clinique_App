from django.urls import path

from community import views

urlpatterns = [
    path("public/forms/<str:form_id>", views.public_form),
    path("public/forms/<str:form_id>/patients", views.public_create_patient),
    path("public/forms/<str:form_id>/submissions", views.public_create_submission),
    path("public/forms/<str:form_id>/media/upload", views.public_upload_media),
]
