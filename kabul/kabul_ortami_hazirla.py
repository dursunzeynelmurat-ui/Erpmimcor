"""Manuel kabul ortamı hazırlığı (kabul sürümü 63a5105). Uygulama kodunu değiştirmez; yalnız servisler üzerinden,
işlem geçmişine yazılarak kurgusal test hesapları ve kayıtlar açar.

Güvenlik:
- Yalnız adı "kabul" içeren veritabanında çalışır; `mimcor` ve `mimcor_uat` adlarında reddeder.
- Parola KABUL_TEST_PASSWORD ortam değişkeninden (.env, git dışı) okunur; ekrana, loga veya dosyaya yazılmaz.
- Var olan (UAT) kullanıcıların parolası, rolü, erişimi değiştirilmez. Tek ortak değişiklik: depo rolünden
  `shipment.approve` çıkarılır (rol ortak olduğundan o roldeki herkesi etkiler). Şablon toplu uygulanmaz.

Kullanım (kabul çalışma dizininde, .env orada):
  cd <kabul-calisma-dizini> && python <bu-dosya> --cikti kayitlar.json
  python <bu-dosya> --parola-yenile      # yalnız 8xxx test hesaplarının parolasını .env'deki değere eşitler
"""
import argparse
import datetime
import json
import os
import sys
from decimal import Decimal as D

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mimcor.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.db import transaction  # noqa: E402
from django.utils import timezone  # noqa: E402

TEST_USERS = [  # (personel no, ad, rol, vardiya, makineler)
    ("8101", "Kabul Yönetici", "yonetici", None, []),
    ("8201", "Kabul Satış A", "satis", None, []),
    ("8202", "Kabul Satış B", "satis", None, []),
    ("8301", "Kabul Planlama", "planlama", None, []),
    ("8401", "Kabul Ustabaşı", "ustabasi", "V1", []),
    ("8402", "Kabul Üretim Yetkilisi", "uretim_yetkilisi", "V1", []),
    ("8403", "Kabul Operatör V1", "operator", "V1", ["KBL-EXT1"]),
    ("8404", "Kabul Operatör V2", "operator", "V2", ["KBL-EXT1"]),
    ("8501", "Kabul Depo", "depo", None, []),
    ("8502", "Kabul Ambar Yetkilisi", "ambar_yetkilisi", None, []),
]
SHIPMENT_RULE = {  # kabul kuralı: sevk/çıkış yetkileri
    "depo": {"shipment.view", "shipment.manage"},
    "ambar_yetkilisi": {"shipment.view", "shipment.manage", "shipment.approve", "shipment.undo", "shipment.exit"},
    "yonetici": {"shipment.view", "shipment.manage", "shipment.approve", "shipment.undo", "shipment.exit"},
}
SENSITIVE = {"shipment.manage", "shipment.approve", "shipment.undo", "shipment.exit"}


def guard():
    db = settings.DATABASES["default"]["NAME"]
    if "kabul" not in db or db in ("mimcor", "mimcor_uat"):
        sys.exit(f"Güvenlik: yalnız kabul kopyasında çalışır (şu an: {db}).")
    if not os.environ.get("KABUL_TEST_PASSWORD"):
        sys.exit("KABUL_TEST_PASSWORD tanımlı değil (kabul çalışma dizinindeki .env).")
    return db


def U(no):
    from accounts.models import User
    return User.objects.get(personnel_no=no)


def ensure_users(password):
    from accounts import services as acc
    from accounts.models import AccessRole, Shift, User
    from catalog.models import Machine, MachineType
    from catalog import services as cat
    if not User.objects.filter(personnel_no="8101").exists():
        # Önyükleme: ilk test yöneticisi (createsuperuser ile aynı yol, uygulama yetkisi rolden gelir)
        User.objects.create_user("8101", password, full_name="Kabul Yönetici", role="yonetici")
    admin = U("8101")
    for code, name in (("KBL-EXT1", "Kabul extruder 1"), ("KBL-EXT2", "Kabul extruder 2")):
        if not Machine.objects.filter(code=code).exists():
            cat.create_definition(admin, Machine, {"code": code, "name": name, "machine_type": MachineType.EXTRUDER})
    for no, name, role, shift, machines in TEST_USERS:
        sh = Shift.objects.get(code=shift) if shift else None
        u = User.objects.filter(personnel_no=no).first()
        if u is None:
            u = acc.create_user(admin, personnel_no=no, full_name=name, password=password, shift=sh,
                                roles=[AccessRole.objects.get(code=role)], reason="Kabul ortamı test hesabı")
        if machines or sh:
            acc.update_user(admin, u, full_name=u.full_name, team=u.team, shift=sh, is_active=True,
                            machines=list(Machine.objects.filter(code__in=machines)) if machines else None,
                            reason="Kabul ortamı: makine/vardiya ataması")
    return admin


def fix_depot_role(admin):
    from accounts import services as acc
    from accounts.models import AccessRole
    role = AccessRole.objects.get(code="depo")
    perms = dict(role.permissions.values_list("action", "scope"))
    if "shipment.approve" in perms:
        perms.pop("shipment.approve")
        acc.update_role(admin, role, name=role.name, description=role.description, hr_assignable=role.hr_assignable,
                        perms=perms, reason="Kabul ortamı: son sevk onayı yalnız ambar yetkilisi ve yöneticide")
        return "depo rolünden shipment.approve kaldırıldı"
    return "depo rolünde shipment.approve zaten yok"


def shipment_table():
    from accounts.models import AccessRole
    rows = []
    for role in AccessRole.objects.order_by("code"):
        have = set(role.permissions.filter(action__startswith="shipment.").values_list("action", flat=True))
        expected = SHIPMENT_RULE.get(role.code)
        if expected is None:
            ok = not (have & SENSITIVE)
            exp = "yalnız görüntüleme veya hiç"
        else:
            ok = have == expected
            exp = ", ".join(sorted(expected))
        rows.append({"rol": role.code, "mevcut": sorted(have), "kural": exp, "uygun": ok})
    return rows


def seed_data(admin):
    from catalog import customers as cs
    from catalog import services as cat
    from catalog.models import Customer, Machine, Material, MaterialKind, Product, Unit
    from core.models import QualityStatus
    from core.tests.factories import complete_card, load
    from orders import services as orders
    from production import instructions, lifecycle
    from production import services as prod
    from warehouse import services as wh
    from accounts.models import Shift
    today = timezone.localdate()
    planner, foreman, depot, store = U("8301"), U("8401"), U("8501"), U("8502")
    s_a, s_b = U("8201"), U("8202")
    customers = {}
    for i, (code, name) in enumerate((("KBL-YETKILI", "Kabul Kurgu Yetkili A.Ş."), ("KBL-SURELI", "Kabul Kurgu Süreli Ltd."),
                                      ("KBL-YETKISIZ", "Kabul Kurgu Yetkisiz San."))):
        c = cat.create_definition(admin, Customer, {"code": code, "name": name})
        complete_card(admin, c, f"99977700{i}{i}")
        customers[code] = c
    cs.grant_access(admin, customers["KBL-YETKILI"], s_a, today, None, "Kabul: kalıcı erişim (kurgusal)")
    cs.grant_access(admin, customers["KBL-SURELI"], s_a, today, today + datetime.timedelta(days=7),
                    "Kabul: süreli erişim / vekâlet (kurgusal)")
    cs.grant_access(admin, customers["KBL-YETKISIZ"], s_b, today, None, "Kabul: yalnız Satış B (kurgusal)")
    mats = {code: cat.create_definition(admin, Material, {"code": code, "name": name, "kind": kind, "unit": "kg"})
            for code, name, kind in (("KBL-LDPE", "Kabul LDPE (kurgusal)", MaterialKind.RAW),
                                     ("KBL-SLIP", "Kabul kaydırıcı (kurgusal)", MaterialKind.ADDITIVE))}
    lot = cat.receive_lot(store, mats["KBL-LDPE"], D("1000"), supplier_lot="KURGU-IRS-0001")
    cat.receive_lot(store, mats["KBL-SLIP"], D("50"), supplier_lot="KURGU-IRS-0002")
    spec = {"width_mm": D("640"), "thickness_micron": D("35"), "length_m": None, "color": "Şeffaf (kurgusal)",
            "print_spec": "", "perforation_spec": "", "process_notes": "Kabul proses notu (kurgusal)"}
    mix = [(mats["KBL-LDPE"], D("97")), (mats["KBL-SLIP"], D("3"))]
    products = {}
    for code, cust in (("KBL-FILM-A", "KBL-YETKILI"), ("KBL-FILM-S", "KBL-SURELI"), ("KBL-FILM-B", "KBL-YETKISIZ")):
        products[code] = cat.create_definition(admin, Product, {"code": code, "customer": customers[cust],
                                                                 "name": f"Kabul film {code[-1]}", "unit": Unit.BOX})
        cat.create_revision(planner, products[code], spec, mix)

    def order(sales, product, qty, ref):
        o = orders.create_order(sales, customer=product.customer, order_date=today, customer_ref=ref,
                                lines=[{"product": product, "quantity": D(qty), "unit": Unit.BOX}])
        o = orders.approve_order(sales, o)
        line = o.lines.get()
        orders.approve_line(planner, line, line.product.current_revision)
        return o
    o_ship = order(s_a, products["KBL-FILM-A"], "10", "KBL-PO-SEVK (kurgusal)")
    o_prod = order(s_a, products["KBL-FILM-A"], "20", "KBL-PO-URETIM (kurgusal)")
    o_sureli = order(s_a, products["KBL-FILM-S"], "5", "KBL-PO-SURELI (kurgusal)")
    o_yetkisiz = order(s_b, products["KBL-FILM-B"], "5", "KBL-PO-YETKISIZ (kurgusal)")
    v1 = Shift.objects.get(code="V1")
    ext1, ext2 = Machine.objects.get(code="KBL-EXT1"), Machine.objects.get(code="KBL-EXT2")

    # İE-2: kapalı iş emri (düzeltme senaryosu) → 40 kutu palet → sevkiyat
    wo2 = prod.create_work_order(planner, order_line=o_ship.lines.get(), machines=[ext2], target_quantity=D("40"),
                                 unit=Unit.BOX, foreman=foreman)
    lifecycle.release(planner, wo2)
    op2 = wo2.operations.get()
    lifecycle.schedule_operation(foreman, op2, planned_date=today, shift=v1, machine=ext2, queue_position=1)
    prod.start_work_order(foreman, wo2)
    wo2.refresh_from_db()
    issue = prod.issue_material(foreman, wo2, lot, D("100"))
    coil = prod.record_coil(foreman, wo2.operations.get(), weight_kg=D("95"))
    pallet = wh.create_pallet_from_production(foreman, wo2, 40, coils=[coil])
    prod.complete_operation(foreman, wo2.operations.get())
    prod.close_work_order(foreman, wo2, scrap_kg=D("5"), remaining_confirmed=True)
    wh.accept_pallet(depot, pallet, {"gross_kg": D("120"), "location": "KBL-A-01"})
    wh.set_pallet_quality(foreman, pallet, QualityStatus.OK, "Kabul: görsel kontrol uygun (kurgusal)")
    res = wh.reserve(depot, pallet, o_ship.lines.get(), 10)
    shipment = wh.create_shipment(depot, o_ship)
    wh.add_shipment_line(depot, shipment, res, 10)
    load(depot, shipment)

    # İE-1: KBL-EXT1 / bugün / V1, başlamış, talimat basılmış; devir yapılmamış (vardiya devri senaryosu)
    wo1 = prod.create_work_order(planner, order_line=o_prod.lines.get(), machines=[ext1], target_quantity=D("20"),
                                 unit=Unit.BOX, foreman=foreman)
    lifecycle.release(planner, wo1)
    op1 = wo1.operations.get()
    lifecycle.schedule_operation(foreman, op1, planned_date=today, shift=v1, machine=ext1, queue_position=1)
    prod.start_work_order(foreman, wo1)
    op1.refresh_from_db()
    ins = instructions.issue_for_print(foreman, op1)
    for x in (wo1, wo2, pallet, shipment, op1):
        x.refresh_from_db()
    return {
        "musteriler": {k: {"kod": c.code, "pk": c.pk} for k, c in customers.items()},
        "siparisler": {"sevk_zinciri": [o_ship.code, o_ship.pk], "uretim": [o_prod.code, o_prod.pk],
                       "sureli": [o_sureli.code, o_sureli.pk], "yetkisiz": [o_yetkisiz.code, o_yetkisiz.pk]},
        "kalemler": {"sevk_zinciri": [o_ship.lines.get().code, o_ship.lines.get().pk],
                     "yetkisiz": [o_yetkisiz.lines.get().code, o_yetkisiz.lines.get().pk]},
        "is_emri_devir": [wo1.code, wo1.pk, wo1.status], "operasyon_devir": [op1.code, op1.pk],
        "talimat": [ins.version, ins.pk], "is_emri_kapali": [wo2.code, wo2.pk, wo2.status],
        "lot_cikis_hareketi": [issue.pk, str(-issue.quantity)], "lot": [lot.code, lot.pk],
        "palet": [pallet.code, pallet.pk, pallet.box_count, pallet.quality_status],
        "sevkiyat": [shipment.code, shipment.pk, shipment.status],
        "malzemeler": ["KBL-LDPE %97", "KBL-SLIP %3"], "olcu": "640 mm / 35 µ",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cikti", help="Kayıt kodlarının yazılacağı JSON dosyası (parola içermez)")
    ap.add_argument("--parola-yenile", action="store_true")
    args = ap.parse_args()
    db = guard()
    password = os.environ["KABUL_TEST_PASSWORD"]
    from accounts.models import User
    if args.parola_yenile:
        for no, name, *_ in TEST_USERS:
            u = User.objects.filter(personnel_no=no, full_name=name).first()
            if u:
                u.set_password(password)
                u.save(update_fields=["password"])
        print("Test hesaplarının parolası .env'deki değere eşitlendi (değer yazdırılmaz).")
        return
    from catalog.models import Customer
    with transaction.atomic():
        admin = ensure_users(password)
        depot_note = fix_depot_role(admin)
        records = None if Customer.objects.filter(code="KBL-YETKILI").exists() else seed_data(admin)
    out = {"veritabani": db, "depo_rolu": depot_note, "sevk_yetkileri": shipment_table(),
           "test_hesaplari": [{"personel_no": no, "ad": n, "rol": r, "vardiya": s, "makineler": m}
                              for no, n, r, s, m in TEST_USERS],
           "kayitlar": records}
    text = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    if args.cikti:
        with open(args.cikti, "w") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    main()
