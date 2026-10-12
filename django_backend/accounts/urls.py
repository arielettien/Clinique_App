from django.urls import path

from accounts import views

urlpatterns = [
    path("auth/login", views.login_view),
    path("auth/logout", views.logout_view),
    path("auth/me", views.me_view),
    path("users", views.users_collection),
    path("users/<str:user_id>", views.user_detail),
]
