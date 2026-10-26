from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_not_required
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.forms import ConfirmForm, ReasonForm
from core.services import history_for
from core.viewutils import run_form_action

from . import services
from .forms import LoginForm, PasswordChangeForm, RoleForm, RoleGrantForm, ShiftForm, UserCreateForm, UserUpdateForm
from .models import AccessRole, RoleStatus, Shift, User, UserRole
from .permissions import can, check, require_any, require_perm


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


def _assignable_roles(actor):
    from .catalog import BY_CODE
    roles = AccessRole.objects.filter(status=RoleStatus.ACTIVE)
    if can(actor, "user.manage"):
        return roles
    admin_only = [c for c, a in BY_CODE.items() if a.admin_only]
    return roles.filter(hr_assignable=True).exclude(permissions__action__in=admin_only)


@require_any("user.manage", "hr.user.manage")
def user_list(request):
    users = User.objects.select_related("shift").prefetch_related("role_assignments__role")
    return render(request, "accounts/user_list.html", {"users": users, "admins": services.admin_user_ids()})


@require_any("user.manage", "hr.user.manage")
def user_create(request):
    form = UserCreateForm(request.POST or None, assignable=_assignable_roles(request.user))
    if request.method == "POST":
        resp = run_form_action(
            request, form, action="user.create",
            fn=lambda d: services.create_user(request.user, personnel_no=d["personnel_no"], full_name=d["full_name"],
                                              password=d["password"], team=d["team"], shift=d["shift"],
                                              roles=list(d["roles"]), reason=d["reason"]),
            success_message=lambda u: f"{u.personnel_no} kullanıcısı oluşturuldu.",
            url_for=lambda u: reverse("accounts:user_edit", args=[u.pk]),
        )
        if resp:
            return resp
    return render(request, "core/form_page.html", {"form": form, "title": "Yeni kullanıcı / personel", "submit": "Kaydet",
                                                   "back": reverse("accounts:user_list")})


@require_any("user.manage", "hr.user.manage")
def user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    url = reverse("accounts:user_edit", args=[pk])
    form = UserUpdateForm(request.POST if request.POST.get("form") == "user" else None, instance=user)
    grant = RoleGrantForm(request.POST if request.POST.get("form") == "grant" else None,
                          assignable=_assignable_roles(request.user))
    if request.method == "POST" and request.POST.get("form") == "user":
        resp = run_form_action(
            request, form, action="user.update",
            fn=lambda d: services.update_user(request.user, user, full_name=d["full_name"], team=d["team"],
                                              shift=d["shift"], is_active=d["is_active"],
                                              new_password=d.get("new_password"), machines=d["machines"],
                                              reason=d["reason"]),
            success_message="Kullanıcı güncellendi.", url_for=lambda u: url)
        if resp:
            return resp
        user.refresh_from_db()
    if request.method == "POST" and request.POST.get("form") == "grant":
        resp = run_form_action(request, grant, action="user.role_grant",
                               fn=lambda d: services.grant_role(request.user, user, d["role"], d["reason"]),
                               success_message="Rol atandı.", url_for=lambda _: url)
        if resp:
            return resp
    return render(request, "accounts/user_edit.html", {
        "target": user, "form": form, "grant": grant, "is_admin_account": services.is_admin_account(user),
        "assignments": user.role_assignments.select_related("role", "granted_by", "revoked_by"),
        "revoke_form": ReasonForm(), "history": history_for(user),
    })


@require_POST
@require_any("user.manage", "hr.user.manage")
def user_role_revoke(request, pk):
    ur = get_object_or_404(UserRole, pk=pk)
    url = reverse("accounts:user_edit", args=[ur.user_id])
    resp = run_form_action(request, ReasonForm(request.POST), action="user.role_revoke",
                           fn=lambda d: services.revoke_role(request.user, ur, d["reason"]),
                           success_message="Rol ataması kaldırıldı.", url_for=lambda _: url)
    return resp or redirect(url)


# --- Roller ----------------------------------------------------------------------------

@require_any("role.manage", "role.draft", "user.manage", "hr.user.manage")
def role_list(request):
    roles = AccessRole.objects.prefetch_related("permissions").annotate(
        user_count=Count("assignments", filter=Q(assignments__revoked_at__isnull=True), distinct=True))
    return render(request, "accounts/role_list.html", {"roles": roles})


def _role_form_page(request, role=None):
    admin = can(request.user, "role.manage")
    if not admin:
        check(request.user, "role.draft")
    form = RoleForm(request.POST or None, role=role, admin=admin)
    if request.method == "POST":
        def run(d):
            if role is None:
                return services.create_role(request.user, code=d["code"], name=d["name"],
                                            description=d["description"], hr_assignable=d.get("hr_assignable", False),
                                            perms=form.perms())
            return services.update_role(request.user, role, name=d["name"], description=d["description"],
                                        hr_assignable=d.get("hr_assignable", role.hr_assignable),
                                        perms=form.perms(), reason=d["reason"])
        resp = run_form_action(request, form, action="role.save", fn=run,
                               success_message=lambda r: f"Rol kaydedildi: {r.name} ({r.get_status_display()})",
                               url_for=lambda r: reverse("accounts:role_edit", args=[r.pk]))
        if resp:
            return resp
    return render(request, "accounts/role_form.html", {
        "form": form, "role": role, "admin": admin, "status_form": ReasonForm(),
        "history": history_for(role) if role else None,
        "users": role.assignments.filter(revoked_at__isnull=True).select_related("user") if role else None,
    })


def role_create(request):
    return _role_form_page(request)


def role_edit(request, pk):
    return _role_form_page(request, get_object_or_404(AccessRole, pk=pk))


@require_POST
def role_action(request, pk, action):
    role = get_object_or_404(AccessRole, pk=pk)
    url = reverse("accounts:role_edit", args=[pk])
    fns = {
        "onayla": lambda d: services.approve_role(request.user, role),
        "pasif": lambda d: services.set_role_status(request.user, role, RoleStatus.INACTIVE, d["reason"]),
        "aktif": lambda d: services.set_role_status(request.user, role, RoleStatus.ACTIVE, d["reason"]),
    }
    if action not in fns:
        raise Http404
    form = ConfirmForm(request.POST) if action == "onayla" else ReasonForm(request.POST)
    resp = run_form_action(request, form, action=f"role.{action}", fn=fns[action],
                           success_message="Rol durumu güncellendi.", url_for=lambda _: url)
    return resp or redirect(url)


# --- Vardiyalar --------------------------------------------------------------------------

@require_any("shift.manage", "user.manage", "hr.user.manage")
def shift_list(request):
    form = ShiftForm(request.POST or None) if can(request.user, "shift.manage") else None
    if request.method == "POST" and form is not None:
        resp = run_form_action(request, form, action="shift.create",
                               fn=lambda d: services.save_shift(request.user, None, d),
                               success_message="Vardiya kaydedildi.", url_for=lambda _: reverse("accounts:shift_list"))
        if resp:
            return resp
    return render(request, "accounts/shift_list.html", {"shifts": Shift.objects.all(), "form": form})


@require_perm("shift.manage")
def shift_edit(request, pk):
    shift = get_object_or_404(Shift, pk=pk)
    form = ShiftForm(request.POST or None, instance=shift)
    if request.method == "POST":
        resp = run_form_action(request, form, action="shift.update",
                               fn=lambda d: services.save_shift(request.user, shift, d),
                               success_message="Vardiya güncellendi.", url_for=lambda _: reverse("accounts:shift_list"))
        if resp:
            return resp
    return render(request, "core/form_page.html", {"form": form, "title": f"Vardiya: {shift}", "submit": "Kaydet",
                                                   "back": reverse("accounts:shift_list"), "history": history_for(shift)})
