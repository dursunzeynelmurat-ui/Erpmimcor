// Sunucu doğrulaması ve idem_key asıl güvencedir; burada yalnız etkileşim iyileştirilir.
(function () {
  const menu = document.querySelector('.menu');
  const desktop = window.matchMedia('(min-width: 1100px)');
  function syncMenu() { if (menu) menu.open = desktop.matches; }
  syncMenu();
  desktop.addEventListener('change', syncMenu);
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && menu && !desktop.matches && menu.open) {
      menu.open = false;
      menu.querySelector('summary').focus();
    }
  });
  document.querySelectorAll('.menu nav a').forEach(function (link) {
    if (link.pathname === location.pathname && link.search === location.search) link.setAttribute('aria-current', 'page');
  });
  document.querySelectorAll('.table-wrap').forEach(function (wrap) {
    wrap.tabIndex = 0;
    wrap.setAttribute('role', 'region');
    wrap.setAttribute('aria-label', 'Kayıt tablosu; gerekirse yatay kaydırın');
  });
  const error = document.querySelector('.form-errors');
  if (error) { error.tabIndex = -1; error.focus(); }

  function review(form, submitter) {
    const dialog = document.createElement('dialog');
    const title = document.createElement('h2');
    title.id = 'review-title';
    title.textContent = 'İşlemi kontrol edin';
    dialog.setAttribute('aria-labelledby', title.id);
    const description = document.createElement('p');
    description.textContent = form.dataset.review;
    const values = document.createElement('dl');
    values.className = 'kv';
    form.querySelectorAll('input:not([type=hidden]), select, textarea').forEach(function (field) {
      if (field.disabled || ((field.type === 'checkbox' || field.type === 'radio') && !field.checked)) return;
      const label = document.createElement('dt');
      label.textContent = field.labels && field.labels[0] ? field.labels[0].textContent.replace('*', '').trim() : 'Değer';
      const value = document.createElement('dd');
      value.textContent = field.tagName === 'SELECT' ? Array.from(field.selectedOptions).map(o => o.text).join(', ') : field.value || (field.name === 'target_code' ? 'Yeni palet oluşturulacak' : 'Belirtilmedi');
      values.append(label, value);
    });
    const notice = document.createElement('p');
    notice.className = 'muted';
    notice.textContent = 'Henüz kaydedilmedi. Onayladığınızda sunucu bilgileri ve güncel stok durumunu kontrol eder.';
    const actions = document.createElement('div');
    actions.className = 'actions';
    const back = document.createElement('button');
    back.type = 'button'; back.className = 'btn'; back.textContent = 'Düzenlemeye dön';
    const confirm = document.createElement('button');
    confirm.type = 'button'; confirm.className = 'btn btn-primary'; confirm.textContent = 'Onayla ve kaydet';
    actions.append(back, confirm); dialog.append(title, description, values, notice, actions);
    document.body.append(dialog);
    back.addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => { dialog.remove(); if (submitter) submitter.focus(); });
    confirm.addEventListener('click', function () {
      dialog.close();
      form.dataset.reviewed = '1';
      form.requestSubmit(submitter || undefined);
    });
    dialog.showModal(); back.focus();
  }
  document.addEventListener('submit', function (event) {
    const form = event.target;
    if (form.method.toLowerCase() !== 'post') return;
    if (form.dataset.submitting === '1') { event.preventDefault(); return; }
    if (form.dataset.review && form.dataset.reviewed !== '1' && typeof HTMLDialogElement !== 'undefined') {
      event.preventDefault(); review(form, event.submitter); return;
    }
    delete form.dataset.reviewed;
    form.dataset.submitting = '1';
    form.querySelectorAll('button[type=submit], button:not([type])').forEach(function (button) {
      button.disabled = true;
      button.dataset.label = button.textContent;
      button.textContent = 'Gönderiliyor…';
    });
  });
  window.addEventListener('pageshow', function () {
    document.querySelectorAll('form[data-submitting="1"]').forEach(function (form) {
      delete form.dataset.submitting;
      delete form.dataset.reviewed;
      form.querySelectorAll('button[data-label]').forEach(function (button) {
        button.disabled = false; button.textContent = button.dataset.label;
      });
    });
  });
})();
