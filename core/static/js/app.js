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
    // Devre dışı bırakılan düğmenin adı/değeri gönderime girmez; seçilen eylemi (ör. İleri/Geri/Düzenle) korumak
    // için aynı ad ve değerle gizli alan eklenir.
    const submitter = event.submitter;
    if (submitter && submitter.name) {
      const keep = document.createElement('input');
      keep.type = 'hidden'; keep.name = submitter.name; keep.value = submitter.value; keep.dataset.submitter = '1';
      form.append(keep);
    }
    form.querySelectorAll('button[type=submit], button:not([type])').forEach(function (button) {
      button.disabled = true;
      button.dataset.label = button.textContent;
      button.textContent = 'Gönderiliyor…';
    });
  });
  // Müşteriye bağlı ürün listesi. Sunucu her kayıtta ürünün müşteriye ait ve aktif olduğunu ayrıca doğrular.
  document.querySelectorAll('select[data-customer-field]').forEach(function (product) {
    const form = product.form;
    const customer = form && form.elements[product.dataset.customerField];
    if (!customer) return;
    const help = document.getElementById(product.id + '_helptext');
    if (help) help.setAttribute('aria-live', 'polite');
    const retry = document.createElement('button');
    retry.type = 'button'; retry.className = 'btn'; retry.textContent = 'Tekrar dene';
    showRetry(false);
    (help || product).after(retry);
    function showRetry(on) { retry.hidden = !on; retry.style.display = on ? '' : 'none'; } // .btn, [hidden]'ı ezer
    let seq = 0;
    let controller = null;

    function setState(state, message) {
      product.dataset.state = state;
      product.disabled = state !== 'ready';
      product.setAttribute('aria-busy', state === 'loading' ? 'true' : 'false');
      if (help) help.textContent = message;
      showRetry(state === 'error');
    }
    function clearOptions() {
      product.value = '';
      Array.from(product.options).forEach(function (opt) { if (opt.value) opt.remove(); });
    }
    function load() {
      const id = customer.value;
      const mine = ++seq;
      if (controller) controller.abort();
      controller = null;
      clearOptions();
      product.dataset.loadedCustomer = '';
      if (!id) { setState('none', product.dataset.msgNoCustomer); return; }
      setState('loading', product.dataset.msgLoading);
      const ctrl = controller = new AbortController();
      const timer = setTimeout(function () { ctrl.abort(); }, 15000);
      fetch(product.dataset.productsUrl.replace('__ID__', encodeURIComponent(id)),
            { headers: { Accept: 'application/json' }, credentials: 'same-origin', signal: ctrl.signal })
        .then(function (resp) { if (!resp.ok) throw new Error(String(resp.status)); return resp.json(); })
        .then(function (data) {
          if (mine !== seq) return; // Eski müşterinin geç gelen yanıtı yeni listeyi ezmez.
          (data.products || []).forEach(function (p) { product.add(new Option(p.label, p.id)); });
          product.dataset.loadedCustomer = id;
          if (product.options.length > 1) setState('ready', product.dataset.msgReady);
          else setState('empty', product.dataset.msgEmpty);
        })
        .catch(function () {
          if (mine !== seq) return;
          clearOptions();
          setState('error', product.dataset.msgError);
        })
        .finally(function () { clearTimeout(timer); });
    }
    customer.addEventListener('change', load);
    retry.addEventListener('click', load);
    // Geri tuşu/otomatik doldurma ile müşteri, sunucunun listelediğinden farklıysa listeyi yenile.
    if ((customer.value || '') !== (product.dataset.loadedCustomer || '')) load();
  });
  document.addEventListener('submit', function (event) {
    // Ürün listesi yüklenirken gönderim engellenir (yarım seçimle kayıt denemesi olmasın).
    if (event.target.querySelector('select[data-customer-field][data-state="loading"]')) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);
  window.addEventListener('pageshow', function () {
    document.querySelectorAll('form[data-submitting="1"]').forEach(function (form) {
      delete form.dataset.submitting;
      form.querySelectorAll('input[data-submitter]').forEach(function (el) { el.remove(); });
      delete form.dataset.reviewed;
      form.querySelectorAll('button[data-label]').forEach(function (button) {
        button.disabled = false; button.textContent = button.dataset.label;
      });
    });
  });
})();

// Talimat çıktısı: yazdır düğmesi ve basım talebinden sonra otomatik yazdırma penceresi
document.querySelectorAll('[data-print]').forEach(function (b) { b.addEventListener('click', function () { window.print(); }); });
if (document.querySelector('[data-autoprint]')) { window.addEventListener('load', function () { window.print(); }); }

// Koşullu alanlar: "Baskı yok", "Körük yok" vb. seçildiğinde ilgisiz alanlar gizlenir. Sunucu gizli alanların
// değerlerini ayrıca temizler; bu yalnız ekran kolaylığıdır.
(function () {
  document.querySelectorAll('[data-show-if]').forEach(function (input) {
    const form = input.form;
    const parts = input.dataset.showIf.split(':');
    const controller = form && form.elements[parts[0]];
    const allowed = (parts[1] || '').split(',');
    const wrap = input.closest('.field') || input;
    if (!controller) return;
    function sync() {
      const on = allowed.indexOf(controller.value) !== -1;
      wrap.hidden = !on;
      input.disabled = !on;
    }
    controller.addEventListener('change', sync);
    sync();
  });
  // Değişmiş formdan ayrılmada tarayıcı uyarısı (kaydetme sırasında gösterilmez).
  document.querySelectorAll('form[data-warn-unsaved]').forEach(function (form) {
    let dirty = false;
    form.addEventListener('input', function () { dirty = true; });
    form.addEventListener('change', function () { dirty = true; });
    form.addEventListener('submit', function () { dirty = false; });
    window.addEventListener('beforeunload', function (event) {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = '';
    });
  });
})();

// Reçete düzenleyici: "Malzeme ekle" yeni satır açar (JS yoksa sunucu yeni boş satırla döner), toplam ve kg canlı
// hesaplanır. Kesin hesap ve doğrulama sunucudadır (Decimal, 0,001 kg).
(function () {
  const form = document.querySelector('form.recipe-editor');
  if (!form) return;
  const rows = form.querySelector('[data-rows]');
  const count = form.querySelector('[data-row-count]');
  const totalOut = form.querySelector('[data-total]');
  const batchInput = form.elements['batch_kg'];
  function num(v) {
    v = (v || '').trim().replace(/\s/g, '');
    if (!v || (v.indexOf(',') !== -1 && v.indexOf('.') !== -1)) return null;
    const n = Number(v.replace(',', '.'));
    return isNaN(n) ? null : n;
  }
  function fmt(n, d) { return n.toFixed(d).replace(/\.?0+$/, '').replace('.', ','); }
  function recalc() {
    let total = 0;
    const batch = num(batchInput && batchInput.value) || Number(form.dataset.batch || 100);
    rows.querySelectorAll('[data-row]').forEach(function (row) {
      const pct = num((row.querySelector('input[name^="pct_"]') || {}).value);
      const out = row.querySelector('[data-kg]');
      if (pct !== null) total += pct;
      if (out) out.textContent = pct === null ? '—' : fmt(batch * pct / 100, 3) + ' kg';
    });
    total = Math.round(total * 1000) / 1000;
    if (totalOut) {
      totalOut.innerHTML = '';
      const strong = document.createElement('strong');
      strong.textContent = total === 100 ? 'Karışım toplamı %100. Onaylanabilir.'
        : 'Karışım toplamı %' + fmt(total, 3) + '. Onaylamak için %100 olmalı.';
      totalOut.append(strong);
    }
  }
  form.addEventListener('input', recalc);
  form.addEventListener('change', recalc);
  const add = form.querySelector('[data-add-row]');
  if (add) add.addEventListener('click', function (event) {
    event.preventDefault();
    const last = rows.querySelector('[data-row]:last-of-type');
    const n = Number(count.value);
    const row = last.cloneNode(true);
    row.querySelectorAll('[name]').forEach(function (el) {
      el.name = el.name.replace(/_\d+$/, '_' + n);
      if (el.id) el.id = el.id.replace(/_\d+$/, '_' + n);
      if (el.tagName === 'SELECT') el.selectedIndex = 0; else el.value = '';
      el.removeAttribute('aria-invalid');
      el.removeAttribute('aria-describedby');
    });
    row.querySelectorAll('label[for]').forEach(function (l) { l.htmlFor = l.htmlFor.replace(/_\d+$/, '_' + n); });
    row.querySelectorAll('.error, .help').forEach(function (e) { e.remove(); });
    row.querySelectorAll('.has-error').forEach(function (e) { e.classList.remove('has-error'); });
    rows.append(row);
    count.value = String(n + 1);
    recalc();
    const sel = row.querySelector('select');
    if (sel) sel.focus();
  });
  recalc();
})();
