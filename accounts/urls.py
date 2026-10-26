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
]
