from django.urls import path

from patients import views

urlpatterns = [
    path("patients", views.patients_collection),
    path("patients/<str:patient_id>", views.patient_detail),
    path("projects/<str:project_id>/potential-duplicates", views.project_potential_duplicates),
    path("potential-duplicates/<str:dup_id>/dismiss", views.dismiss_potential_duplicate),
]
