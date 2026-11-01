/* 名片快速索引:記憶體中的倒排索引(中文 1/2-gram、英文前綴、電話數字子字串),
 * 先以索引挑出候選,再逐欄驗證、加權排序。瀏覽器與 Node(測試)共用。 */
(function (root) {
  'use strict';

  const WEIGHTS = { name: 10, nameEn: 8, company: 6, companyEn: 5, tags: 5, email: 4, mobile: 4, tel: 3, title: 3, department: 3,
    taxId: 3, lineId: 2, website: 2, address: 2, fax: 1, notes: 1, rawText: 0.3 };
  const PHONE_FIELDS = ['mobile', 'tel', 'fax'];

  // 查詢語法的欄位別名:company:台積、公司:台積、#客戶(標籤)
  const ALIASES = {
    name: ['name', 'n', '姓名', '名字'], company: ['company', 'c', 'co', '公司'], title: ['title', 't', '職稱'],
    department: ['dept', 'department', '部門'], tags: ['tag', 'tags', '標籤'], email: ['email', 'mail', 'e', '信箱'],
    phone: ['phone', 'tel', 'p', '電話', '手機'], address: ['addr', 'address', 'a', '地址'], notes: ['note', 'notes', '備註'],
  };
  const ALIAS_MAP = {};
  for (const [k, list] of Object.entries(ALIASES)) for (const a of list) ALIAS_MAP[a] = k;

  const reCJK = /[㐀-鿿豈-﫿]/;
  const collator = new Intl.Collator('zh-Hant-TW', { numeric: true, sensitivity: 'base' });

  function norm(s) {
    return String(s == null ? '' : s).normalize('NFKC').toLowerCase().replace(/\s+/g, ' ').trim();
  }
  const digitsOf = s => String(s || '').replace(/\D/g, '');

  function fieldText(card, f) {
    const v = card[f];
    return Array.isArray(v) ? v.join(' ') : v;
  }

  /** 把一段文字切成索引用的 token */
  function tokenize(text, out) {
    const t = norm(text);
    // 中文:單字與相鄰兩字
    const cjkRuns = t.match(/[㐀-鿿豈-﫿]+/g) || [];
    for (const run of cjkRuns) {
      for (let i = 0; i < run.length; i++) {
        out.add(run[i]);
        if (i + 1 < run.length) out.add(run.slice(i, i + 2));
      }
    }
    // 英數字:每個字的前綴(最多 12 字元)
    const words = t.match(/[a-z0-9][a-z0-9'&+]*/g) || [];
    for (const w of words) for (let i = 1; i <= Math.min(w.length, 12); i++) out.add('w:' + w.slice(0, i));
    return out;
  }

  /** 查詢字詞 → 必須都出現的 token(用來從索引取候選) */
  function queryTokens(term) {
    const t = norm(term);
    const toks = [];
    const cjkRuns = t.match(/[㐀-鿿豈-﫿]+/g) || [];
    for (const run of cjkRuns) {
      if (run.length === 1) toks.push(run);
      for (let i = 0; i + 1 < run.length; i++) toks.push(run.slice(i, i + 2));
    }
    const words = t.match(/[a-z0-9][a-z0-9'&+]*/g) || [];
    for (const w of words) toks.push('w:' + w.slice(0, 12));
    return toks;
  }

  function parseQuery(q) {
    const terms = [];
    const re = /(-?)(?:([^\s:"]+):)?(?:"([^"]+)"|(\S+))/g;
    let m;
    while ((m = re.exec(String(q || '')))) {
      let field = m[2] ? ALIAS_MAP[m[2].toLowerCase()] : null;
      let text = m[3] || m[4] || '';
      if (m[2] && !field) text = m[2] + ':' + text; // 不認得的前綴當一般文字(例如網址)
      if (!field && text.startsWith('#') && text.length > 1) { field = 'tags'; text = text.slice(1); }
      if (!text) continue;
      terms.push({ field, text, negate: m[1] === '-', norm: norm(text), digits: /^[\d\s\-+()]{3,}$/.test(text) ? digitsOf(text) : '' });
    }
    return terms;
  }

  class CardIndex {
    constructor(cards) {
      this.postings = new Map(); // token → Set(id)
      this.docs = new Map();     // id → {card, f: {field: normalized}, phones: 'digits digits'}
      if (cards) for (const c of cards) this.add(c);
    }
    get size() { return this.docs.size; }

    add(card) {
      if (this.docs.has(card.id)) this.remove(card.id);
      const f = {};
      const toks = new Set();
      for (const k of Object.keys(WEIGHTS)) {
        const v = fieldText(card, k);
        if (!v) continue;
        f[k] = norm(v);
        tokenize(v, toks);
      }
      const phones = PHONE_FIELDS.map(k => String(card[k] || '').split('/').map(digitsOf).filter(Boolean).join(' ')).filter(Boolean).join(' ');
      // 電話:所有長度 ≥3 的數字子字串都進索引,輸入末幾碼也找得到
      for (const d of phones.split(' ')) {
        for (let i = 0; i < d.length; i++) for (let j = i + 3; j <= d.length; j++) toks.add('d:' + d.slice(i, j));
      }
      for (const t of toks) {
        let s = this.postings.get(t);
        if (!s) this.postings.set(t, (s = new Set()));
        s.add(card.id);
      }
      this.docs.set(card.id, { card, f, phones, toks });
    }

    remove(id) {
      const d = this.docs.get(id);
      if (!d) return;
      for (const t of d.toks) {
        const s = this.postings.get(t);
        if (s) { s.delete(id); if (!s.size) this.postings.delete(t); }
      }
      this.docs.delete(id);
    }

    candidates(term) {
      if (term.digits.length >= 3) { // 電話數字 ∪ 其他欄位的數字(統編、地址)
        const a = this.postings.get('d:' + term.digits) || new Set();
        const b = this._textCandidates(term);
        return a.size ? (b.size ? new Set([...a, ...b]) : a) : b;
      }
      const toks = queryTokens(term.text);
      if (!toks.length) return null; // 沒有可索引的字元(例如純符號):全部候選,交給驗證
      let best = null;
      for (const t of toks) {
        const s = this.postings.get(t);
        if (!s) return new Set();
        if (!best || s.size < best.size) best = s;
      }
      return best;
    }
    _textCandidates(term) { // 數字查詢在電話找不到時,仍可能出現在統編、地址
      const toks = queryTokens(term.text);
      let best = null;
      for (const t of toks) { const s = this.postings.get(t); if (!s) return new Set(); if (!best || s.size < best.size) best = s; }
      return best || new Set();
    }

    scoreTerm(doc, term) {
      let score = 0;
      const fields = term.field === 'phone' ? PHONE_FIELDS : term.field ? [term.field] : Object.keys(WEIGHTS);
      for (const k of fields) {
        const v = doc.f[k];
        if (!v) continue;
        const i = v.indexOf(term.norm);
        if (i < 0) continue;
        let s = WEIGHTS[k];
        if (v === term.norm) s *= 3;
        else if (i === 0 || v[i - 1] === ' ' || v[i - 1] === '/') s *= 2;
        score += s;
      }
      if (term.digits && (!term.field || term.field === 'phone') && doc.phones.includes(term.digits)) {
        score += doc.phones.split(' ').some(p => p.endsWith(term.digits)) ? 8 : 4;
      }
      return score;
    }

    /** 搜尋;回傳依分數排序的 [{card, score}]。空查詢回傳全部。 */
    search(query, { limit = Infinity } = {}) {
      const terms = parseQuery(query);
      const pos = terms.filter(t => !t.negate);
      const neg = terms.filter(t => t.negate);
      let ids;
      if (!pos.length) ids = [...this.docs.keys()];
      else {
        const sets = pos.map(t => this.candidates(t));
        const known = sets.filter(Boolean).sort((a, b) => a.size - b.size);
        ids = known.length ? [...known[0]].filter(id => known.every(s => s.has(id))) : [...this.docs.keys()];
      }
      const out = [];
      for (const id of ids) {
        const doc = this.docs.get(id);
        let total = 0, ok = true;
        for (const t of pos) {
          const s = this.scoreTerm(doc, t);
          if (!s) { ok = false; break; }
          total += s;
        }
        if (!ok) continue;
        if (neg.some(t => this.scoreTerm(doc, t) > 0)) continue;
        if (doc.card.favorite) total += 0.5;
        out.push({ card: doc.card, score: total });
      }
      out.sort((a, b) => b.score - a.score || compareCards(a.card, b.card));
      return limit < out.length ? out.slice(0, limit) : out;
    }
  }

  function compareCards(a, b) {
    return collator.compare(a.name || a.company || '', b.name || b.company || '');
  }

  /** 快速索引分組:姓名首字(英文依字母)、公司、標籤、建立月份 */
  function groupKeys(card, mode) {
    if (mode === 'company') return [card.company || '(未填公司)'];
    if (mode === 'tag') return card.tags && card.tags.length ? card.tags : ['(無標籤)'];
    if (mode === 'month') return [card.createdAt ? new Date(card.createdAt).toISOString().slice(0, 7) : '(未知)'];
    const ch = (card.name || card.company || '').trim().charAt(0);
    if (!ch) return ['#'];
    if (/[a-z]/i.test(ch)) return [ch.toUpperCase()];
    if (reCJK.test(ch)) return [ch];
    return ['#'];
  }

  function groupCards(cards, mode) {
    const groups = new Map();
    for (const c of cards) for (const k of groupKeys(c, mode)) {
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(c);
    }
    const keys = [...groups.keys()].sort((a, b) => {
      if (mode === 'month') return b.localeCompare(a);
      const ra = a.startsWith('(') || a === '#' ? 2 : /^[a-z]/i.test(a) ? 0 : 1;
      const rb = b.startsWith('(') || b === '#' ? 2 : /^[a-z]/i.test(b) ? 0 : 1;
      return ra - rb || collator.compare(a, b);
    });
    return keys.map(k => ({ key: k, cards: groups.get(k).sort(compareCards) }));
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  /** 以 <mark> 標出查詢字詞(不分大小寫、全半形) */
  function highlight(text, query) {
    const s = String(text == null ? '' : text);
    const terms = (Array.isArray(query) ? query : parseQuery(query)).filter(t => !t.negate && t.text);
    if (!terms.length || !s) return escapeHtml(s);
    const n = s.normalize('NFKC').toLowerCase();
    const marks = new Array(s.length).fill(false);
    const sameLen = n.length === s.length;
    for (const t of terms) {
      if (!sameLen) break;
      let i = n.indexOf(t.norm);
      while (i >= 0 && t.norm) { for (let k = i; k < i + t.norm.length; k++) marks[k] = true; i = n.indexOf(t.norm, i + 1); }
      if (t.digits && /\d/.test(s)) { // 電話:忽略分隔符號比對數字
        const pos = [];
        for (let k = 0; k < s.length; k++) if (/\d/.test(s[k])) pos.push(k);
        const ds = pos.map(k => s[k]).join('');
        let j = ds.indexOf(t.digits);
        while (j >= 0) { for (let k = pos[j]; k <= pos[j + t.digits.length - 1]; k++) marks[k] = true; j = ds.indexOf(t.digits, j + 1); }
      }
    }
    let out = '', open = false;
    for (let k = 0; k < s.length; k++) {
      if (marks[k] && !open) { out += '<mark>'; open = true; }
      if (!marks[k] && open) { out += '</mark>'; open = false; }
      out += escapeHtml(s[k]);
    }
    return out + (open ? '</mark>' : '');
  }

  const api = { CardIndex, parseQuery, groupCards, groupKeys, highlight, escapeHtml, compareCards, norm };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CardSearch = api;
})(typeof self !== 'undefined' ? self : this);
