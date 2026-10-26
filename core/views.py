from django.shortcuts import render

from accounts.models import Role
from orders.models import LineStatus, OrderLine, SalesOrder
from warehouse.models import Pallet, PalletStatus, Shipment, ShipmentStatus, WeightStatus
from production.models import WorkOrder, WorkOrderStatus


def home(request):
    user = request.user
    ctx = {"role": user.role}
    if user.role in (Role.PLANNING, Role.ADMIN):
        ctx["waiting_planning"] = OrderLine.objects.filter(status=LineStatus.WAITING_PLANNING).count()
    if user.role in (Role.PRODUCTION, Role.ADMIN, Role.PLANNING):
        ctx["active_work_orders"] = (WorkOrder.objects.filter(status__in=[WorkOrderStatus.PLANNED, WorkOrderStatus.STARTED])
                                     .select_related("product").order_by("planned_start", "created_at")[:10])
    if user.role in (Role.SALES, Role.ADMIN):
        ctx["recent_orders"] = SalesOrder.objects.filter(status="acik").select_related("customer")[:6]
    if user.role in (Role.PLANNING, Role.ADMIN):
        ctx["planning_lines"] = OrderLine.objects.filter(status=LineStatus.WAITING_PLANNING).select_related("order__customer", "product")[:6]
    if user.role in (Role.WAREHOUSE, Role.ADMIN):
        ctx["acceptance_pallets"] = Pallet.objects.filter(status=PalletStatus.AWAITING_ACCEPTANCE).select_related("product__customer")[:6]
        ctx["reweigh_pallets"] = Pallet.objects.filter(status=PalletStatus.IN_STOCK, weight_status=WeightStatus.REWEIGH).select_related("product")[:6]
        ctx["draft_shipments"] = Shipment.objects.filter(status=ShipmentStatus.DRAFT).select_related("order__customer")[:6]
    return render(request, "core/home.html", ctx)
