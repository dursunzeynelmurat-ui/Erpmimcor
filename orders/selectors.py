"""Okuma amaçlı sorgular: satış ilerleme görünümü."""
from decimal import Decimal


def line_progress(line):
    """Kalem için gerçekleşen miktar, aşama ve son güncelleme zamanı."""
    from production.selectors import produced_quantity_for_line
    from production.models import WorkOrderStatus

    work_orders = list(line.work_orders.prefetch_related("operations__machine").order_by("created_at"))
    from catalog.models import Unit
    from warehouse.models import ReservationStatus
    from django.db.models import F, Sum

    produced = produced_quantity_for_line(line)
    reservations = line.reservations.all()
    reserved = reservations.filter(status=ReservationStatus.ACTIVE).aggregate(
        total=Sum(F("quantity") - F("shipped_quantity")))["total"] or 0
    timestamps = [line.updated_at] + [r.updated_at for r in reservations]
    for wo in work_orders:
        timestamps.append(wo.updated_at)
        timestamps.extend(op.updated_at for op in wo.operations.all())
    timestamps.extend(produced["timestamps"])
    active = [wo for wo in work_orders if wo.status == WorkOrderStatus.STARTED] or work_orders[-1:]
    if active:
        stage = f"{active[0].code}: {active[0].stage_display}"
    else:
        stage = line.get_status_display()
    return {
        "line": line,
        "reserved": reserved,
        "production_supported": line.unit in (Unit.BOX, Unit.KG),
        "shipping_supported": line.unit == Unit.BOX,
        "waiting_reason": "Teknik bilgiler ve reçete onayı bekleniyor." if line.status == "planlama_bekliyor" else (
            "Teknik onay verildi; henüz iş emri oluşturulmadı." if line.status == "onaylandi" and not work_orders else ""),
        "produced": produced["quantity"],
        "produced_unit": line.get_unit_display(),
        "shipped": produced.get("shipped", Decimal("0")),
        "ratio": int(min(produced["quantity"] / line.quantity * 100, 100))
        if line.quantity and produced["quantity"] is not None else 0,
        "stage": stage,
        "last_update": max(t for t in timestamps if t),
        "work_orders": work_orders,
    }
