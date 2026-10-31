"""Docker profilleri için ayar bayrakları: varsayılan davranış değişmez; yerel HTTP kipi yalnız açıkça istenince Secure
çerezleri kapatır; TLS vekili kipinde HTTPS bilgisi X-Forwarded-Proto'dan okunur; çoklu worker için paylaşılan önbellek."""
import json
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

BASE = Path(__file__).resolve().parents[2]
PROBE = ("import django, json; from django.conf import settings as s; django.setup(); print(json.dumps({"
         "'debug': s.DEBUG, 'session_secure': s.SESSION_COOKIE_SECURE, 'csrf_secure': s.CSRF_COOKIE_SECURE, "
         "'proxy': list(getattr(s, 'SECURE_PROXY_SSL_HEADER', None) or []), "
         "'cache': s.CACHES['default']['BACKEND'], 'static_root': str(s.STATIC_ROOT)}))")


def probe(**env):
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "STATIC_ROOT"))}
    # Değerler açıkça verilir: settings, depo kökündeki yerel .env'i de yükler (ortam değişkeni önceliklidir).
    clean.update({"DJANGO_SETTINGS_MODULE": "mimcor.settings", "DJANGO_SECRET_KEY": "test-yalniz",
                  "DJANGO_DEBUG": "0", "DJANGO_LOCAL_HTTP": "0", "DJANGO_BEHIND_TLS_PROXY": "0",
                  "DJANGO_CACHE": "locmem", **env})
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=BASE, env=clean, capture_output=True, text=True,
                         check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


class DeploySettingsTests(SimpleTestCase):
    def test_https_defaults_without_flags(self):
        s = probe()
        self.assertEqual((s["debug"], s["session_secure"], s["csrf_secure"], s["proxy"]), (False, True, True, []))
        self.assertIn("LocMemCache", s["cache"])

    def test_local_http_only_when_explicit(self):
        s = probe(DJANGO_LOCAL_HTTP="1")
        self.assertEqual((s["debug"], s["session_secure"], s["csrf_secure"]), (False, False, False))

    def test_secure_profile(self):
        s = probe(DJANGO_LOCAL_HTTP="0", DJANGO_BEHIND_TLS_PROXY="1", DJANGO_CACHE="db",
                  STATIC_ROOT="/app/staticfiles")
        self.assertEqual((s["session_secure"], s["csrf_secure"]), (True, True))
        self.assertEqual(s["proxy"], ["HTTP_X_FORWARDED_PROTO", "https"])
        self.assertIn("DatabaseCache", s["cache"])
        self.assertEqual(s["static_root"], "/app/staticfiles")
