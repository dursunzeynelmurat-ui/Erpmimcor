"""Salt okunur yetki denetimi: kullanıcının neden neye eriştiği ve iş kuralını aşan durumlar.

Hiçbir kayıt değiştirilmez. Bulgu düzeyleri:
- KURAL: iş kuralını aşıyor (ör. fiziksel çıkış yetkisi yönetici/ambar yetkilisi dışında bir rolden geliyor).
- İNCELE: yönetici kararı gerekiyor (taşınan/kaynağı belirsiz müşteri erişimi, çoklu rolle genişleyen kapsam).
- ENGEL: kullanıcı beklenen işini yapamaz (vardiyası veya makinesi tanımsız operatör).
- BİLGİ: şablondan farklı rol vb.; tek başına hata değildir.
"""
from django.utils import timezone

from .catalog import BY_CODE, TEMPLATES, Kind, Scope

# Bu işlemler yalnız başlangıç şablonunda onları içeren rollerden gelmelidir (ör. shipment.exit → yönetici, ambar
# yetkilisi). Özelleştirilmiş rol, şablondan farklı olduğu için değil, bu işlemleri verdiği için raporlanır.
SENSITIVE_KINDS = (Kind.APPROVE, Kind.CORRECTION_APPROVE, Kind.ADMIN)
SENSITIVE_EXTRA = ("recipe.view", "recipe.change", "shipment.undo", "material.deviation", "customer.access.manage")
FLOOR_SCOPES = (Scope.MACHINE, Scope.SHIFT, Scope.MACHINE_SHIFT)


def sensitive_actions():
    codes = {a.code for a in BY_CODE.values() if a.kind in SENSITIVE_KINDS} | set(SENSITIVE_EXTRA)
    return {code: sorted(c for c, (_, _, perms) in TEMPLATES.items() if code in perms) for code in sorted(codes)}


def _active_assignments(user):
    from .models import RoleStatus, UserRole
    return list(UserRole.objects.filter(user=user, revoked_at__isnull=True, role__status=RoleStatus.ACTIVE)
                .select_related("role", "granted_by").prefetch_related("role__permissions").order_by("role__code"))


def _customer_accesses(user):
    from catalog.models import CustomerAccess
    today = timezone.localdate()
    rows = []
    for a in CustomerAccess.objects.filter(user=user, revoked_at__isnull=True).select_related("customer", "granted_by"):
        if a.valid_until and a.valid_until < today:
            continue
        rows.append({"musteri": a.customer.code, "kaynak": a.source, "kaynak_adi": a.get_source_display(),
                     "baslangic": a.valid_from.isoformat(), "bitis": a.valid_until.isoformat() if a.valid_until else None,
                     "gecerli": a.valid_from <= today, "inceleme_bekliyor": a.needs_review,
                     "inceleme": a.review_note or None, "veren": a.granted_by.personnel_no if a.granted_by else None,
                     "gerekce": a.reason})
    return rows


def user_report(user):
    from .models import UserMachine
    from .services import template_drift
    assignments = _active_assignments(user)
    effective = {}
    for ur in assignments:
        for p in ur.role.permissions.all():
            effective.setdefault(p.action, {}).setdefault(p.scope, []).append(ur.role.code)
    machines = sorted(UserMachine.objects.filter(user=user).values_list("machine__code", flat=True))
    customers = _customer_accesses(user)
    findings = []

    def add(level, text, action):
        findings.append({"duzey": level, "bulgu": text, "onerilen_islem": action})

    for code, allowed in sensitive_actions().items():
        for scope, roles in effective.get(code, {}).items():
            extra = [r for r in roles if r not in allowed]
            if extra:
                add("KURAL", f"'{code}' ({BY_CODE[code].label}) {', '.join(extra)} rolünden geliyor; iş kuralına göre "
                             f"yalnız {', '.join(allowed) or 'yönetici'} rollerinde olmalı.",
                    f"{', '.join(extra)} rolünden '{code}' yetkisini kaldırın veya kişiye uygun rolü atayın.")
    for code, scopes in sorted(effective.items()):
        if len(scopes) > 1:
            parts = "; ".join(f"{s} ← {', '.join(r)}" for s, r in sorted(scopes.items()))
            add("İNCELE", f"'{code}' birden fazla kapsamla geliyor ve VEYA ile genişliyor: {parts}.",
                "Genişleme bilinçli mi? Değilse fazla rolü kaldırın.")
    for ur in assignments:
        drift = template_drift(ur.role)
        if drift is None:
            add("BİLGİ", f"{ur.role.code} şablon dışı (özel) rol.", "İşlemleri iş kuralına göre gözden geçirin.")
        elif drift[0] or drift[1]:
            wider = [a for a, s in drift[1].items() if s == Scope.ALL and TEMPLATES[ur.role.code][2].get(a) not in (None, Scope.ALL)]
            for a in wider:
                add("KURAL", f"{ur.role.code} rolünde '{a}' tüm kayıtlar kapsamında; şablonda "
                             f"'{TEMPLATES[ur.role.code][2][a]}'.", f"{ur.role.code} rolünde '{a}' kapsamını daraltın.")
            add("BİLGİ", f"{ur.role.code} şablondan farklı (eksik {len(drift[0])}, fazla/farklı {len(drift[1])}).",
                "Farklar bilinçli değilse rol ekranında 'Önerilen şablonu uygula'.")
    pending = [c["musteri"] for c in customers if c["inceleme_bekliyor"]]
    if pending:
        add("İNCELE", f"İnceleme bekleyen müşteri erişimi: {', '.join(pending)}.",
            "Müşteri erişim incelemesi ekranında her erişimi onaylayın veya kaldırın.")
    unclear = [c["musteri"] for c in customers if c["kaynak"] in ("tasima", "belirsiz") and not c["inceleme_bekliyor"]
               and not c["inceleme"]]
    if unclear:
        add("İNCELE", f"Kaynağı taşıma/belirsiz ve inceleme notu olmayan erişim: {', '.join(unclear)}.",
            "Erişimin gerekçesini kayda geçirin veya kaldırın.")
    customer_scoped = [a for a, s in effective.items() if Scope.CUSTOMER in s]
    if customer_scoped and not customers:
        add("ENGEL", "Müşteri kapsamlı yetkileri var ama geçerli müşteri erişimi yok; listeler boş görünür.",
            "Gerekli müşteri erişimini verin.")
    floor = sorted({s for scopes in effective.values() for s in scopes if s in FLOOR_SCOPES})
    if floor:
        if not user.shift_id and (Scope.SHIFT in floor or Scope.MACHINE_SHIFT in floor):
            add("ENGEL", "Vardiyası tanımsız: vardiya kapsamlı teknik bilgi ve üretim kaydı açılmaz.",
                "İK ekranından vardiya atayın.")
        if not machines and (Scope.MACHINE in floor or Scope.MACHINE_SHIFT in floor):
            add("ENGEL", "Atanmış makinesi yok: makine kapsamlı işler görünmez.", "Kullanıcıya makine atayın.")
    return {
        "personel_no": user.personnel_no, "ad": user.full_name, "aktif": user.is_active,
        "vardiya": user.shift.code if user.shift_id else None, "makineler": machines,
        "roller": [{"kod": ur.role.code, "ad": ur.role.name, "sablon": ur.role.is_template,
                    "veren": ur.granted_by.personnel_no if ur.granted_by else None, "gerekce": ur.reason}
                   for ur in assignments],
        "etkin_yetkiler": {a: {s: r for s, r in sorted(scopes.items())} for a, scopes in sorted(effective.items())},
        "musteri_erisimleri": customers, "bulgular": findings,
    }


def explain_customer(user, customer_code):
    """Kullanıcı bu müşterinin kartını/siparişini neden görebiliyor (veya göremiyor)?"""
    report = user_report(user)
    via_roles = {s: r for s, r in report["etkin_yetkiler"].get("customer.view", {}).items()}
    accesses = [c for c in report["musteri_erisimleri"] if c["musteri"] == customer_code and c["gecerli"]]
    if Scope.ALL in via_roles:
        return f"Görür: customer.view tüm kayıtlar kapsamında ({', '.join(via_roles[Scope.ALL])} rolünden)."
    if Scope.CUSTOMER in via_roles and accesses:
        a = accesses[0]
        return (f"Görür: customer.view müşteri kapsamında ({', '.join(via_roles[Scope.CUSTOMER])} rolünden) ve "
                f"{customer_code} erişimi var (kaynak: {a['kaynak_adi']}, bitiş: {a['bitis'] or 'süresiz'}).")
    if Scope.CUSTOMER in via_roles:
        return f"Görmez: müşteri kapsamlı yetkisi var ama {customer_code} için geçerli erişim yok."
    return "Görmez: customer.view yetkisi yok."
