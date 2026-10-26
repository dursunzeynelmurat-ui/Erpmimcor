from accounts.permissions import scoped
from catalog.models import Unit
from core.models import QualityStatus

from .models import Pallet, PalletStatus, Reservation


def order_warehouse_context(order, lines, user=None):
    """Sipariş sayfası için: rezervasyonlar, sevkiyatlar ve kalem ürünüyle aynı koddaki serbest paletler."""
    stock, non_box = [], []
    for line in lines:
        if line.unit != Unit.BOX:
            non_box.append(line)
            continue
        base = scoped(Pallet.objects.all(), user, "stock.view") if user else Pallet.objects.none()
        pallets = [p for p in base.filter(product=line.product, status=PalletStatus.IN_STOCK)
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


def stock_breakdown(user):
    """Satış/planlama stok özeti (kutu, ürün bazında). Kategoriler ayrıktır ve toplamları fizikseli verir:
    fiziksel = kontrol bekleyen + blokeli + bobin bekletmeli + uygun; uygun = ayrılmış + sevke uygun serbest.
    Bobin bekletmesi ihtiyatlıdır: aktarımla ilişkili paletler de bekletilir (ayrıntı palet sayfasında)."""
    from accounts.permissions import scoped
    from core.models import QualityStatus

    from .models import Pallet, PalletStatus
    from .services import _reserved, coil_holds

    rows = {}
    pallets = (scoped(Pallet.objects.filter(status=PalletStatus.IN_STOCK, box_count__gt=0), user, "stock.view")
               .select_related("product__customer").order_by("product__code", "code"))
    for p in pallets:
        r = rows.setdefault(p.product, {"product": p.product, "physical": 0, "pending": 0, "blocked": 0, "held": 0,
                                        "reserved": 0, "shippable": 0, "pallets": 0})
        r["physical"] += p.box_count
        r["pallets"] += 1
        if p.quality_status == QualityStatus.BLOCKED:
            r["blocked"] += p.box_count
        elif coil_holds(p):
            r["held"] += p.box_count
        elif p.quality_status == QualityStatus.PENDING:
            r["pending"] += p.box_count
        else:
            reserved = _reserved(p)
            r["reserved"] += reserved
            r["shippable"] += p.box_count - reserved
    return list(rows.values())
