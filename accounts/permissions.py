"""Sunucu tarafı yetki ve kapsam kontrolü.

Kurallar:
- Kullanıcının yetkileri, *aktif* rol atamalarındaki *aktif* rollerin yetkilerinin birleşimidir.
- Bir işlemin kapsamı, o işlemi veren rollerin kapsamlarının birleşimidir. `all` yalnız bir rol bu işlemi açıkça
  `all` kapsamıyla veriyorsa geçerlidir; kapsamlar asla kendiliğinden genişlemez.
- Bir kapsam kuralının tanımlı olmadığı modelde o kapsam hiçbir kayda erişim vermez (varsayılan kapalı).
- `is_superuser` uygulama yetkisi vermez; yetki yalnız rollerden gelir (rol düşürme her zaman etkilidir).
- Yetkiler her istekte veritabanından okunur (istek içi önbellek); kaldırılan yetki açık oturumda da hemen geçerlidir.
"""
import threading
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import Http404

from .catalog import BY_CODE, Scope

_request_cache = threading.local()


def start_request_cache():
    _request_cache.maps = {}


def end_request_cache():
    _request_cache.maps = None


def permission_map(user):
    """{işlem: {kapsam, ...}}"""
    from .models import RolePermission, RoleStatus

    maps = getattr(_request_cache, "maps", None)
    if maps is not None and user.pk in maps:
        return maps[user.pk]
    result = {}
    rows = RolePermission.objects.filter(
        role__status=RoleStatus.ACTIVE, role__assignments__user_id=user.pk,
        role__assignments__revoked_at__isnull=True).values_list("action", "scope")
    for action, scope in rows:
        result.setdefault(action, set()).add(scope)
    if maps is not None:
        maps[user.pk] = result
    return result


def _active(user):
    return bool(user and user.is_authenticated and user.is_active)


def scopes_for(user, action):
    if action not in BY_CODE:
        raise KeyError(f"Tanımsız yetki: {action}")
    if not _active(user):
        return set()
    return permission_map(user).get(action, set())


def can(user, action, obj=None):
    scopes = scopes_for(user, action)
    if not scopes:
        return False
    if obj is None or Scope.ALL in scopes:
        return True
    return scoped(type(obj)._default_manager.filter(pk=obj.pk), user, action).exists()


def check(user, action, obj=None):
    if not can(user, action, obj):
        raise PermissionDenied("Bu işlem için yetkiniz yok.")


def scoped(qs, user, action):
    """Kullanıcının bu işlem için erişebildiği kayıtlar."""
    from .scope import scope_q

    scopes = scopes_for(user, action)
    if not scopes:
        return qs.none()
    if Scope.ALL in scopes:
        return qs
    q = None
    for scope in scopes:
        part = scope_q(qs.model, scope, user)
        if part is not None:
            q = part if q is None else (q | part)
    if q is None:
        return qs.none()
    return qs.filter(pk__in=qs.model._default_manager.filter(q).values("pk"))


def get_scoped_or_404(qs, user, action, **lookup):
    """Kapsam dışı kaydın varlığı da açığa çıkmasın diye 404 döner."""
    check(user, action)
    try:
        return scoped(qs, user, action).get(**lookup)
    except qs.model.DoesNotExist:
        raise Http404


def require_perm(action):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            check(request.user, action)
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def require_any(*actions):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not any(can(request.user, a) for a in actions):
                raise PermissionDenied("Bu işlem için yetkiniz yok.")
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def is_admin(user):
    return can(user, "system.admin")
