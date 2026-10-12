from django.urls import path

from clinical_forms import exports, views

urlpatterns = [
    path("forms", views.forms_collection),
    path("forms/import", views.import_form),
    path("forms/<str:form_id>", views.form_detail),
    path("forms/<str:form_id>/public-link", views.public_link),
    path("forms/<str:form_id>/public-link/regenerate", views.regenerate_public_code),
    path("forms/<str:form_id>/export-template", views.export_form_template),
    path("forms/<str:form_id>/export", exports.export_form_data),
    path("forms/<str:form_id>/export-patients", exports.export_patients_data),
    path("forms/<str:form_id>/trend", exports.form_trend),
    path("forms/<str:form_id>/status-change", exports.detect_status_change),
    path("forms/<str:form_id>/referral-candidates", exports.referral_candidates),
]
