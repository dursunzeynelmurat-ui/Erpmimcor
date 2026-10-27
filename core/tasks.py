"""Uygulama içi iş listeleri (e-posta/WhatsApp yok). Her liste yetki + kapsamdan geçer; yalnız sayı ve ilk kayıtlar."""
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from accounts.permissions import can, scoped

LIMIT = 5


def wh_missing(shipment):
    from warehouse.services import load_check
    return bool(load_check(shipment)["missing"])


def _task(title, qs, url, label):
    items = list(qs[:LIMIT])
    return {"title": title, "count": qs.count(), "url": url, "items": [(label(o), o) for o in items]} if items else None


def task_lists(user):
    from corrections.models import CorrectionRequest, CorrectionStatus
    from orders.models import LineStatus, OrderLine, OrderStatus, SalesOrder
    from production.models import ShiftHandover, WorkOrder, WorkOrderOperation, WorkOrderStatus
    from warehouse.models import Pallet, PalletStatus, Shipment, ShipmentStatus
    from core.models import QualityStatus

    today = timezone.localdate()
    tasks = []
    if can(user, "correction.approve"):
        qs = scoped(CorrectionRequest.objects.filter(status=CorrectionStatus.REQUESTED), user, "correction.approve") \
            .exclude(requested_by=user).order_by("requested_at")
        tasks.append(_task("Onay bekleyen düzeltme talepleri", qs, reverse("corrections:correction_list"),
                           lambda c: f"{c.code} · {c.get_kind_display()} · {c.target_code}"))
    if can(user, "customer.access.manage"):
        from catalog.models import CustomerAccess
        qs = (CustomerAccess.objects.filter(needs_review=True, revoked_at__isnull=True)
              .select_related("user", "customer").order_by("user__personnel_no"))
        tasks.append(_task("İncelenmemiş müşteri erişimleri (toplu taşınan / belirsiz)", qs,
                           reverse("catalog:access_review"),
                           lambda a: f"{a.user.full_name} → {a.customer.code} · {a.get_source_display()}"))
    if can(user, "order.approve"):
        qs = scoped(SalesOrder.objects.filter(status=OrderStatus.DRAFT), user, "order.view").select_related("customer")
        tasks.append(_task("Onay bekleyen taslak siparişler", qs, reverse("orders:order_list") + "?durum=taslak",
                           lambda o: f"{o.code} · {o.customer.name}"))
    if can(user, "order.view"):
        qs = (scoped(OrderLine.objects.filter(order__status__in=[OrderStatus.OPEN, OrderStatus.PARTIAL]), user, "order.view")
              .exclude(status__in=[LineStatus.SHIPPED, LineStatus.CANCELLED])
              .filter(Q(due_date__lt=today) | Q(due_date__isnull=True, order__due_date__lt=today))
              .select_related("order__customer", "product").order_by("due_date", "order__due_date"))
        tasks.append(_task("Termini geçmiş kalemler", qs, reverse("orders:progress_list"),
                           lambda ln: f"{ln.code} · {ln.product.code} · {ln.order.customer.name}"))
    if can(user, "correction.approve") or can(user, "planning.approve"):
        from production.services import closure_status
        closed = scoped(WorkOrder.objects.filter(status=WorkOrderStatus.CLOSED, corrections__status="uygulandi")
                        .distinct(), user, "workorder.view").select_related("closure")
        pending = [w for w in closed if (closure_status(w) or {}).get("needs_review")]
        if pending:
            tasks.append({"title": "Yeniden değerlendirme bekleyen kapanışlar", "count": len(pending),
                          "url": reverse("production:workorder_detail", args=[pending[0].pk]) + "#kapanis",
                          "items": [(f"{w.code} · güncel fark {closure_status(w)['current']['difference']} kg", w)
                                    for w in pending[:LIMIT]]})
    if can(user, "workorder.release"):
        qs = scoped(WorkOrder.objects.filter(status=WorkOrderStatus.DRAFT, archived_at__isnull=True), user,
                    "workorder.view").select_related("product")
        tasks.append(_task("Serbest bırakılmayı bekleyen iş emirleri", qs,
                           reverse("production:workorder_list") + "?durum=taslak", lambda w: f"{w.code} · {w.product.code}"))
    if can(user, "production.hold"):
        ops = scoped(WorkOrderOperation.objects.all(), user, "production.hold").values("pk")
        qs = (ShiftHandover.objects.filter(acknowledged_at__isnull=True, operation__in=ops).exclude(created_by=user)
              .select_related("operation__work_order", "to_shift"))
        tasks.append(_task("Teslim alınmamış vardiya devirleri", qs, reverse("production:floor_plan"),
                           lambda h: f"{h.operation.code} → {h.to_shift} · {h.remaining_note}"))
    if can(user, "quality.decide") or can(user, "quality.release"):
        qs = (scoped(Pallet.objects.filter(status=PalletStatus.IN_STOCK, quality_status=QualityStatus.PENDING), user,
                     "stock.view").select_related("product").order_by("created_at"))
        tasks.append(_task("Kalite kontrolü bekleyen paletler (iadeler dahil)", qs,
                           reverse("warehouse:pallet_list") + "?quality=kontrol_bekliyor",
                           lambda p: f"{p.code} · {p.product.code} · {p.box_count} kutu"))
    if can(user, "shipment.manage"):
        qs = scoped(Shipment.objects.filter(status=ShipmentStatus.PREPARED), user, "shipment.view").select_related(
            "order__customer")
        tasks.append(_task("Yükleme kontrolü bekleyen sevkiyatlar", qs, reverse("warehouse:shipment_list"),
                           lambda s: f"{s.code} · {s.order.customer.name}"))
    if can(user, "shipment.approve"):
        qs = scoped(Shipment.objects.filter(status=ShipmentStatus.PREPARED), user, "shipment.view").select_related(
            "order__customer")
        qs = qs.filter(pk__in=[s.pk for s in qs if not wh_missing(s)])
        tasks.append(_task("Son sevk onayı bekleyen sevkiyatlar (yükleme kontrolü tamam)", qs,
                           reverse("warehouse:shipment_list"), lambda s: f"{s.code} · {s.order.customer.name}"))
    if can(user, "shipment.exit"):
        qs = scoped(Shipment.objects.filter(status=ShipmentStatus.SHIPPED), user, "shipment.view").select_related(
            "order__customer")
        tasks.append(_task("Araç çıkışı bekleyen sevkiyatlar", qs, reverse("warehouse:shipment_list"),
                           lambda s: f"{s.code} · {s.order.customer.name}"))
    if can(user, "stocklevel.manage"):
        from warehouse.services import planning_needs
        needs = planning_needs(user)
        if needs:
            tasks.append({"title": "Alt stok seviyesinin altındaki ürünler", "count": len(needs),
                          "url": reverse("warehouse:stock_needs"),
                          "items": [(f"{n['level'].product.code}: serbest {n['available']}, alt {n['level'].min_boxes}, "
                                     f"üretim ihtiyacı {n['need']} kutu", n) for n in needs[:LIMIT]]})
    return [t for t in tasks if t]
