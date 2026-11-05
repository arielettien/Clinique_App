from django.urls import path

from indicators import views

# Routes litterales avant les routes parametrees, comme dans
# clinical_forms/urls.py ("forms/import" avant "forms/<id>").
urlpatterns = [
    path("donors", views.donors),
    path("projects/<str:project_id>/dashboard", views.project_dashboard),
    path("projects/<str:project_id>/dashboard/settings", views.dashboard_settings),
    path("projects/<str:project_id>/indicators", views.indicators_collection),
    path("projects/<str:project_id>/sites", views.sites_collection),
    path("projects/<str:project_id>/sites/import", views.sites_import),
    path("indicators/<str:indicator_id>", views.indicator_detail),
    path("indicators/<str:indicator_id>/targets", views.indicator_targets),
    path("sites/<str:site_id>", views.site_detail),
]
