"""Manuel kabul ortamı hazırlığı — sürüm c316506 (teknik sipariş, reçete, baskı, repete, numune).

Uygulama kodunu değiştirmez; yalnız servisler üzerinden, işlem geçmişine yazarak kurgusal test hesapları ve kayıtlar
açar. Güvenlik:
- Yalnız adı "kabul" içeren veritabanında çalışır; `mimcor`, `mimcor_uat`, `mimcor_kabul_kopya` adlarında reddeder.
- Parola KABUL_TEST_PASSWORD ortam değişkeninden (.env, git dışı) okunur; ekrana, loga veya dosyaya yazılmaz.
- UAT kullanıcılarının parolası, rolü, erişimi değiştirilmez. Ortak rol değişiklikleri (yalnız kopyada):
  depo rolünden `shipment.approve` çıkarılır; şablonda olup özelleştirilmiş rollerde eksik kalan **yalnız**
  `artwork.*` / `sample.*` yetkileri eklenir. Şablon toplu uygulanmaz (ilgisiz yetki eklenmez).

Kullanım (kabul çalışma dizininde, .env orada):
  cd <kabul-calisma-dizini> && python <bu-dosya> --cikti kayitlar.json
  python <bu-dosya> --parola-yenile      # yalnız 8xxx test hesaplarının parolasını .env'deki değere eşitler
"""
import argparse
import datetime
import io
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

PROTECTED = ("mimcor", "mimcor_uat", "mimcor_kabul_kopya")
TEST_USERS = [  # (personel no, ad, rol, vardiya, makineler)
    ("8101", "Kabul Yönetici", "yonetici", None, []),
    ("8201", "Kabul Satış A", "satis", None, []),
    ("8202", "Kabul Satış B", "satis", None, []),
    ("8301", "Kabul Planlama", "planlama", None, []),
    ("8401", "Kabul Ustabaşı", "ustabasi", "V1", []),
    ("8402", "Kabul Üretim Yetkilisi", "uretim_yetkilisi", "V1", []),
    ("8403", "Kabul Operatör V1 (extruder)", "operator", "V1", ["KBL-EXT1"]),
    ("8404", "Kabul Operatör V2 (extruder)", "operator", "V2", ["KBL-EXT1"]),
    ("8405", "Kabul Matbaa Operatörü V1", "operator", "V1", ["KBL-MAT1"]),
    ("8501", "Kabul Depo", "depo", None, []),
    ("8502", "Kabul Ambar Yetkilisi", "ambar_yetkilisi", None, []),
    ("8601", "Kabul Kalite", "kalite", None, []),
]
NEW_PREFIXES = ("artwork.", "sample.")


def guard():
    db = settings.DATABASES["default"]["NAME"]
    if "kabul" not in db or db in PROTECTED:
        sys.exit(f"Güvenlik: yalnız bu sürüm için açılmış kabul kopyasında çalışır (şu an: {db}).")
    if not os.environ.get("KABUL_TEST_PASSWORD"):
        sys.exit("KABUL_TEST_PASSWORD tanımlı değil (kabul çalışma dizinindeki .env).")
    return db


def U(no):
    from accounts.models import User
    return User.objects.get(personnel_no=no)


def ensure_users(password):
    from accounts import services as acc
    from accounts.models import AccessRole, Shift, User
    from catalog import services as cat
    from catalog.models import Machine, MachineType
    if not User.objects.filter(personnel_no="8101").exists():
        User.objects.create_user("8101", password, full_name="Kabul Yönetici", role="yonetici")
    admin = U("8101")
    for code, name, kind in (("KBL-EXT1", "Kabul extruder 1", MachineType.EXTRUDER),
                             ("KBL-EXT2", "Kabul extruder 2", MachineType.EXTRUDER),
                             ("KBL-MAT1", "Kabul matbaa 1", MachineType.PRINT)):
        if not Machine.objects.filter(code=code).exists():
            cat.create_definition(admin, Machine, {"code": code, "name": name, "machine_type": kind})
    for no, name, role, shift, machines in TEST_USERS:
        sh = Shift.objects.get(code=shift) if shift else None
        u = User.objects.filter(personnel_no=no).first()
        if u is None:
            u = acc.create_user(admin, personnel_no=no, full_name=name, password=password, shift=sh,
                                roles=[AccessRole.objects.get(code=role)], reason="Kabul ortamı test hesabı")
        if machines:
            acc.update_user(admin, u, full_name=u.full_name, team=u.team, shift=sh, is_active=True,
                            machines=list(Machine.objects.filter(code__in=machines)),
                            reason="Kabul ortamı: makine/vardiya ataması")
    return admin


def fix_roles(admin):
    """Depo: yalnız shipment.approve çıkar. Özelleştirilmiş şablon rollerde eksik kalan yeni artwork/sample
    yetkilerini (şablondaki kapsamla) ekle; başka hiçbir yetkiye dokunma."""
    from accounts import services as acc
    from accounts.catalog import TEMPLATES
    from accounts.models import AccessRole
    notes = []
    for role in AccessRole.objects.filter(is_template=True).order_by("code"):
        perms = dict(role.permissions.values_list("action", "scope"))
        template = TEMPLATES.get(role.code, (None, None, {}))[2]
        added = {a: s for a, s in template.items() if a.startswith(NEW_PREFIXES) and a not in perms}
        removed = ["shipment.approve"] if role.code == "depo" and "shipment.approve" in perms else []
        if not added and not removed:
            continue
        for a in removed:
            perms.pop(a)
        perms.update(added)
        acc.update_role(admin, role, name=role.name, description=role.description, hr_assignable=role.hr_assignable,
                        perms=perms, reason="Kabul kopyası: " + "; ".join(
                            (["son sevk onayı kaldırıldı"] if removed else []) +
                            ([f"yeni sürüm yetkileri eklendi ({', '.join(sorted(added))})"] if added else [])))
        notes.append({"rol": role.code, "eklenen": sorted(added), "kaldirilan": removed})
    return notes


def png(text):
    import segno
    from django.core.files.uploadedfile import SimpleUploadedFile
    buf = io.BytesIO()
    segno.make(text).save(buf, kind="png", scale=6)
    return SimpleUploadedFile("kbl-baski.png", buf.getvalue())


def seed_data(admin):
    from accounts.models import Shift
    from catalog import artwork as art
    from catalog import customers as cs
    from catalog import services as cat
    from catalog.models import ArtworkApprovalKind, Customer, Material, MaterialKind, Product, Unit
    from core.models import QualityStatus
    from core.tests.factories import complete_card
    from orders import services as orders
    from production import instructions, lifecycle
    from production import services as prod
    from samples import services as smp
    from samples.models import CriterionUnit, SampleKind
    from warehouse import services as wh
    from catalog.models import Machine
    today = timezone.localdate()
    planner, foreman, depot, store = U("8301"), U("8401"), U("8501"), U("8502")
    s_a, s_b, quality = U("8201"), U("8202"), U("8601")
    customers = {}
    for i, (code, name) in enumerate((("KBL-YETKILI", "Kabul Kurgu Yetkili A.Ş."), ("KBL-SURELI", "Kabul Kurgu Süreli Ltd."),
                                      ("KBL-YETKISIZ", "Kabul Kurgu Yetkisiz San."))):
        c = cat.create_definition(admin, Customer, {"code": code, "name": name})
        complete_card(admin, c, f"99977700{i}{i}")
        customers[code] = c
    cs.grant_access(admin, customers["KBL-YETKILI"], s_a, today, None, "Kabul: kalıcı erişim (kurgusal)")
    cs.grant_access(admin, customers["KBL-SURELI"], s_a, today, today + datetime.timedelta(days=7),
                    "Kabul: süreli erişim (kurgusal)")
    cs.grant_access(admin, customers["KBL-YETKISIZ"], s_b, today, None, "Kabul: yalnız Satış B (kurgusal)")
    mats = {}
    for code, name, kind in (("KBL-LDPE", "Kabul LDPE (kurgusal)", MaterialKind.RAW),
                             ("KBL-LLDPE", "Kabul LLDPE (kurgusal)", MaterialKind.RAW),
                             ("KBL-SLIP", "Kabul kaydırıcı (kurgusal)", MaterialKind.ADDITIVE),
                             ("KBL-MAVI", "Kabul mavi masterbatch (kurgusal)", MaterialKind.COLORANT)):
        mats[code] = cat.create_definition(admin, Material, {"code": code, "name": name, "kind": kind, "unit": "kg"})
        cat.receive_lot(store, mats[code], D("1000") if kind == MaterialKind.RAW else D("100"),
                        supplier_lot=f"KURGU-IRS-{code}")
    lot = mats["KBL-LDPE"].lots.first()

    def product(code, cust, name, per_box=None):
        return cat.create_definition(admin, Product, {"code": code, "customer": customers[cust], "name": name,
                                                      "unit": Unit.BOX, "units_per_box": per_box})
    kb = product("KBL-KB-045", "KBL-YETKILI", "Kabul körüklü baskılı poşet 45µ (kurgusal)", 500)
    plain = product("KBL-FILM-A", "KBL-YETKILI", "Kabul düz film (eski tip revizyon, kurgusal)", 20)
    sureli = product("KBL-FILM-S", "KBL-SURELI", "Kabul film S (kurgusal)", 20)
    yetkisiz = product("KBL-FILM-B", "KBL-YETKISIZ", "Kabul film B (kurgusal)", 20)
    rich = {"width_mm": D("400"), "thickness_micron": D("45"), "product_length_mm": D("550"), "length_m": None,
            "gusset_type": "yan", "gusset_depth_mm": D("60"), "width_basis": "kapali", "width_tol_mm": D("2"),
            "thickness_tol_micron": D("3"), "appearance": "renkli", "color": "Mavi", "color_system": "ral",
            "color_code": "RAL 5015", "color_preview": "#1E5AA8", "perforation": "yok", "print_status": "var",
            "plate_status": "yeni", "plate_code": "KL-KBL-01", "cylinder_circumference_mm": D("600"),
            "print_repeat_mm": D("550"), "print_gap_mm": D("50"), "print_color_count": 2,
            "print_colors": "Pantone 286 C, Siyah (kurgusal)", "print_sides": "tek",
            "print_direction": "Rulodan çıkarken yazı okunur", "process_notes": "Kabul proses notu (kurgusal)"}
    cat.create_revision(planner, kb, rich, [(mats["KBL-LDPE"], D("60")), (mats["KBL-LLDPE"], D("36")),
                                            (mats["KBL-MAVI"], D("3")), (mats["KBL-SLIP"], D("1"))])
    old_style = {"width_mm": D("600"), "thickness_micron": D("30"), "length_m": D("500"), "color": "Şeffaf"}
    for p in (plain, sureli, yetkisiz):  # eski tip: yalnız temel ölçüler (yeni alanlar belirtilmemiş)
        cat.create_revision(planner, p, old_style, [(mats["KBL-LDPE"], D("98")), (mats["KBL-SLIP"], D("2"))])
    a1 = art.upload_artwork(s_a, kb, png("KBL-KB-045 KURGUSAL GORSEL S1"), note="Kabul: ilk görsel (kurgusal)")
    art.approve(planner, a1, ArtworkApprovalKind.TECHNICAL)
    art.approve(s_a, a1, ArtworkApprovalKind.CUSTOMER, evidence="Kurgusal: e-posta 28.10.2026",
                customer_contact="Kurgu Satın Alma")

    def order(sales, prod_, qty, ref, purpose="ticari", approve_line=True):
        o = orders.create_order(sales, customer=prod_.customer, order_date=today, customer_ref=ref, purpose=purpose,
                                creation_method="yeni", lines=[{"product": prod_, "quantity": D(qty), "unit": Unit.BOX}])
        o = orders.approve_order(sales, o)
        if approve_line:
            ln = o.lines.get()
            orders.approve_line(planner, ln, ln.product.current_revision)
        return o
    o_kb = order(s_a, kb, "20", "KBL-PO-BASKILI (kurgusal)")
    o_plain = order(s_a, plain, "10", "KBL-PO-DUZ (kurgusal)")
    o_sureli = order(s_a, sureli, "5", "KBL-PO-SURELI (kurgusal)")
    o_yetkisiz = order(s_b, yetkisiz, "5", "KBL-PO-YETKISIZ (kurgusal)")
    v1 = Shift.objects.get(code="V1")
    ext1, ext2, mat1 = (Machine.objects.get(code=c) for c in ("KBL-EXT1", "KBL-EXT2", "KBL-MAT1"))

    # Baskılı iş emri: extruder + matbaa, serbest bırakılınca görsel s.1'e sabitlenir; talimatlar basılır
    wo_kb = prod.create_work_order(planner, order_line=o_kb.lines.get(), machines=[ext1, mat1], target_quantity=D("20"),
                                   unit=Unit.BOX, foreman=foreman)
    for op in wo_kb.operations.order_by("seq"):
        lifecycle.schedule_operation(foreman, op, planned_date=today, shift=v1, machine=op.machine, queue_position=1)
    lifecycle.release(planner, wo_kb)
    prod.start_work_order(foreman, wo_kb)
    wo_kb.refresh_from_db()
    ins = {op.machine.code: instructions.issue_for_print(foreman, op) for op in wo_kb.operations.order_by("seq")}

    # Stok paleti (KBL-FILM-A): kapalı iş emrinden 40 kutu, kabul + kalite uygun (numune ayırma için)
    wo_st = prod.create_work_order(planner, order_line=o_plain.lines.get(), machines=[ext2], target_quantity=D("40"),
                                   unit=Unit.BOX, foreman=foreman)
    lifecycle.schedule_operation(foreman, wo_st.operations.get(), planned_date=today, shift=v1, machine=ext2,
                                 queue_position=1)
    lifecycle.release(planner, wo_st)
    prod.start_work_order(foreman, wo_st)
    wo_st.refresh_from_db()
    prod.issue_material(foreman, wo_st, lot, D("100"))
    coil = prod.record_coil(foreman, wo_st.operations.get(), weight_kg=D("95"))
    pallet = wh.create_pallet_from_production(foreman, wo_st, 40, coils=[coil])
    prod.complete_operation(foreman, wo_st.operations.get())
    prod.close_work_order(foreman, wo_st, scrap_kg=D("5"), remaining_confirmed=True)
    wh.accept_pallet(depot, pallet, {"gross_kg": D("120"), "location": "KBL-A-01"})
    wh.set_pallet_quality(foreman, pallet, QualityStatus.OK, "Kabul: görsel kontrol uygun (kurgusal)")

    smp.create_criterion(quality, name="Kalınlık", unit=CriterionUnit.MICRON,
                         description="Kurgusal kabul kriteri; aralığı kalite yetkilisi tanımlar.")
    smp.create_criterion(quality, name="Görünüm", unit=CriterionUnit.TEXT, description="Gözle kontrol (kurgusal)")
    ref = smp.create_sample(quality, kind=SampleKind.CUSTOMER_REFERENCE, customer=customers["KBL-YETKILI"], product=kb,
                            quantity=D("5"), unit="adet", counterpart="Kurgu Satın Alma", room="Kalite lab",
                            cabinet="D1", shelf="R2", box="K1", notes="Müşterinin referans poşetleri (kurgusal)",
                            shared_note="Referans numune alındı.")
    for x in (wo_kb, wo_st, pallet):
        x.refresh_from_db()
    return {
        "musteriler": {k: c.pk for k, c in customers.items()},
        "urunler": {p.code: p.pk for p in (kb, plain, sureli, yetkisiz)},
        "siparisler": {k: [o.code, o.pk, o.lines.get().code, o.lines.get().pk]
                       for k, o in (("baskili", o_kb), ("duz", o_plain), ("sureli", o_sureli), ("yetkisiz", o_yetkisiz))},
        "is_emri_baskili": [wo_kb.code, wo_kb.pk, wo_kb.status, f"görsel s.{wo_kb.artwork.version_no}"],
        "operasyonlar": {op.machine.code: [op.code, op.pk] for op in wo_kb.operations.all()},
        "talimatlar": {k: v.pk for k, v in ins.items()},
        "is_emri_stok": [wo_st.code, wo_st.pk, wo_st.status],
        "palet": [pallet.code, pallet.pk, pallet.box_count],
        "gorsel": [a1.version_no, a1.pk], "referans_numune": [ref.code, ref.pk],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cikti")
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
        roles = fix_roles(admin)
        records = None if Customer.objects.filter(code="KBL-YETKILI").exists() else seed_data(admin)
    out = {"veritabani": db, "rol_degisiklikleri": roles,
           "test_hesaplari": [{"personel_no": no, "ad": n, "rol": r, "vardiya": s, "makineler": m}
                              for no, n, r, s, m in TEST_USERS], "kayitlar": records}
    text = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    if args.cikti:
        with open(args.cikti, "w") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    main()
