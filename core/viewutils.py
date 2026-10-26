"""View yardımcıları: form doğrulama → servis (tek seferlik) → mesaj → yönlendirme."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from .exceptions import BusinessRuleError
from .services import perform_once


def run_form_action(request, form, *, action, fn, success_message, url_for):
    """Form geçerliyse fn(cleaned_data) çalıştırır. Başarıda HttpResponseRedirect döner,
    aksi halde None döner ve hatalar forma eklenir. Mesaj yalnızca kayıt tamamlandıktan sonra eklenir."""
    if not form.is_valid():
        messages.error(request, "Kaydedilmedi: formdaki hataları düzeltin.")
        return None
    try:
        result = perform_once(
            user=request.user,
            key=form.cleaned_data.get("idem_key"),
            action=action,
            fn=lambda: fn(form.cleaned_data),
            url_for=url_for,
        )
    except BusinessRuleError as exc:
        form.add_error(exc.field if exc.field in form.fields else None, exc.message)
        messages.error(request, f"Kaydedilmedi: {exc.message}")
        return None
    except PermissionDenied:
        raise
    if result.duplicate:
        messages.info(request, "Bu işlem daha önce kaydedilmişti; tekrar uygulanmadı.")
        return redirect(result.url or request.path)
    messages.success(request, success_message(result.value) if callable(success_message) else success_message)
    return redirect(result.url or request.path)
