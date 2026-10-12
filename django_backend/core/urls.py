from django.urls import path

from core import views

urlpatterns = [
    path("audit-logs", views.audit_logs),
    path("health", views.health),
]
