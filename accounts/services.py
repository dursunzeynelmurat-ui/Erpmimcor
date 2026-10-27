"""Kullanıcı, rol ve erişim yönetimi. Tüm değişiklikler kayıt altına alınır.

Sınırlar (yetkiden bağımsız, her zaman uygulanır):
- Sistem en az bir etkin yöneticiyle (system.admin yetkili aktif kullanıcı) kalmalı.
- Kimse kendi rol atamasını değiştiremez; kendi hesabını pasife alamaz.
- İK yalnız "İK atayabilir" işaretli, aktif ve yöneticiye özel yetki içermeyen rolleri atayabilir; yönetici
  hesaplarını değiştiremez/pasife alamaz; rol taslağı hazırlayabilir ama etkinleştiremez.
"""
from django.db import connection, transaction
from django.utils import timezone

from core.exceptions import BusinessRuleError
from core.services import audit, diff

from .catalog import BY_CODE, Scope
from .models import AccessRole, RolePermission, RoleStatus, User, UserMachine, UserRole
from .permissions import can, check

ADMIN_LOCK = 774201  # pg_advisory_xact_lock anahtarı: yönetici sayısı kontrolü


def _lock_admin_changes():
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", [ADMIN_LOCK])


def admin_user_ids():
    return set(User.objects.filter(
        is_active=True, role_assignments__revoked_at__isnull=True,
        role_assignments__role__status=RoleStatus.ACTIVE,
        role_assignments__role__permissions__action="system.admin").values_list("pk", flat=True))


def is_admin_account(user):
    return user.pk in admin_user_ids()


def _ensure_admin_remains():
    if not admin_user_ids():
        raise BusinessRuleError("Bu değişiklik sistemi etkin yöneticisiz bırakır; işlem yapılmadı.")


def _role_has_admin_only(role_or_perms):
    actions = role_or_perms.keys() if isinstance(role_or_perms, dict) else \
        role_or_perms.permissions.values_list("action", flat=True)
    return any(BY_CODE[a].admin_only for a in actions if a in BY_CODE)


def _check_manage_user(actor, target=None):
    if can(actor, "user.manage"):
        return "admin"
    check(actor, "hr.user.manage")
    if target is not None and is_admin_account(target):
        raise BusinessRuleError("Yönetici hesapları yalnız yönetici tarafından değiştirilebilir.")
    return "hr"


def _check_assignable(actor, role, mode):
    if role.status != RoleStatus.ACTIVE:
        raise BusinessRuleError(f"'{role.name}' rolü aktif değil; atanamaz.")
    if mode == "hr":
        check(actor, "hr.role.assign")
        if not role.hr_assignable or _role_has_admin_only(role):
            raise BusinessRuleError(f"'{role.name}' rolünü İK atayamaz (yönetici yetkisi gerekir).")


# --- Kullanıcılar -----------------------------------------------------------------------

@transaction.atomic
def create_user(actor, *, personnel_no, full_name, password, team="", shift=None, roles=(), reason=""):
    mode = _check_manage_user(actor)
    for role in roles:
        _check_assignable(actor, role, mode)
    user = User.objects.create_user(personnel_no=personnel_no, password=password, full_name=full_name,
                                    team=team, shift=shift)
    audit(actor, "user.create", user, {"team": (None, team), "shift": (None, shift)}, reason=reason,
          code=user.personnel_no)
    for role in roles:
        UserRole.objects.create(user=user, role=role, granted_by=actor, reason=reason or "Hesap açılışı")
        audit(actor, "user.role_grant", user, {"rol": (None, role.name)}, reason=reason, code=user.personnel_no)
    return user


@transaction.atomic
def update_user(actor, user, *, full_name, team, shift, is_active, new_password=None, machines=None, reason=""):
    _lock_admin_changes()
    user = User.objects.select_for_update().get(pk=user.pk)
    mode = _check_manage_user(actor, user)
    if user.pk == actor.pk and not is_active:
        raise BusinessRuleError("Kendi hesabınızı pasife alamazsınız.")
    if user.is_active and not is_active and not reason:
        raise BusinessRuleError("Hesap kapatma için gerekçe zorunludur.", field="reason")
    changes = diff(user, {"full_name": full_name, "team": team, "shift": shift, "is_active": is_active})
    for field, (_, new) in changes.items():
        setattr(user, field, new)
    if new_password:
        user.set_password(new_password)  # Oturum karması değişir; açık oturumlar sonlanır.
        changes["password"] = ("***", "değiştirildi")
    user.save()
    if machines is not None:
        old = set(user.machine_links.values_list("machine_id", flat=True))
        new = {m.pk for m in machines}
        if old != new:
            user.machine_links.exclude(machine_id__in=new).delete()
            for mid in new - old:
                UserMachine.objects.create(user=user, machine_id=mid)
            changes["makineler"] = (sorted(old), sorted(new))
    if "is_active" in changes:
        _ensure_admin_remains()
    if changes:
        deactivated = "is_active" in changes and changes["is_active"][1] is False
        audit(actor, "user.deactivate" if deactivated else "user.update", user, changes,
              reason=reason or ("İK" if mode == "hr" else ""), code=user.personnel_no)
    return user


@transaction.atomic
def grant_role(actor, user, role, reason=""):
    _lock_admin_changes()
    user = User.objects.select_for_update().get(pk=user.pk)
    mode = _check_manage_user(actor, user)
    if user.pk == actor.pk:
        raise BusinessRuleError("Kendi rol atamanızı değiştiremezsiniz.")
    _check_assignable(actor, role, mode)
    if UserRole.objects.filter(user=user, role=role, revoked_at__isnull=True).exists():
        raise BusinessRuleError("Bu rol zaten atanmış.")
    ur = UserRole.objects.create(user=user, role=role, granted_by=actor, reason=reason)
    audit(actor, "user.role_grant", user, {"rol": (None, role.name)}, reason=reason, code=user.personnel_no)
    return ur


@transaction.atomic
def revoke_role(actor, assignment, reason):
    _lock_admin_changes()
    ur = UserRole.objects.select_for_update(of=("self",)).select_related("user", "role").get(pk=assignment.pk)
    mode = _check_manage_user(actor, ur.user)
    if ur.user_id == actor.pk:
        raise BusinessRuleError("Kendi rol atamanızı değiştiremezsiniz.")
    if mode == "hr":
        _check_assignable(actor, ur.role, mode)
    if ur.revoked_at:
        raise BusinessRuleError("Bu atama zaten kaldırılmış.")
    if not reason:
        raise BusinessRuleError("Rol kaldırma için gerekçe zorunludur.", field="reason")
    ur.revoked_at, ur.revoked_by, ur.revoke_reason = timezone.now(), actor, reason
    ur.save()
    _ensure_admin_remains()
    audit(actor, "user.role_revoke", ur.user, {"rol": (ur.role.name, None)}, reason=reason, code=ur.user.personnel_no)
    return ur


@transaction.atomic
def change_own_password(user, new_password):
    user.set_password(new_password)
    user.save(update_fields=["password"])
    audit(user, "user.password_change", user, {"password": ("***", "değiştirildi")}, code=user.personnel_no)
    return user


# --- Roller ----------------------------------------------------------------------------

def _clean_perms(perms, allow_admin_only):
    clean = {}
    for action, scope in perms.items():
        spec = BY_CODE.get(action)
        if spec is None:
            raise BusinessRuleError(f"Desteklenmeyen yetki: {action}")
        if scope not in spec.scopes:
            raise BusinessRuleError(f"'{spec.label}' için '{Scope.LABELS.get(scope, scope)}' kapsamı kullanılamaz.")
        if spec.admin_only and not allow_admin_only:
            raise BusinessRuleError(f"'{spec.label}' yetkisini yalnız yönetici verebilir.")
        clean[action] = scope
    return clean


def _write_perms(role, perms):
    old = dict(role.permissions.values_list("action", "scope"))
    role.permissions.exclude(action__in=perms.keys()).delete()
    for action, scope in perms.items():
        RolePermission.objects.update_or_create(role=role, action=action, defaults={"scope": scope})
    return {a: (old.get(a), perms.get(a)) for a in set(old) | set(perms) if old.get(a) != perms.get(a)}


@transaction.atomic
def create_role(actor, *, code, name, description="", hr_assignable=False, perms):
    if can(actor, "role.manage"):
        status, approved = RoleStatus.ACTIVE, True
        perms = _clean_perms(perms, allow_admin_only=True)
    else:
        check(actor, "role.draft")
        status, approved, hr_assignable = RoleStatus.DRAFT, False, False
        perms = _clean_perms(perms, allow_admin_only=False)
    if AccessRole.objects.filter(code=code).exists():
        raise BusinessRuleError("Bu rol kodu kullanılıyor.", field="code")
    role = AccessRole.objects.create(code=code, name=name, description=description, status=status,
                                     hr_assignable=hr_assignable, created_by=actor,
                                     approved_by=actor if approved else None,
                                     approved_at=timezone.now() if approved else None)
    changes = _write_perms(role, perms)
    audit(actor, "role.create", role, {"durum": (None, status), **changes}, code=role.code)
    return role


@transaction.atomic
def update_role(actor, role, *, name, description, hr_assignable, perms, reason=""):
    _lock_admin_changes()
    role = AccessRole.objects.select_for_update().get(pk=role.pk)
    if can(actor, "role.manage"):
        perms = _clean_perms(perms, allow_admin_only=True)
    else:
        check(actor, "role.draft")
        if role.status != RoleStatus.DRAFT or role.created_by_id != actor.pk:
            raise BusinessRuleError("Yalnız kendi hazırladığınız taslak rolü değiştirebilirsiniz.")
        perms = _clean_perms(perms, allow_admin_only=False)
        hr_assignable = role.hr_assignable
    changes = diff(role, {"name": name, "description": description, "hr_assignable": hr_assignable})
    for f, (_, new) in changes.items():
        setattr(role, f, new)
    role.save()
    changes.update(_write_perms(role, perms))
    _ensure_admin_remains()
    if changes:
        audit(actor, "role.update", role, changes, reason=reason, code=role.code)
    return role


@transaction.atomic
def approve_role(actor, role):
    check(actor, "role.approve")
    role = AccessRole.objects.select_for_update().get(pk=role.pk)
    if role.status != RoleStatus.DRAFT:
        raise BusinessRuleError("Yalnız taslak rol onaylanabilir.")
    role.status, role.approved_by, role.approved_at = RoleStatus.ACTIVE, actor, timezone.now()
    role.save()
    audit(actor, "role.approve", role, {"durum": (RoleStatus.DRAFT, RoleStatus.ACTIVE)}, code=role.code)
    return role


@transaction.atomic
def set_role_status(actor, role, status, reason):
    check(actor, "role.manage")
    _lock_admin_changes()
    role = AccessRole.objects.select_for_update().get(pk=role.pk)
    if not reason:
        raise BusinessRuleError("Gerekçe zorunludur.", field="reason")
    old = role.status
    role.status = status
    role.save()
    _ensure_admin_remains()
    audit(actor, "role.status", role, {"durum": (old, status)}, reason=reason, code=role.code)
    return role


@transaction.atomic
def save_shift(actor, shift, data):
    from .models import Shift
    check(actor, "shift.manage")
    fields = {k: data[k] for k in ("code", "name", "start_time", "end_time", "is_active")}
    if shift is None:
        shift = Shift.objects.create(**fields)
        audit(actor, "shift.create", shift, {k: (None, v) for k, v in fields.items()}, code=shift.code)
        return shift
    shift = Shift.objects.select_for_update().get(pk=shift.pk)
    changes = diff(shift, fields)
    for f, (_, new) in changes.items():
        setattr(shift, f, new)
    shift.save()
    if changes:
        audit(actor, "shift.update", shift, changes, code=shift.code)
    return shift


# --- Başlangıç şablonu farkı ------------------------------------------------------------

def template_drift(role):
    """Şablon rolün kod şablonundan farkı: (eksik {işlem: kapsam}, fazla {işlem: kapsam}). Şablon değilse None."""
    from .catalog import TEMPLATES
    if not role.is_template or role.code not in TEMPLATES:
        return None
    template = TEMPLATES[role.code][2]
    current = dict(role.permissions.values_list("action", "scope"))
    missing = {a: s for a, s in template.items() if current.get(a) != s}
    extra = {a: s for a, s in current.items() if template.get(a) != s}
    return (missing, extra) if (missing or extra) else ({}, {})


@transaction.atomic
def apply_template(actor, role, reason):
    """Yöneticinin açık kararıyla şablon rolü güncel kod şablonuna eşitler (özelleştirmeler kaldırılır)."""
    from .catalog import TEMPLATES
    check(actor, "role.manage")
    if not reason:
        raise BusinessRuleError("Gerekçe zorunludur.", field="reason")
    if not role.is_template or role.code not in TEMPLATES:
        raise BusinessRuleError("Bu rol bir başlangıç şablonu değil.")
    return update_role(actor, role, name=role.name, description=role.description, hr_assignable=role.hr_assignable,
                       perms=dict(TEMPLATES[role.code][2]), reason=f"Şablona eşitlendi: {reason}")
