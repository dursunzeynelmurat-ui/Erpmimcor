// Çift tıklamaya karşı: form gönderilirken düğmeyi kilitle. Asıl koruma sunucudadır (tek kullanımlık form anahtarı).
document.addEventListener("submit", function (e) {
  var form = e.target;
  if (form.dataset.submitting === "1") { e.preventDefault(); return; }
  form.dataset.submitting = "1";
  form.querySelectorAll("button[type=submit]").forEach(function (b) {
    b.disabled = true;
    b.dataset.label = b.textContent;
    b.textContent = "Kaydediliyor…";
  });
});
// Sayfa geri tuşuyla önbellekten açılırsa düğmeleri yeniden etkinleştir.
window.addEventListener("pageshow", function (e) {
  if (!e.persisted) return;
  document.querySelectorAll("form[data-submitting='1']").forEach(function (f) {
    f.dataset.submitting = "";
    f.querySelectorAll("button[type=submit]").forEach(function (b) { b.disabled = false; if (b.dataset.label) b.textContent = b.dataset.label; });
  });
});
