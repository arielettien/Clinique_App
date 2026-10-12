from django.urls import path

from projects import views

urlpatterns = [
    path("projects", views.projects_collection),
    path("projects/<str:project_id>", views.project_detail),
    path("projects/<str:project_id>/stats", views.project_stats),
]
