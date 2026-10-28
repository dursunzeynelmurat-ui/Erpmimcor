"""Kapsam kuralları: model + kapsam → Q. Tanımsız eşleşme None döner (o kapsam erişim vermez)."""
from django.db.models import Q
from django.utils import timezone

from .catalog import Scope

# Model → müşteri alanına giden yol ("" = modelin kendisi müşteri)
CUSTOMER_PATHS = {
    "catalog.customer": "",
    "catalog.product": "customer",
    "catalog.customercontact": "customer",
    "catalog.customeraddress": "customer",
    "catalog.customerdocument": "customer",
    "catalog.printartwork": "product__customer",
    "samples.sample": "customer",
    "catalog.customeraccess": "customer",
    "orders.salesorder": "customer",
    "orders.orderline": "order__customer",
    "production.workorder": "product__customer",
    "production.coil": "work_order__product__customer",
    "warehouse.pallet": "product__customer",
    "warehouse.reservation": "order_line__order__customer",
    "warehouse.shipment": "order__customer",
    "warehouse.shipmentline": "shipment__order__customer",
    "corrections.correctionrequest": "customer",
    "catalog.lotmovement": "work_order__product__customer",
    "warehouse.palletmovement": "pallet__product__customer",
    "warehouse.palletweighing": "pallet__product__customer",
}
# Model → iş emrine giden yol ("" = modelin kendisi iş emri)
WORK_ORDER_PATHS = {
    "production.workorder": "",
    "samples.sample": "work_order",
    "production.workorderoperation": "work_order",
    "production.coil": "work_order",
    "warehouse.pallet": "work_order",
    "corrections.correctionrequest": "work_order",
    "catalog.lotmovement": "work_order",
    "warehouse.palletmovement": "pallet__work_order",
    "warehouse.palletweighing": "pallet__work_order",
}
# Model → operasyona giden yol (makine/vardiya kapsamı için)
OPERATION_PATHS = {
    "production.workorder": "operations",
    "production.workorderoperation": "",
    "production.coil": "operation",
    "warehouse.pallet": "work_order__operations",
    "corrections.correctionrequest": "work_order__operations",
    "catalog.lotmovement": "work_order__operations",
    "warehouse.palletmovement": "pallet__work_order__operations",
    "warehouse.palletweighing": "pallet__work_order__operations",
}


def accessible_customer_ids(user):
    from catalog.models import CustomerAccess

    today = timezone.localdate()
    return (CustomerAccess.objects.filter(user=user, revoked_at__isnull=True, valid_from__lte=today)
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=today)).values("customer_id"))


def _join(prefix, field):
    return f"{prefix}__{field}" if prefix else field


def scope_q(model, scope, user):
    label = model._meta.label_lower
    if scope == Scope.CUSTOMER and label in CUSTOMER_PATHS:
        path = CUSTOMER_PATHS[label]
        return Q(**{_join(path, "pk__in") if not path else f"{path}__in": accessible_customer_ids(user)})
    if scope == Scope.ASSIGNED and label in WORK_ORDER_PATHS:
        return Q(**{_join(WORK_ORDER_PATHS[label], "assigned_foreman"): user})
    if scope == Scope.MACHINE and label in OPERATION_PATHS:
        from .models import UserMachine
        machines = UserMachine.objects.filter(user=user).values("machine_id")
        return Q(**{_join(OPERATION_PATHS[label], "machine__in"): machines})
    if scope == Scope.SHIFT and label in OPERATION_PATHS:
        if not user.shift_id:
            return None
        return Q(**{_join(OPERATION_PATHS[label], "responsible_shift_id"): user.shift_id})
    if scope == Scope.MACHINE_SHIFT and label in OPERATION_PATHS:
        # Tek kapsam içinde VE: aynı operasyon hem atanmış makinede hem kullanıcının vardiyasının sorumluluğunda
        # olmalı (tek filter() çağrısında aynı ilişki satırına uygulanır). Vardiyası tanımsız kullanıcıya erişim yok.
        if not user.shift_id:
            return None
        from .models import UserMachine
        path = OPERATION_PATHS[label]
        machines = UserMachine.objects.filter(user=user).values("machine_id")
        return Q(**{_join(path, "machine__in"): machines, _join(path, "responsible_shift_id"): user.shift_id})
    return None


def customer_in_scope(user, action, customer):
    """İşlem müşteri kapsamlıysa müşteriye erişim var mı? (ürün açma gibi yeni kayıt işlemleri için)"""
    from .permissions import scopes_for

    scopes = scopes_for(user, action)
    if Scope.ALL in scopes:
        return True
    if Scope.CUSTOMER in scopes:
        return accessible_customer_ids(user).filter(customer_id=customer.pk).exists()
    return False
