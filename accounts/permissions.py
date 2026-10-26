"""Rol → eylem yetki matrisi. Hem view'larda hem servislerde kullanılır (sunucu tarafı)."""
from functools import wraps

from django.core.exceptions import PermissionDenied

from .models import Role

A, S, P, U, D = Role.ADMIN, Role.SALES, Role.PLANNING, Role.PRODUCTION, Role.WAREHOUSE

MATRIX = {
    "user.manage": {A},
    "machine.manage": {A, P},
    "customer.manage": {A, S},
    "product.manage": {A, S, P},
    "recipe.manage": {A, P},
    "material.manage": {A, P},
    "lot.receive": {A, D},
    "lot.adjust": {A, D},
    "order.create": {A, S},
    "planning.approve": {A, P},
    "workorder.create": {A, P},
    "production.execute": {A, U},
    "quality.decide": {A, U},
    "quality.block": {A, U, D},
    "warehouse.operate": {A, D},
    "reservation.manage": {A, S, D},
    "shipment.manage": {A, D},
    # Görüntüleme: tüm roller
    "view.catalog": {A, S, P, U, D},
    "view.orders": {A, S, P, U, D},
    "view.production": {A, S, P, U, D},
    "view.warehouse": {A, S, P, U, D},
}


def can(user, action):
    if action not in MATRIX:
        raise KeyError(f"Tanımsız yetki: {action}")
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    return user.role in MATRIX[action]


def check(user, action):
    if not can(user, action):
        raise PermissionDenied("Bu işlem için yetkiniz yok.")


def require_perm(action):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            check(request.user, action)
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
