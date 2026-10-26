from decimal import Decimal


def produced_quantity_for_line(line):
    """Kalem için gerçekleşen üretim. M1: üretim miktarı kaydı henüz yok (bobin/palet M2'de)."""
    return {"quantity": Decimal("0"), "timestamps": []}
