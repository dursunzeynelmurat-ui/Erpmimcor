"""Başlangıç rol şablonu değişikliklerinin veritabanına güvenli taşınması (migration'lardan çağrılır).

Kural: bir şablon rolün yetki kümesi, değişiklikten önceki şablonla *birebir* aynıysa yeni şablona güncellenir.
Yönetici tarafından özelleştirilmiş rollere dokunulmaz; bunun yerine işlem geçmişine "şablon farkı" notu yazılır ve
rol listesi yöneticiye önerilen farkı ve etkilenen kullanıcı sayısını gösterir.
"""


def sync_template_role(apps, code, old_perms, new_perms, reason):
    AccessRole = apps.get_model("accounts", "AccessRole")
    RolePermission = apps.get_model("accounts", "RolePermission")
    AuditLog = apps.get_model("core", "AuditLog")
    role = AccessRole.objects.filter(code=code, is_template=True).first()
    if role is None:
        return "yok"
    current = dict(RolePermission.objects.filter(role=role).values_list("action", "scope"))
    # Rol, sonraki bir sürümün şablonuyla oluşturulmuşsa (temiz kurulumda ilk migration güncel şablonu kullanır),
    # bu değişiklikten sonra eklenen yetkiler karşılaştırmada yok sayılır ve korunur.
    from accounts.catalog import TEMPLATES
    live = dict(TEMPLATES[code][2]) if code in TEMPLATES else {}
    later = {a: s for a, s in current.items() if a not in old_perms and a not in new_perms and live.get(a) == s}
    current = {a: s for a, s in current.items() if a not in later}
    if current == dict(new_perms):
        return "guncel"  # rol zaten yeni şablonla oluşturulmuş (ör. eski veritabanında rolleri bu sürüm yarattı)
    if current != dict(old_perms):
        AuditLog.objects.create(user=None, action="role.template_drift", object_type="accounts.accessrole",
                                object_id=str(role.pk), object_code=role.code,
                                changes={"oneri": [None, sorted(set(new_perms.items()) ^ set(old_perms.items()))]},
                                reason=f"Rol özelleştirilmiş; şablon değişikliği otomatik uygulanmadı. {reason}")
        return "ozel"
    RolePermission.objects.filter(role=role).delete()
    RolePermission.objects.bulk_create([RolePermission(role=role, action=a, scope=s)
                                        for a, s in {**new_perms, **later}.items()])
    removed = {a: s for a, s in old_perms.items() if new_perms.get(a) != s}
    added = {a: s for a, s in new_perms.items() if old_perms.get(a) != s}
    AuditLog.objects.create(user=None, action="role.update", object_type="accounts.accessrole", object_id=str(role.pk),
                            object_code=role.code, changes={"kaldirilan": [removed, None], "eklenen": [None, added]},
                            reason=f"Yükseltme: değiştirilmemiş başlangıç şablonu güncellendi. {reason}")
    return "guncellendi"
