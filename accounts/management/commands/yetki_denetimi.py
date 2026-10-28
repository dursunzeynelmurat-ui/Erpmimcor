"""Salt okunur yetki denetimi raporu (hiçbir kaydı değiştirmez).

Örnekler:
  python manage.py yetki_denetimi                     # tüm aktif kullanıcılar, yalnız bulgusu olanlar
  python manage.py yetki_denetimi --hepsi             # bulgusu olmayanlar dahil
  python manage.py yetki_denetimi --kullanici 2002    # tek kişi, etkin yetkilerin kaynağıyla
  python manage.py yetki_denetimi --kullanici 2002 --musteri M-ORNEK   # bu müşteriyi neden görüyor?
  python manage.py yetki_denetimi --json > rapor.json
"""
import json

from django.core.management.base import BaseCommand, CommandError

from accounts.audit import explain_customer, sensitive_actions, user_report
from accounts.models import User

ORDER = {"KURAL": 0, "ENGEL": 1, "İNCELE": 2, "BİLGİ": 3}


class Command(BaseCommand):
    help = "Kullanıcıların birleşik etkin yetkilerini, kaynaklarını ve iş kuralını aşan durumları raporlar (salt okunur)."

    def add_arguments(self, parser):
        parser.add_argument("--kullanici", help="Personel no")
        parser.add_argument("--musteri", help="Müşteri kodu: kullanıcının bu müşteriyi neden gördüğünü açıkla")
        parser.add_argument("--hepsi", action="store_true", help="Bulgusu olmayan kullanıcıları da yaz")
        parser.add_argument("--pasif", action="store_true", help="Pasif kullanıcıları da dahil et")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **opts):
        users = User.objects.select_related("shift").order_by("personnel_no")
        if opts["kullanici"]:
            users = users.filter(personnel_no=opts["kullanici"])
            if not users.exists():
                raise CommandError("Kullanıcı bulunamadı.")
        elif not opts["pasif"]:
            users = users.filter(is_active=True)
        reports = [user_report(u) for u in users]
        if opts["musteri"]:
            for u, r in zip(users, reports):
                r["musteri_aciklama"] = {opts["musteri"]: explain_customer(u, opts["musteri"])}
        if opts["json"]:
            self.stdout.write(json.dumps({"kullanicilar": reports, "hassas_islemler": sensitive_actions()},
                                         ensure_ascii=False, indent=2))
            return
        detail = bool(opts["kullanici"])
        counts = {}
        for r in reports:
            for f in r["bulgular"]:
                counts[f["duzey"]] = counts.get(f["duzey"], 0) + 1
            if not (r["bulgular"] or opts["hepsi"] or detail):
                continue
            self.stdout.write(f"\n== {r['personel_no']} {r['ad']}{'' if r['aktif'] else ' (PASİF)'} · vardiya "
                              f"{r['vardiya'] or '—'} · makine {', '.join(r['makineler']) or '—'}")
            self.stdout.write("   Roller: " + (", ".join(f"{x['kod']}{'' if x['sablon'] else ' (özel)'}"
                                                         for x in r["roller"]) or "—"))
            if r["musteri_erisimleri"]:
                self.stdout.write("   Müşteri erişimi: " + ", ".join(
                    f"{c['musteri']} ({c['kaynak_adi']}{', bitiş ' + c['bitis'] if c['bitis'] else ''}"
                    f"{', İNCELEME BEKLİYOR' if c['inceleme_bekliyor'] else ''})" for c in r["musteri_erisimleri"]))
            if detail:
                self.stdout.write("   Etkin yetkiler (işlem: kapsam ← rol):")
                for a, scopes in r["etkin_yetkiler"].items():
                    self.stdout.write(f"     {a}: " + "; ".join(f"{s} ← {', '.join(rs)}" for s, rs in scopes.items()))
            for code, text in r.get("musteri_aciklama", {}).items():
                self.stdout.write(f"   {code}: {text}")
            for f in sorted(r["bulgular"], key=lambda f: ORDER[f["duzey"]]):
                self.stdout.write(f"   [{f['duzey']}] {f['bulgu']}\n      → {f['onerilen_islem']}")
        self.stdout.write("\nÖzet: " + (", ".join(f"{k} {counts[k]}" for k in sorted(counts, key=ORDER.get))
                                         or "bulgu yok") + f" · {len(reports)} kullanıcı. Rapor hiçbir kaydı değiştirmez; "
                          "önerilen işlemler yönetici ekranlarından elle yapılır.")
