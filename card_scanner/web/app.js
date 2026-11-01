/* 名片庫主程式:清單與快速索引、掃描流程、編輯、重複偵測、匯入匯出、設定。 */
(function () {
  'use strict';
  const { CardIndex, groupCards, highlight, escapeHtml, parseQuery } = self.CardSearch;
  const Store = self.CardStore, OCR = self.CardOCR, Parser = self.CardParser;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = escapeHtml;

  const FIELD_LABELS = {
    name: '姓名', nameEn: '英文姓名', company: '公司', companyEn: '公司英文名', department: '部門', title: '職稱',
    mobile: '手機', tel: '電話', fax: '傳真', email: 'Email', website: '網站', address: '地址', taxId: '統一編號', lineId: 'LINE ID',
    tags: '標籤', notes: '備註',
  };
  const EDIT_FIELDS = Object.keys(FIELD_LABELS);
  const MULTI = ['mobile', 'tel', 'fax', 'email'];

  /* ---------- 設定 ---------- */
  const SETTINGS_KEY = 'card-scanner-settings';
  const settings = Object.assign({ engine: 'tesseract', langs: 'chi_tra', apiKey: '', model: 'claude-opus-5-5', mode: 'name' }, (() => {
    try { return JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}'); } catch (e) { return {}; }
  })());
  const saveSettings = () => { try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch (e) { /* 私密瀏覽 */ } };

  /* ---------- 狀態 ---------- */
  let cards = [], index = new CardIndex([]), results = [], sel = -1;
  const filters = { tags: new Set(), review: false, fav: false };
  const thumbs = new Map(); // id → objectURL

  /* ---------- 小工具 ---------- */
  function toast(msg, ms = 2600) {
    const t = $('#toast');
    t.textContent = msg; t.classList.add('on');
    clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('on'), ms);
  }
  function download(content, name, type) {
    const blob = content instanceof Blob ? content : new Blob([content], { type });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }
  const today = () => new Date().toISOString().slice(0, 10);
  const digits = s => String(s || '').replace(/\D/g, '');
  const splitMulti = s => String(s || '').split(/\s*\/\s*/).map(x => x.trim()).filter(Boolean);
  const splitTags = s => [...new Set(String(s || '').split(/[,，、;；]/).map(x => x.trim().replace(/^#/, '')).filter(Boolean))];
  const initial = c => ((c.name || c.company || '?').trim().charAt(0) || '?').toUpperCase();

  /* ---------- 資料載入 ---------- */
  async function refresh() {
    try { cards = await Store.allCards(); }
    catch (e) { cards = []; toast('無法開啟資料庫:' + e.message, 6000); }
    index = new CardIndex(cards);
    updateDatalists();
    render();
  }

  function updateDatalists() {
    const companies = [...new Set(cards.map(c => c.company).filter(Boolean))].sort();
    $('#dl-company').innerHTML = companies.map(c => `<option value="${esc(c)}">`).join('');
    $('#dl-tags').innerHTML = allTags().map(([t]) => `<option value="${esc(t)}">`).join('');
  }
  function allTags() {
    const m = new Map();
    for (const c of cards) for (const t of c.tags || []) m.set(t, (m.get(t) || 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }

  /* ---------- 清單 ---------- */
  function filtered(list) {
    return list.filter(c => (!filters.review || c.needsReview) && (!filters.fav || c.favorite) &&
      [...filters.tags].every(t => (c.tags || []).includes(t)));
  }

  function renderChips() {
    const review = cards.filter(c => c.needsReview).length;
    const fav = cards.filter(c => c.favorite).length;
    const tags = allTags().slice(0, 12);
    for (const t of filters.tags) if (!tags.some(([x]) => x === t)) tags.push([t, 0]);
    $('#chips').innerHTML =
      (review ? `<button type="button" class="chip warn" data-chip="review" aria-pressed="${filters.review}">待確認 ${review}</button>` : '') +
      (fav ? `<button type="button" class="chip" data-chip="fav" aria-pressed="${filters.fav}">★ 星號 ${fav}</button>` : '') +
      tags.map(([t, n]) => `<button type="button" class="chip" data-tag="${esc(t)}" aria-pressed="${filters.tags.has(t)}">#${esc(t)} <small>${n || ''}</small></button>`).join('');
  }

  function cardHtml(c, q, i) {
    const sub = [c.title, c.department, c.company].filter(Boolean);
    const meta = [splitMulti(c.mobile)[0], splitMulti(c.tel)[0], splitMulti(c.email)[0]].filter(Boolean);
    // 搜尋命中但不在顯示欄位的資訊(地址、備註…)也露出來,讓人知道為何被找到
    let extra = '';
    if (q) {
      const terms = parseQuery(q).filter(t => !t.negate);
      for (const k of ['address', 'notes', 'website', 'taxId', 'lineId', 'nameEn', 'companyEn', 'fax']) {
        const v = c[k];
        if (v && terms.some(t => String(v).normalize('NFKC').toLowerCase().includes(t.norm))) { extra = `${FIELD_LABELS[k]}:${highlight(v, q)}`; break; }
      }
    }
    const tags = (c.tags || []).map(t => `<span class="tag">#${highlight(t, q)}</span>`).join('') + (c.needsReview ? '<span class="tag review">待確認</span>' : '');
    return `<button type="button" class="card" data-id="${esc(c.id)}" data-i="${i}">
      <span class="avatar" data-thumb="${c.hasImage ? esc(c.id) : ''}">${esc(initial(c))}</span>
      <span><span class="nm">${highlight(c.name || c.company || '(未命名)', q)}${c.nameEn && c.name !== c.nameEn ? `<small>${highlight(c.nameEn, q)}</small>` : ''}</span>
        ${sub.length ? `<span class="sub" style="display:block">${sub.map(s => highlight(s, q)).join(' · ')}</span>` : ''}
        ${meta.length ? `<span class="meta" style="display:block">${meta.map(s => highlight(s, q)).join(' · ')}</span>` : ''}
        ${extra ? `<span class="meta" style="display:block">${extra}</span>` : ''}
        ${tags ? `<span class="tags">${tags}</span>` : ''}</span>
      ${c.favorite ? '<span class="star" aria-label="星號">★</span>' : '<span></span>'}
    </button>`;
  }

  function render() {
    const q = $('#q').value.trim();
    $('#search-box').classList.toggle('has-q', !!q);
    $('#count').textContent = cards.length ? `${cards.length} 張` : '';
    $$('#group-mode button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.mode === settings.mode)));
    renderChips();
    const main = $('#main');
    if (!cards.length) {
      main.innerHTML = `<div class="empty"><svg class="i big"><use href="#i-card"/></svg><h2>還沒有名片</h2>
        <p>按「掃描名片」用手機拍照,或把名片照片拖曳到這個視窗(可一次多張)。<br>辨識結果可修改後存入,之後用上方搜尋框即時查找。</p>
        <div class="actions"><button class="btn primary" type="button" data-act="scan"><svg class="i"><use href="#i-camera"/></svg>掃描名片</button>
        <button class="btn" type="button" data-act="demo">載入示範名片</button><button class="btn" type="button" data-act="import">匯入備份</button></div></div>`;
      $('#rail').innerHTML = ''; $('#result-info').textContent = ''; results = []; return;
    }
    const t0 = performance.now();
    let groups;
    if (q) {
      const hits = filtered(index.search(q).map(r => r.card));
      groups = hits.length ? [{ key: '最相關', cards: hits }] : [];
    } else groups = groupCards(filtered(cards), settings.mode);
    results = [];
    let html = '';
    for (const g of groups) {
      const items = g.cards.map(c => { results.push(c); return cardHtml(c, q, results.length - 1); }).join('');
      html += `<section class="group" data-key="${esc(g.key)}"><h2>${esc(g.key)} <small>${g.cards.length}</small></h2><div class="cards">${items}</div></section>`;
    }
    const ms = performance.now() - t0;
    main.innerHTML = html || `<div class="empty"><h2>找不到符合的名片</h2><p>試試較短的關鍵字、電話末四碼,或清除標籤篩選。</p></div>`;
    $('#result-info').textContent = q || filters.tags.size || filters.review || filters.fav ? `${results.length} 筆 · ${ms < 1 ? '<1' : ms.toFixed(0)} ms` : '';
    sel = q && results.length ? 0 : -1;
    markSel(false);
    renderRail(q ? [] : groups);
    observeThumbs();
  }

  /* ---------- 縮圖(進入畫面才讀) ---------- */
  const io = 'IntersectionObserver' in self ? new IntersectionObserver(entries => {
    for (const e of entries) if (e.isIntersecting) { io.unobserve(e.target); loadThumb(e.target); }
  }, { rootMargin: '300px' }) : null;
  function observeThumbs() {
    $$('.avatar[data-thumb]:not([data-thumb=""])').forEach(el => (io ? io.observe(el) : loadThumb(el)));
  }
  async function loadThumb(el) {
    const id = el.dataset.thumb;
    let url = thumbs.get(id);
    if (!url) {
      const blob = await Store.getImage(id).catch(() => null);
      if (!blob) return;
      url = URL.createObjectURL(blob); thumbs.set(id, url);
    }
    el.innerHTML = `<img src="${url}" alt="">`;
  }
  function dropThumb(id) { const u = thumbs.get(id); if (u) { URL.revokeObjectURL(u); thumbs.delete(id); } }

  /* ---------- 快速索引列 ---------- */
  function renderRail(groups) {
    const rail = $('#rail');
    if (groups.length < 2) { rail.innerHTML = ''; return; }
    const label = k => settings.mode === 'month' ? k.slice(2).replace('-', '/') : k.startsWith('(') ? '…' : k.slice(0, settings.mode === 'name' ? 1 : 2);
    // 太多組時抽樣顯示,拖曳仍可逐組跳
    const max = Math.max(8, Math.floor((rail.clientHeight || 500) / 17));
    const step = Math.ceil(groups.length / max);
    rail.dataset.keys = JSON.stringify(groups.map(g => g.key));
    rail.innerHTML = groups.filter((g, i) => i % step === 0).map(g => `<button type="button" data-key="${esc(g.key)}" title="${esc(g.key)}">${esc(label(g.key))}</button>`).join('');
  }
  function jumpTo(key, pop) {
    const sec = $$('.group').find(s => s.dataset.key === key);
    if (!sec) return;
    const hdr = $('#top').offsetHeight;
    window.scrollTo({ top: sec.getBoundingClientRect().top + scrollY - hdr - 4 });
    if (pop) { const p = $('#rail-pop'); p.textContent = key.length > 6 ? key.slice(0, 6) + '…' : key; p.classList.add('on'); }
  }
  function railFromPoint(y) {
    const rail = $('#rail');
    const keys = JSON.parse(rail.dataset.keys || '[]');
    if (!keys.length) return null;
    const r = rail.getBoundingClientRect();
    const btns = $$('button', rail);
    const usedH = btns.length ? btns[btns.length - 1].getBoundingClientRect().bottom - r.top : r.height;
    const k = Math.max(0, Math.min(keys.length - 1, Math.floor((y - r.top) / Math.max(1, usedH) * keys.length)));
    return keys[k];
  }
  function setupRail() {
    const rail = $('#rail');
    let dragging = false;
    const move = e => { const k = railFromPoint(e.clientY); if (k) jumpTo(k, true); };
    rail.addEventListener('pointerdown', e => { dragging = true; rail.setPointerCapture(e.pointerId); move(e); e.preventDefault(); });
    rail.addEventListener('pointermove', e => { if (dragging) move(e); });
    const end = () => { dragging = false; setTimeout(() => $('#rail-pop').classList.remove('on'), 350); };
    rail.addEventListener('pointerup', end); rail.addEventListener('pointercancel', end);
  }

  /* ---------- 鍵盤 ---------- */
  function markSel(scroll = true) {
    $$('.card.sel').forEach(el => el.classList.remove('sel'));
    if (sel < 0) return;
    const el = $(`.card[data-i="${sel}"]`);
    if (el) { el.classList.add('sel'); if (scroll) el.scrollIntoView({ block: 'nearest' }); }
  }
  function setupKeys() {
    document.addEventListener('keydown', e => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) && e.target.id !== 'q';
      if (document.querySelector('dialog[open]') || typing) return;
      if (e.key === '/' && e.target.id !== 'q') { e.preventDefault(); $('#q').focus(); $('#q').select(); return; }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (!results.length) return;
        e.preventDefault();
        sel = Math.max(0, Math.min(results.length - 1, sel + (e.key === 'ArrowDown' ? 1 : -1)));
        markSel();
      } else if (e.key === 'Enter' && sel >= 0 && results[sel]) { e.preventDefault(); openCard(results[sel]); }
      else if (e.key === 'Escape' && e.target.id === 'q') { $('#q').value = ''; render(); }
    });
  }

  /* ---------- 掃描 ---------- */
  function jobUi(name) {
    const el = document.createElement('div');
    el.className = 'job';
    el.innerHTML = `<div><b>${esc(name)}</b> <span class="st">準備中…</span></div><div class="bar"><i></i></div>`;
    $('#queue').appendChild(el);
    return {
      progress(status, p) {
        $('.st', el).textContent = statusText(status);
        if (p != null) $('.bar i', el).style.width = Math.round(p * 100) + '%';
      },
      fail(msg) { el.classList.add('err'); $('.st', el).textContent = msg; setTimeout(() => el.remove(), 8000); },
      done() { $('.bar i', el).style.width = '100%'; setTimeout(() => el.remove(), 600); },
    };
  }
  function statusText(s) {
    return ({ 'loading tesseract core': '載入 OCR 引擎', 'initializing tesseract': '初始化', 'loading language traineddata': '下載語言檔(第一次較久)',
      'initializing api': '初始化', 'recognizing text': '辨識文字' })[s] || s || '';
  }

  async function runOcr(prep, job) {
    const onProgress = (s, p) => job && job.progress(s, p);
    if (settings.engine === 'claude') return OCR.claude(prep, { apiKey: settings.apiKey, model: settings.model, onProgress });
    return OCR.tesseract(prep, { langs: settings.langs, onProgress });
  }

  async function scanFiles(files) {
    files = [...files].filter(f => /^image\//.test(f.type));
    if (!files.length) return;
    if (files.length === 1) return scanOne(files[0]);
    // 多張:逐張辨識直接建檔,標為「待確認」
    let ok = 0;
    for (const f of files) {
      const job = jobUi(f.name || '名片');
      try {
        const prep = await OCR.prepare(f);
        const r = await runOcr(prep, job);
        await Store.saveCard({ ...r.fields, tags: [], notes: '', rawText: r.text, needsReview: true, source: settings.engine }, prep.image);
        ok++; job.done();
      } catch (e) { job.fail(e.message || String(e)); }
    }
    await refresh();
    if (ok) { filters.review = true; render(); toast(`已建檔 ${ok} 張,請檢查「待確認」的名片`, 4000); }
  }

  async function scanOne(file) {
    const job = jobUi(file.name || '名片');
    let prep;
    try {
      prep = await OCR.prepare(file);
      const r = await runOcr(prep, job);
      job.done();
      openEditor({ ...r.fields, rawText: r.text, source: settings.engine }, { image: prep.image, isNew: true, ocr: r });
    } catch (e) {
      job.fail(e.message || String(e));
      if (prep) openEditor({}, { image: prep.image, isNew: true, error: '辨識失敗:' + (e.message || e) + '。可手動輸入,或到設定切換 OCR 引擎。' });
    }
  }

  /* ---------- 編輯 ---------- */
  const ed = { card: null, image: null, imageChanged: false, isNew: false, url: null };

  async function openCard(card) {
    const image = card.hasImage ? await Store.getImage(card.id).catch(() => null) : null;
    openEditor(card, { image, isNew: false });
  }

  function openEditor(card, { image, isNew, ocr, error } = {}) {
    ed.card = { ...card }; ed.image = image || null; ed.imageChanged = isNew && !!image; ed.isNew = !!isNew;
    const form = $('#edit-form');
    for (const k of EDIT_FIELDS) {
      const el = form.elements[k];
      el.value = k === 'tags' ? (card.tags || []).join(', ') : card[k] || '';
      el.classList.toggle('ocr-hit', !!(ocr && ocr.fields[k]));
    }
    $('#edit-title').textContent = isNew ? '新增名片' : card.name || card.company || '名片';
    $('#edit-delete').hidden = isNew;
    setFav(!!card.favorite);
    let notice = '';
    if (error) notice = `<div class="notice">${esc(error)}</div>`;
    else if (ocr) {
      const n = Object.keys(ocr.fields).length;
      notice = `<div class="notice ok">已辨識出 ${n} 個欄位(綠底),請核對後儲存。${ocr.unmatched && ocr.unmatched.length ? '未歸類的文字:' + esc(ocr.unmatched.join(' | ')) : ''}</div>`;
    } else if (card.needsReview) notice = '<div class="notice">這張是批次建檔的名片,請核對欄位後按「儲存」。</div>';
    if (!isNew && card.updatedAt) notice += `<div class="hint" style="margin-bottom:8px">建檔 ${new Date(card.createdAt).toLocaleDateString('zh-TW')} · 更新 ${new Date(card.updatedAt).toLocaleString('zh-TW')}</div>`;
    $('#edit-notice').innerHTML = notice;
    $('#edit-raw').textContent = card.rawText || '';
    $('#edit-raw-wrap').hidden = !card.rawText;
    showShot();
    renderQuick(card);
    const dlg = $('#dlg-edit');
    if (!dlg.open) dlg.showModal();
    $('.dlg-body', dlg).scrollTop = 0;
    if (isNew && matchMedia('(pointer:fine)').matches) setTimeout(() => form.elements.name.focus({ preventScroll: true }), 50);
  }

  function showShot() {
    if (ed.url) { URL.revokeObjectURL(ed.url); ed.url = null; }
    const shot = $('#edit-shot');
    if (ed.image) {
      ed.url = URL.createObjectURL(ed.image);
      shot.innerHTML = `<img src="${ed.url}" alt="名片照片">`;
    } else shot.innerHTML = '<div class="none">沒有名片圖片<br>按「換照片」加入</div>';
    $('#edit-rotate').disabled = $('#edit-reocr').disabled = !ed.image;
  }

  function renderQuick(c) {
    const links = [];
    for (const p of splitMulti(c.mobile).concat(splitMulti(c.tel))) links.push(`<a href="tel:${esc(p.replace(/[^\d+#]/g, '').replace('#', ','))}"><svg class="i"><use href="#i-phone"/></svg>${esc(p)}</a>`);
    for (const m of splitMulti(c.email)) links.push(`<a href="mailto:${esc(m)}"><svg class="i"><use href="#i-mail"/></svg>${esc(m)}</a>`);
    if (c.address) links.push(`<a target="_blank" rel="noopener" href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(c.address)}"><svg class="i"><use href="#i-pin"/></svg>地圖</a>`);
    if (c.website) links.push(`<a target="_blank" rel="noopener" href="${esc(/^https?:/i.test(c.website) ? c.website : 'https://' + c.website)}"><svg class="i"><use href="#i-link"/></svg>網站</a>`);
    $('#edit-quick').innerHTML = links.join('');
  }

  function setFav(on) {
    const b = $('#edit-fav');
    b.setAttribute('aria-pressed', String(on)); b.textContent = on ? '★' : '☆'; b.style.color = on ? 'var(--orange)' : '';
    ed.card.favorite = on;
  }

  function readForm() {
    const form = $('#edit-form');
    const c = { ...ed.card };
    for (const k of EDIT_FIELDS) {
      const v = form.elements[k].value.trim();
      if (k === 'tags') c.tags = splitTags(v);
      else if (v) c[k] = v; else delete c[k];
    }
    return c;
  }

  function findDuplicate(c) {
    const emails = splitMulti(c.email).map(e => e.toLowerCase());
    const phones = splitMulti(c.mobile).map(digits).filter(d => d.length >= 8);
    return cards.find(o => o.id !== c.id && (
      emails.some(e => splitMulti(o.email).map(x => x.toLowerCase()).includes(e)) ||
      phones.some(p => splitMulti(o.mobile).map(digits).includes(p)) ||
      (c.name && o.name === c.name && c.company && o.company === c.company)));
  }

  function mergeInto(old, neu) {
    const m = { ...old };
    for (const k of Object.keys(FIELD_LABELS)) {
      if (k === 'tags') { m.tags = [...new Set([...(old.tags || []), ...(neu.tags || [])])]; continue; }
      const nv = neu[k]; if (!nv) continue;
      if (k === 'notes') { m.notes = old.notes && old.notes !== nv ? old.notes + '\n' + nv : nv; continue; }
      if (MULTI.includes(k) && old[k]) {
        const have = splitMulti(old[k]);
        for (const v of splitMulti(nv)) if (!have.some(h => digits(h) === digits(v) && digits(v) ? true : h.toLowerCase() === v.toLowerCase())) have.push(v);
        m[k] = have.join(' / ');
      } else {
        if (old[k] && old[k] !== nv && ['title', 'company', 'department', 'address'].includes(k)) m.notes = (m.notes ? m.notes + '\n' : '') + `舊${FIELD_LABELS[k]}:${old[k]}`;
        m[k] = nv;
      }
    }
    m.rawText = neu.rawText || old.rawText;
    m.favorite = old.favorite || neu.favorite;
    return m;
  }

  async function saveEditor() {
    let c = readForm();
    if (!c.name && !c.company && !c.mobile && !c.tel && !c.email) { toast('請至少填入姓名、公司、電話或 Email 其中一項'); return false; }
    c.needsReview = false;
    let image = ed.imageChanged ? ed.image : null;
    if (ed.isNew) {
      const dup = findDuplicate(c);
      if (dup && confirm(`這張名片看起來和既有的「${dup.name || dup.company}${dup.company && dup.name ? '(' + dup.company + ')' : ''}」是同一人。\n\n按「確定」合併到既有名片(新資料覆蓋、電話與 Email 累加、舊職稱記入備註);\n按「取消」另存一張新名片。`)) {
        c = mergeInto(dup, c);
        if (image) dropThumb(dup.id);
      }
    } else if (image) dropThumb(c.id);
    await Store.saveCard(c, image);
    $('#dlg-edit').close();
    await refresh();
    toast(ed.isNew ? '已存入名片庫' : '已更新');
    return true;
  }

  async function rotateImage() {
    if (!ed.image) return;
    const bmp = await createImageBitmap(ed.image);
    const c = document.createElement('canvas');
    c.width = bmp.height; c.height = bmp.width;
    const g = c.getContext('2d');
    g.translate(c.width / 2, c.height / 2); g.rotate(Math.PI / 2); g.drawImage(bmp, -bmp.width / 2, -bmp.height / 2);
    ed.image = await new Promise(r => c.toBlob(r, 'image/jpeg', 0.88));
    ed.imageChanged = true;
    showShot();
  }

  async function reOcr(file) {
    const src = file || ed.image;
    if (!src) return;
    const job = jobUi('重新辨識');
    try {
      const prep = await OCR.prepare(src);
      if (file) { ed.image = prep.image; ed.imageChanged = true; showShot(); }
      const r = await runOcr(prep, job);
      job.done();
      const form = $('#edit-form');
      let n = 0;
      for (const [k, v] of Object.entries(r.fields)) {
        const el = form.elements[k];
        if (el && v && (!el.value.trim() || confirmOverwrite(k, el.value, v))) { el.value = v; el.classList.add('ocr-hit'); n++; }
      }
      ed.card.rawText = r.text;
      $('#edit-raw').textContent = r.text; $('#edit-raw-wrap').hidden = !r.text;
      toast(`辨識完成,更新 ${n} 個欄位`);
    } catch (e) { job.fail(e.message || String(e)); }
  }
  let overwriteAll = null;
  function confirmOverwrite(k, oldV, newV) {
    if (oldV.trim() === newV.trim()) return false;
    if (overwriteAll === null) overwriteAll = confirm('辨識結果和目前欄位內容不同。要用新的辨識結果覆蓋已填的欄位嗎?\n(取消 = 只補空白欄位)');
    setTimeout(() => { overwriteAll = null; }, 0);
    return overwriteAll;
  }

  /* ---------- 匯出 ---------- */
  const CSV_COLS = ['name', 'nameEn', 'company', 'companyEn', 'department', 'title', 'mobile', 'tel', 'fax', 'email', 'website', 'address', 'taxId', 'lineId', 'tags', 'notes', 'createdAt'];
  function toCsv(list) {
    const q = v => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
    const head = CSV_COLS.map(k => q(k === 'createdAt' ? '建檔日期' : FIELD_LABELS[k])).join(',');
    const rows = list.map(c => CSV_COLS.map(k => q(k === 'tags' ? (c.tags || []).join(', ') : k === 'createdAt' ? (c.createdAt ? new Date(c.createdAt).toISOString().slice(0, 10) : '') : c[k])).join(','));
    return '\ufeff' + [head, ...rows].join('\r\n');
  }
  const COMPOUND = ['歐陽', '司馬', '諸葛', '上官', '張簡', '范姜', '東方', '皇甫', '司徒', '公孫', '令狐', '慕容', '尉遲', '長孫', '夏侯', '端木'];
  function splitName(name) {
    name = (name || '').trim();
    if (/^[\u3400-\u9fff]{2,5}$/.test(name)) {
      const fam = COMPOUND.find(s => name.startsWith(s) && name.length > 2) || name[0];
      return [fam, name.slice(fam.length)];
    }
    const w = name.split(/\s+/);
    return w.length > 1 ? [w[w.length - 1], w.slice(0, -1).join(' ')] : [name, ''];
  }
  const vesc = s => String(s || '').replace(/\\/g, '\\\\').replace(/\n/g, '\\n').replace(/([,;])/g, '\\$1');
  function toVcard(c) {
    const [fam, given] = splitName(c.name || c.nameEn || '');
    const L = ['BEGIN:VCARD', 'VERSION:3.0', `N:${vesc(fam)};${vesc(given)};;;`, `FN:${vesc(c.name || c.nameEn || c.company || '')}`];
    if (c.nameEn && c.nameEn !== c.name) L.push(`X-PHONETIC-FIRST-NAME:${vesc(c.nameEn)}`);
    if (c.company || c.department) L.push(`ORG:${vesc(c.company)}${c.department ? ';' + vesc(c.department) : ''}`);
    if (c.title) L.push(`TITLE:${vesc(c.title)}`);
    for (const p of splitMulti(c.mobile)) L.push(`TEL;TYPE=CELL:${p}`);
    for (const p of splitMulti(c.tel)) L.push(`TEL;TYPE=WORK,VOICE:${p}`);
    for (const p of splitMulti(c.fax)) L.push(`TEL;TYPE=WORK,FAX:${p}`);
    for (const m of splitMulti(c.email)) L.push(`EMAIL;TYPE=INTERNET,WORK:${m}`);
    if (c.website) L.push(`URL:${c.website}`);
    if (c.address) L.push(`ADR;TYPE=WORK:;;${vesc(c.address)};;;;`);
    if (c.tags && c.tags.length) L.push(`CATEGORIES:${c.tags.map(vesc).join(',')}`);
    const note = [c.notes, c.taxId && '統一編號:' + c.taxId, c.lineId && 'LINE:' + c.lineId].filter(Boolean).join('\n');
    if (note) L.push(`NOTE:${vesc(note)}`);
    L.push('END:VCARD');
    return L.join('\r\n');
  }
  async function exportJson() {
    const images = {};
    for (const c of cards) if (c.hasImage) {
      const b = await Store.getImage(c.id).catch(() => null);
      if (b) images[c.id] = 'data:image/jpeg;base64,' + await OCR.blobToBase64(b);
    }
    download(JSON.stringify({ app: 'card-scanner', version: 1, exportedAt: new Date().toISOString(), cards, images }), `名片庫備份-${today()}.json`, 'application/json');
  }

  /* ---------- 匯入 ---------- */
  function parseCsv(text) {
    const rows = []; let row = [], f = '', i = 0, inQ = false;
    text = text.replace(/^\ufeff/, '');
    for (; i < text.length; i++) {
      const ch = text[i];
      if (inQ) { if (ch === '"') { if (text[i + 1] === '"') { f += '"'; i++; } else inQ = false; } else f += ch; }
      else if (ch === '"') inQ = true;
      else if (ch === ',') { row.push(f); f = ''; }
      else if (ch === '\n' || ch === '\r') { if (ch === '\r' && text[i + 1] === '\n') i++; row.push(f); rows.push(row); row = []; f = ''; }
      else f += ch;
    }
    if (f || row.length) { row.push(f); rows.push(row); }
    return rows.filter(r => r.some(x => x.trim()));
  }
  function csvToCards(text) {
    const rows = parseCsv(text);
    if (rows.length < 2) return [];
    const byLabel = Object.fromEntries(Object.entries(FIELD_LABELS).map(([k, v]) => [v.toLowerCase(), k]));
    const extra = { 'first name': 'name', 'e-mail': 'email', 'e-mail address': 'email', mobile: 'mobile', 'mobile phone': 'mobile', phone: 'tel', 'business phone': 'tel',
      company: 'company', 'job title': 'title', 'title': 'title', 'business fax': 'fax', 'web page': 'website', 'notes': 'notes', 'categories': 'tags', 'address': 'address' };
    const keys = rows[0].map(h => { const x = h.trim().toLowerCase(); return byLabel[x] || (FIELD_LABELS[h.trim()] ? h.trim() : extra[x]) || null; });
    return rows.slice(1).map(r => {
      const c = { tags: [] };
      keys.forEach((k, i) => { const v = (r[i] || '').trim(); if (!k || !v) return; if (k === 'tags') c.tags = splitTags(v); else c[k] = c[k] ? c[k] + ' / ' + v : v; });
      return c;
    }).filter(c => c.name || c.company || c.email || c.mobile || c.tel);
  }
  function vcfToCards(text) {
    const out = [];
    const lines = text.replace(/\r\n[ \t]/g, '').replace(/\n[ \t]/g, '').split(/\r?\n/);
    let c = null;
    const un = s => s.replace(/\\n/gi, '\n').replace(/\\([,;\\])/g, '$1');
    for (const line of lines) {
      const m = line.match(/^([^:]+):(.*)$/); if (!m) continue;
      const [prop, ...params] = m[1].split(';'); const P = prop.toUpperCase().replace(/^ITEM\d+\./, ''); const ps = params.join(';').toUpperCase(); const v = m[2];
      if (P === 'BEGIN') c = { tags: [] };
      else if (!c) continue;
      else if (P === 'END') { if (c.name || c.company || c.email || c.mobile || c.tel) out.push(c); c = null; }
      else if (P === 'FN') c.name = un(v);
      else if (P === 'N' && !c.name) { const [fam, giv] = v.split(';'); c.name = /[\u3400-\u9fff]/.test(fam + giv) ? un(fam + giv) : un(`${giv} ${fam}`).trim(); }
      else if (P === 'ORG') { const [o, d] = v.split(/(?<!\\);/); c.company = un(o || ''); if (d) c.department = un(d); }
      else if (P === 'TITLE') c.title = un(v);
      else if (P === 'TEL') { const k = /FAX/.test(ps) ? 'fax' : /CELL|MOBILE|IPHONE/.test(ps) ? 'mobile' : 'tel'; c[k] = c[k] ? c[k] + ' / ' + v : v; }
      else if (P === 'EMAIL') c.email = c.email ? c.email + ' / ' + v : v;
      else if (P === 'URL') c.website = v;
      else if (P === 'ADR') c.address = un(v.split(/(?<!\\);/).filter(Boolean).join(' '));
      else if (P === 'NOTE') c.notes = un(v);
      else if (P === 'CATEGORIES') c.tags = splitTags(un(v));
    }
    return out;
  }
  async function importFile(file) {
    const text = await file.text();
    let list = [], images = new Map();
    try {
      if (/\.json$/i.test(file.name) || /^\s*\{/.test(text)) {
        const data = JSON.parse(text);
        list = Array.isArray(data) ? data : data.cards || [];
        for (const [id, url] of Object.entries(data.images || {})) images.set(id, await (await fetch(url)).blob());
      } else if (/BEGIN:VCARD/i.test(text)) list = vcfToCards(text);
      else list = csvToCards(text);
    } catch (e) { toast('無法讀取檔案:' + e.message, 5000); return; }
    if (!list.length) { toast('檔案裡沒有可匯入的名片'); return; }
    const now = Date.now();
    list = list.map(c => ({ ...c, id: c.id || Store.newId(), tags: c.tags || [], createdAt: c.createdAt || now, updatedAt: c.updatedAt || now, hasImage: !!(c.hasImage && images.has(c.id)) }));
    const existing = new Set(cards.map(c => c.id));
    const overwrite = list.filter(c => existing.has(c.id)).length;
    if (overwrite && !confirm(`其中 ${overwrite} 張和名片庫中的名片相同(同一份備份),要以檔案內容覆蓋嗎?`)) list = list.filter(c => !existing.has(c.id));
    for (const c of list) dropThumb(c.id);
    await Store.putMany(list, images);
    await refresh();
    toast(`已匯入 ${list.length} 張名片`);
  }

  /* ---------- 示範資料(虛構) ---------- */
  const DEMO = [
    { name: '林雅婷', nameEn: 'Ya-Ting Lin', company: '星河光電股份有限公司', department: '採購部', title: '資深採購專員', mobile: '0912-345-678', tel: '03-578-1234 #215', email: 'yating.lin@galaxy-opto.example', address: '新竹市東區園區二路 18 號', tags: ['客戶', '面板'], notes: '2026 半導體展認識,關心 ITO 靶交期' },
    { name: '陳建宏', company: '宏碩真空設備有限公司', title: '業務經理', mobile: '0928-111-222', tel: '06-505-0001', fax: '06-505-0002', email: 'kh.chen@hongshuo-vac.example', website: 'www.hongshuo-vac.example', address: '台南市新市區南科三路 9 號', tags: ['供應商', '設備'] },
    { name: 'Emily Carter', company: 'Northwind Materials Inc.', title: 'Director of Sales, APAC', mobile: '+1 (415) 555-0134', email: 'emily.carter@northwind.example', address: '500 Market Street, Suite 12, San Francisco, CA', tags: ['海外', '客戶'] },
    { name: '王志明', company: '晶達半導體股份有限公司', department: '製程整合處', title: '副理', tel: '03-563-6688 #7721', email: 'cmwang@jingda-semi.example', taxId: '24681357', tags: ['客戶', '半導體'], favorite: true, notes: '簡稱晶達,PVD 製程窗口' },
    { name: '佐藤健', nameEn: 'Ken Sato', company: '株式会社サンプル金属', title: '営業部 課長', tel: '+81-3-1234-5678', email: 'k.sato@sample-kinzoku.example', tags: ['日本', '供應商'] },
    { name: '張簡美玲', company: '鈺豐貿易有限公司', title: '負責人', mobile: '0933-246-810', lineId: 'meiling.trade', email: 'meiling@yufeng-trade.example', address: '台中市西屯區工業區一路 100 號 5 樓', tags: ['代理商'] },
    { name: '李國華', company: '清研材料研究所', department: '薄膜實驗室', title: '研究員', tel: '03-591-0000 #33', email: 'kh.lee@tsing-lab.example', tags: ['學研'] },
    { name: 'Daniel Kim', nameEn: 'Daniel Kim', company: 'Hanbit Display Co., Ltd.', title: 'Purchasing Manager', mobile: '+82-10-2345-6789', email: 'daniel.kim@hanbit.example', tags: ['海外', '面板', '客戶'] },
  ];
  async function loadDemo() {
    const base = Date.now() - 86400000 * 120;
    const list = DEMO.map((c, i) => ({ ...c, id: Store.newId(), createdAt: base + i * 86400000 * 17, updatedAt: base + i * 86400000 * 17, demo: true }));
    await Store.putMany(list);
    await refresh();
    toast('已載入 8 張虛構的示範名片,可在設定中清除');
  }

  /* ---------- 設定 ---------- */
  async function openSettings() {
    const f = $('#dlg-settings form');
    f.elements.engine.value = settings.engine; f.elements.langs.value = settings.langs;
    f.elements.apiKey.value = settings.apiKey; f.elements.model.value = settings.model;
    const est = await Store.estimate();
    $('#set-storage').textContent = `名片 ${cards.length} 張` + (est ? `,已使用約 ${(est.usage / 1048576).toFixed(1)} MB` : '') +
      '。資料只存在這台裝置的瀏覽器,清除瀏覽資料會一併刪除,請定期「完整備份」。';
    $('#dlg-settings').showModal();
  }

  /* ---------- 事件 ---------- */
  function bind() {
    const q = $('#q');
    let t;
    q.addEventListener('input', () => { clearTimeout(t); t = setTimeout(render, 60); });
    $('#q-clear').onclick = () => { q.value = ''; render(); q.focus(); };
    $('#group-mode').onclick = e => { const b = e.target.closest('button'); if (!b) return; settings.mode = b.dataset.mode; saveSettings(); render(); scrollTo({ top: 0 }); };
    $('#chips').onclick = e => {
      const b = e.target.closest('.chip'); if (!b) return;
      if (b.dataset.chip) filters[b.dataset.chip] = !filters[b.dataset.chip];
      else { const tg = b.dataset.tag; filters.tags.has(tg) ? filters.tags.delete(tg) : filters.tags.add(tg); }
      render();
    };
    $('#main').addEventListener('click', e => {
      const c = e.target.closest('.card');
      if (c) { const card = cards.find(x => x.id === c.dataset.id); if (card) { sel = +c.dataset.i; markSel(false); openCard(card); } return; }
      const a = e.target.closest('[data-act]'); if (a) act(a.dataset.act);
    });
    $('#rail').addEventListener('click', e => { const b = e.target.closest('button'); if (b) jumpTo(b.dataset.key); });
    $('#btn-scan').onclick = () => act('scan');
    $('#btn-new').onclick = () => openEditor({ tags: [] }, { isNew: true });
    $('#file-scan').onchange = e => { scanFiles(e.target.files); e.target.value = ''; };
    $('#file-import').onchange = e => { if (e.target.files[0]) importFile(e.target.files[0]); e.target.value = ''; };
    const menu = $('#menu');
    $('#btn-menu').onclick = e => { e.stopPropagation(); const o = !menu.classList.contains('open'); menu.classList.toggle('open', o); $('#btn-menu').setAttribute('aria-expanded', String(o)); };
    document.addEventListener('click', e => { if (!menu.contains(e.target)) menu.classList.remove('open'); });
    $('.menu-list').onclick = e => { const b = e.target.closest('[data-act]'); if (b) { menu.classList.remove('open'); act(b.dataset.act); } };

    // 對話框
    $$('dialog [data-close]').forEach(b => (b.onclick = () => b.closest('dialog').close()));
    $('#dlg-edit').addEventListener('close', () => { if (ed.url) { URL.revokeObjectURL(ed.url); ed.url = null; } });
    $('#edit-form').addEventListener('submit', e => { e.preventDefault(); saveEditor(); });
    $('#edit-fav').onclick = () => setFav(!ed.card.favorite);
    $('#edit-rotate').onclick = rotateImage;
    $('#edit-reocr').onclick = () => reOcr();
    $('#edit-photo').onclick = () => {
      const inp = document.createElement('input'); inp.type = 'file'; inp.accept = 'image/*';
      inp.onchange = () => inp.files[0] && reOcr(inp.files[0]); inp.click();
    };
    $('#edit-shot').onclick = e => { if (e.target.tagName === 'IMG') { $('#zoom img').src = e.target.src; $('#zoom').classList.add('on'); } };
    $('#zoom').onclick = () => $('#zoom').classList.remove('on');
    $('#edit-delete').onclick = async () => {
      if (!confirm(`確定刪除「${ed.card.name || ed.card.company || '這張名片'}」?`)) return;
      await Store.deleteCard(ed.card.id); dropThumb(ed.card.id);
      $('#dlg-edit').close(); await refresh(); toast('已刪除');
    };
    $('#edit-vcf').onclick = () => { const c = readForm(); download(toVcard(c), `${c.name || c.company || 'card'}.vcf`, 'text/vcard'); };
    $('#edit-fields').addEventListener('input', e => { e.target.classList.remove('ocr-hit'); renderQuick(readForm()); });

    const sf = $('#dlg-settings form');
    sf.addEventListener('submit', e => {
      e.preventDefault();
      settings.engine = sf.elements.engine.value || 'tesseract'; settings.langs = sf.elements.langs.value;
      settings.apiKey = sf.elements.apiKey.value.trim(); settings.model = sf.elements.model.value;
      if (settings.engine === 'claude' && !settings.apiKey) { toast('使用 Claude 需要輸入 API 金鑰'); return; }
      saveSettings(); $('#dlg-settings').close(); toast('設定已儲存');
    });
    $('#set-persist').onclick = async () => toast((await Store.persist()) ? '瀏覽器已同意永久保存,不會因空間不足自動清除' : '瀏覽器未同意(可把網頁加入主畫面後再試)', 4000);
    $('#set-demo').onclick = async () => { $('#dlg-settings').close(); await loadDemo(); };
    $('#set-clear').onclick = async () => {
      if (!confirm(`確定刪除全部 ${cards.length} 張名片?此動作無法復原,建議先「完整備份」。`)) return;
      await Store.clearAll(); thumbs.forEach(u => URL.revokeObjectURL(u)); thumbs.clear();
      $('#dlg-settings').close(); await refresh(); toast('已清除');
    };

    // 拖放與貼上
    let dragN = 0;
    addEventListener('dragenter', e => { if ([...(e.dataTransfer && e.dataTransfer.types || [])].includes('Files')) { dragN++; document.body.classList.add('dragging'); } });
    addEventListener('dragleave', () => { if (--dragN <= 0) { dragN = 0; document.body.classList.remove('dragging'); } });
    addEventListener('dragover', e => e.preventDefault());
    addEventListener('drop', e => { e.preventDefault(); dragN = 0; document.body.classList.remove('dragging'); if (e.dataTransfer.files.length) scanFiles(e.dataTransfer.files); });
    addEventListener('paste', e => {
      if (document.querySelector('dialog[open]')) return;
      const files = [...(e.clipboardData && e.clipboardData.files || [])].filter(f => /^image\//.test(f.type));
      if (files.length) { e.preventDefault(); scanFiles(files); }
    });

    // 表頭高度 → 分組標題黏附位置
    const setHdr = () => document.documentElement.style.setProperty('--hdr', $('#top').offsetHeight + 'px');
    setHdr();
    if ('ResizeObserver' in self) new ResizeObserver(setHdr).observe($('#top'));
    addEventListener('resize', () => renderRail(railGroups()));
  }
  function railGroups() {
    return $('#q').value.trim() ? [] : $$('.group').map(s => ({ key: s.dataset.key }));
  }

  function act(a) {
    if (a === 'scan') $('#file-scan').click();
    else if (a === 'import') $('#file-import').click();
    else if (a === 'demo') loadDemo();
    else if (a === 'settings') openSettings();
    else if (a === 'export-csv' || a === 'export-vcf' || a === 'export-json') {
      if (!cards.length) { toast('名片庫是空的'); return; }
      const list = results.length && ($('#q').value.trim() || filters.tags.size || filters.review || filters.fav) ? results : cards;
      const scope = list === cards ? '全部' : `目前篩選的 ${list.length} 張`;
      if (a === 'export-csv') download(toCsv(list), `名片庫-${today()}.csv`, 'text/csv;charset=utf-8');
      else if (a === 'export-vcf') download(list.map(toVcard).join('\r\n'), `名片庫-${today()}.vcf`, 'text/vcard;charset=utf-8');
      else exportJson();
      toast(a === 'export-json' ? '已產生完整備份' : `已匯出${scope}`);
    }
  }

  // 給測試與除錯用
  self.CardApp = { toCsv, toVcard, csvToCards, vcfToCards, mergeInto, findDuplicate: c => findDuplicate(c), refresh };

  bind(); setupRail(); setupKeys();
  refresh();
})();
