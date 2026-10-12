from django.urls import path

from cases import exports, views

urlpatterns = [
    path("cases", views.create_case),
    path("cases/<str:case_id>", views.case_detail),
    path("cases/<str:case_id>/close", views.close_case),
    path("cases/<str:case_id>/reopen", views.reopen_case),
    path("cases/<str:case_id>/timeline", views.case_timeline),
    path("patients/<str:patient_id>/cases", views.patient_cases),
    path("projects/<str:project_id>/cases", views.project_cases),
    path("projects/<str:project_id>/cases/export", exports.export_cases),
    path("projects/<str:project_id>/cases/stats", exports.case_stats),
    path("projects/<str:project_id>/export", exports.export_project_summary),
]
