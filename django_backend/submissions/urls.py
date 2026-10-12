from django.urls import path

from submissions import views

urlpatterns = [
    path("submissions", views.create_submission),
    path("submissions/<str:submission_id>", views.submission_detail),
    path("patients/<str:patient_id>/submissions", views.patient_submissions),
    path("forms/<str:form_id>/submissions", views.form_submissions),
]
