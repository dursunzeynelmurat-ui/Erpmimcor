from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_not_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.services import history_for
from core.viewutils import run_form_action

from . import services
from .forms import LoginForm, PasswordChangeForm, UserCreateForm, UserUpdateForm
from .models import User
from .permissions import require_perm


@login_not_required
def login_view(request):
    if request.user.is_authenticated:
        return redirect("core:home")
    form = LoginForm(request, request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.user)
        nxt = request.GET.get("next") or request.POST.get("next")
        if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()}, require_https=request.is_secure()):
            return redirect(nxt)
        return redirect("core:home")
    return render(request, "accounts/login.html", {"form": form, "next": request.GET.get("next", "")})


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "Çıkış yapıldı.")
    return redirect("accounts:login")


def password_change(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="user.password_change",
            fn=lambda d: services.change_own_password(request.user, d["new_password"]),
            success_message="Parolanız değiştirildi.", url_for=lambda _: "/",
        )
        if resp:
            update_session_auth_hash(request, request.user)
            return resp
    return render(request, "core/form_page.html", {"form": form, "title": "Parola değiştir", "submit": "Değiştir"})


@require_perm("user.manage")
def user_list(request):
    return render(request, "accounts/user_list.html", {"users": User.objects.all()})


@require_perm("user.manage")
def user_create(request):
    form = UserCreateForm(request.POST or None)
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="user.create",
            fn=lambda d: services.create_user(request.user, personnel_no=d["personnel_no"], full_name=d["full_name"],
                                              role=d["role"], password=d["password"]),
            success_message=lambda u: f"{u.personnel_no} kullanıcısı oluşturuldu.",
            url_for=lambda u: f"/kullanicilar/{u.pk}/",
        )
        if resp:
            return resp
    return render(request, "core/form_page.html", {"form": form, "title": "Yeni kullanıcı", "submit": "Kaydet"})


@require_perm("user.manage")
def user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    form = UserUpdateForm(request.POST or None, instance=user)
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="user.update",
            fn=lambda d: services.update_user(request.user, user, full_name=d["full_name"], role=d["role"],
                                              is_active=d["is_active"], new_password=d.get("new_password")),
            success_message="Kullanıcı güncellendi.", url_for=lambda u: f"/kullanicilar/{u.pk}/",
        )
        if resp:
            return resp
        user.refresh_from_db()
    return render(request, "core/form_page.html", {
        "form": form, "title": f"Kullanıcı: {user}", "submit": "Kaydet", "history": history_for(user),
    })
