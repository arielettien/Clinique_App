from django.urls import path

from spreadsheets import views

urlpatterns = [
    path("projects/<str:project_id>/spreadsheets", views.project_spreadsheets),
    path("spreadsheets/<str:spreadsheet_id>", views.spreadsheet_detail),
    path("spreadsheets/<str:spreadsheet_id>/preview", views.spreadsheet_preview),
    path("spreadsheets/<str:spreadsheet_id>/download", views.spreadsheet_download),
]
