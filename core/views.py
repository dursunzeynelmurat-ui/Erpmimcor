from django.shortcuts import render

from accounts.permissions import can, scoped
from orders.models import LineStatus, OrderLine, SalesOrder
from production.models import WorkOrder, WorkOrderStatus
from warehouse.models import Pallet, PalletStatus, Shipment, ShipmentStatus, WeightStatus


def home(request):
    """Rol adına değil yetki ve kapsama göre iş listeleri; her liste kullanıcının kapsamından geçer."""
    u = request.user
    ctx = {"active_work_orders": None, "recent_orders": None}
    if can(u, "planning.approve"):
        lines = OrderLine.objects.filter(status=LineStatus.WAITING_PLANNING)
        ctx["waiting_planning"] = lines.count()
        ctx["planning_lines"] = lines.select_related("order__customer", "product")[:6]
    if can(u, "workorder.view") and (can(u, "production.execute") or can(u, "workorder.create")):
        ctx["active_work_orders"] = (scoped(WorkOrder.objects.filter(status__in=[WorkOrderStatus.PLANNED, WorkOrderStatus.STARTED]),
                                            u, "workorder.view")
                                     .select_related("product").order_by("planned_start", "created_at")[:10])
    if can(u, "order.create"):
        ctx["recent_orders"] = scoped(SalesOrder.objects.filter(status="acik"), u, "order.view").select_related("customer")[:6]
    if can(u, "warehouse.operate"):
        ctx["acceptance_pallets"] = Pallet.objects.filter(status=PalletStatus.AWAITING_ACCEPTANCE).select_related("product__customer")[:6]
        ctx["reweigh_pallets"] = Pallet.objects.filter(status=PalletStatus.IN_STOCK, weight_status=WeightStatus.REWEIGH).select_related("product")[:6]
        ctx["draft_shipments"] = Shipment.objects.filter(status=ShipmentStatus.DRAFT).select_related("order__customer")[:6]
    return render(request, "core/home.html", ctx)
