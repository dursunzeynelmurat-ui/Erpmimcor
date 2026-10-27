"""Tarayıcı testleri için ortak yardımcılar.

- CHROMIUM_PATH verilirse o Chromium kullanılır (yerel geliştirme); verilmezse Playwright'ın kurduğu Chromium (CI).
- Her tarayıcı bağlamında Playwright trace kaydı tutulur; test başarısız olursa BROWSER_ARTIFACT_DIR altına trace
  (.zip) ve tam sayfa ekran görüntüsü yazılır. Başarılı testte trace atılır.
Yalnız kurgusal seed_demo verisi kullanılır; parola/oturum bilgisi dosyaya yazılmaz.
"""
import os
import re
from pathlib import Path


def launch(pw):
    path = os.getenv("CHROMIUM_PATH")
    kwargs = {"headless": True, "args": ["--no-sandbox"]}
    if path:
        kwargs["executable_path"] = path
    return pw.chromium.launch(**kwargs)


def artifact_dir():
    d = Path(os.getenv("BROWSER_ARTIFACT_DIR", "browser-artifacts"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_context(browser, **kwargs):
    context = browser.new_context(**kwargs)
    context.tracing.start(screenshots=True, snapshots=True)
    return context


def failed(testcase):
    """tearDown sırasında bu testin başarısız olup olmadığı (Python 3.11+ sonuç listelerinden)."""
    outcome = getattr(testcase, "_outcome", None)
    if outcome is None:
        return False
    if not outcome.success:
        return True
    result = outcome.result
    return any(t is testcase for t, _ in list(getattr(result, "errors", [])) + list(getattr(result, "failures", [])))


def finish_contexts(testcase, contexts):
    """tearDown'da çağrılır: başarısızlıkta trace + ekran görüntüsü saklanır."""
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", testcase.id())
    bad = failed(testcase)
    for i, context in enumerate(contexts):
        try:
            if bad:
                out = artifact_dir()
                for j, page in enumerate(context.pages):
                    page.screenshot(path=str(out / f"{name}-{i}-{j}.png"), full_page=True)
                context.tracing.stop(path=str(out / f"{name}-{i}-trace.zip"))
            else:
                context.tracing.stop()
        except Exception:  # noqa: BLE001 - artifact toplama testin sonucunu değiştirmemeli
            pass
