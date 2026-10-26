from django.shortcuts import render

from accounts.models import Role
from orders.models import LineStatus, OrderLine
from production.models import WorkOrder, WorkOrderStatus


def home(request):
    user = request.user
    ctx = {"role": user.role}
    if user.role in (Role.PLANNING, Role.ADMIN):
        ctx["waiting_planning"] = OrderLine.objects.filter(status=LineStatus.WAITING_PLANNING).count()
    if user.role in (Role.PRODUCTION, Role.ADMIN, Role.PLANNING):
        ctx["active_work_orders"] = (WorkOrder.objects.filter(status__in=[WorkOrderStatus.PLANNED, WorkOrderStatus.STARTED])
                                     .select_related("product").order_by("planned_start", "created_at")[:10])
    return render(request, "core/home.html", ctx)
