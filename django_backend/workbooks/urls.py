from django.urls import path

from workbooks import views

urlpatterns = [
    path("workbooks", views.workbooks_collection),
    path("workbooks/<str:workbook_id>", views.workbook_detail),
    path("workbooks/<str:workbook_id>/export", views.workbook_export),
]
