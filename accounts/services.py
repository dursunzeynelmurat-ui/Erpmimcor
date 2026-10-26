from django.db import transaction

from core.services import audit, diff

from .models import User
from .permissions import check


@transaction.atomic
def create_user(actor, *, personnel_no, full_name, role, password):
    check(actor, "user.manage")
    user = User.objects.create_user(personnel_no=personnel_no, password=password, full_name=full_name, role=role)
    audit(actor, "user.create", user, {"role": (None, role)}, code=user.personnel_no)
    return user


@transaction.atomic
def update_user(actor, user, *, full_name, role, is_active, new_password=None):
    check(actor, "user.manage")
    user = User.objects.select_for_update().get(pk=user.pk)
    changes = diff(user, {"full_name": full_name, "role": role, "is_active": is_active})
    for field, (_, new) in changes.items():
        setattr(user, field, new)
    if new_password:
        user.set_password(new_password)
        changes["password"] = ("***", "değiştirildi")
    user.save()
    if changes:
        audit(actor, "user.update", user, changes, code=user.personnel_no)
    return user


@transaction.atomic
def change_own_password(user, new_password):
    user.set_password(new_password)
    user.save(update_fields=["password"])
    audit(user, "user.password_change", user, {"password": ("***", "değiştirildi")}, code=user.personnel_no)
    return user
