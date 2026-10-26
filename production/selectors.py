from decimal import Decimal

from django.db.models import Sum


def produced_quantity_for_line(line):
    """Kalem için gerçekleşen üretim ve sevk.

    Kutu birimli kalemde: bu kaleme bağlı iş emirlerinden palete alınan kutular.
    kg birimli kalemde: son operasyonda kaydedilen, blokeli olmayan bobin ağırlıkları.
    adet/rulo birimli kalemde: ölçüm olmadığından None (tahmin yapılmaz).
    Sevk edilen: bu kaleme yapılan onaylı sevkiyatlar (stok paletlerinden ayrılanlar dahil).
    """
    from catalog.models import Unit
    from core.models import QualityStatus
    from warehouse.models import SHIPPED_STATES, MovementKind, PalletMovement, ShipmentLine

    from .models import Coil

    stamps = []
    if line.unit == Unit.KG:
        coils = Coil.objects.filter(work_order__order_line=line).exclude(quality_status=QualityStatus.BLOCKED)
        coils = [c for c in coils.select_related("operation") if c.operation.seq == c.work_order.operations.count()]
        qty = sum((c.weight_kg for c in coils), Decimal("0"))
        stamps += [c.created_at for c in coils]
    elif line.unit == Unit.BOX:
        mvs = PalletMovement.objects.filter(kind__in=[MovementKind.PRODUCTION, MovementKind.CORRECTION],
                                            work_order__order_line=line)
        qty = Decimal(mvs.aggregate(s=Sum("quantity"))["s"] or 0)
        last = mvs.order_by("-at").first()
        if last:
            stamps.append(last.at)
    else:
        # adet/rulo: ölçüm kaydı yok; kutu hareketi bu birime çevrilmez (bulgu 11).
        qty = None
    shipped_lines = ShipmentLine.objects.filter(order_line=line, shipment__status__in=SHIPPED_STATES)
    shipped = Decimal(shipped_lines.aggregate(s=Sum("quantity"))["s"] or 0)
    last_ship = shipped_lines.order_by("-shipment__shipped_at").first()
    if last_ship:
        stamps.append(last_ship.shipment.shipped_at)
    return {"quantity": qty, "shipped": shipped, "timestamps": stamps}
