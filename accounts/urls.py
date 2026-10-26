from django.urls import path

from . import views

app_name = "accounts"
urlpatterns = [
    path("giris/", views.login_view, name="login"),
    path("cikis/", views.logout_view, name="logout"),
    path("parola/", views.password_change, name="password_change"),
    path("kullanicilar/", views.user_list, name="user_list"),
    path("kullanicilar/yeni/", views.user_create, name="user_create"),
    path("kullanicilar/<int:pk>/", views.user_edit, name="user_edit"),
    path("rol-atama/<int:pk>/kaldir/", views.user_role_revoke, name="user_role_revoke"),
    path("roller/", views.role_list, name="role_list"),
    path("roller/yeni/", views.role_create, name="role_create"),
    path("roller/<int:pk>/", views.role_edit, name="role_edit"),
    path("roller/<int:pk>/<str:action>/", views.role_action, name="role_action"),
    path("vardiyalar/", views.shift_list, name="shift_list"),
    path("vardiyalar/<int:pk>/", views.shift_edit, name="shift_edit"),
]
