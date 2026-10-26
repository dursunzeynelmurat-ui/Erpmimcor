from catalog.models import Unit
from core.models import QualityStatus

from .models import Pallet, PalletStatus, Reservation


def order_warehouse_context(order, lines):
    """Sipariş sayfası için: rezervasyonlar, sevkiyatlar ve kalem ürünüyle aynı koddaki serbest paletler."""
    stock, non_box = [], []
    for line in lines:
        if line.unit != Unit.BOX:
            non_box.append(line)
            continue
        pallets = [p for p in Pallet.objects.filter(product=line.product, status=PalletStatus.IN_STOCK)
                   .exclude(quality_status=QualityStatus.BLOCKED).select_related("work_order")
                   if p.available_boxes > 0]
        if pallets:
            stock.append((line, pallets))
    return {
        "reservations": Reservation.objects.filter(order_line__order=order).select_related("pallet", "order_line__order"),
        "shipments": order.shipments.prefetch_related("lines"),
        "stock_options": stock,
        "non_box_lines": non_box,
    }
