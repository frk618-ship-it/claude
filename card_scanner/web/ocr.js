/* 影像前處理與 OCR 引擎:
 *  - tesseract:Tesseract.js 在瀏覽器內辨識(繁中+英文),免金鑰;第一次使用會下載語言檔(約 20 MB,之後快取)。
 *  - claude:Claude 視覺模型直接讀名片並回傳結構化欄位,準確度高,需自己的 Anthropic API 金鑰(只存在這台裝置)。 */
(function (root) {
  'use strict';
  const TESSERACT_URL = 'https://cdn.jsdelivr.net/npm/tesseract.js@7.0.0/dist/tesseract.min.js';
  const ANTHROPIC_SDK_URL = 'https://cdn.jsdelivr.net/npm/@anthropic-ai/sdk@0.135.0/+esm';
  const FALLBACK_MODELS = ['claude-opus-5-5', 'claude-sonnet-5-5', 'claude-fable-5-1'];

  /* ---------- 影像 ---------- */
  async function loadBitmap(file) {
    if (root.createImageBitmap) {
      try { return await createImageBitmap(file, { imageOrientation: 'from-image' }); } catch (e) { /* 舊瀏覽器退回 <img> */ }
    }
    const url = URL.createObjectURL(file);
    try {
      const img = new Image();
      img.src = url;
      await img.decode();
      return img;
    } finally { URL.revokeObjectURL(url); }
  }

  /** 縮放、旋轉;回傳 canvas。rotate 為 0/90/180/270 */
  function drawScaled(src, maxSide, rotate) {
    const w = src.width, h = src.height;
    const k = Math.min(1, maxSide / Math.max(w, h));
    const sw = Math.round(w * k), sh = Math.round(h * k);
    const swap = rotate === 90 || rotate === 270;
    const c = document.createElement('canvas');
    c.width = swap ? sh : sw; c.height = swap ? sw : sh;
    const g = c.getContext('2d');
    g.translate(c.width / 2, c.height / 2);
    g.rotate((rotate || 0) * Math.PI / 180);
    g.drawImage(src, -sw / 2, -sh / 2, sw, sh);
    return c;
  }

  /** OCR 用:灰階 + 自動對比拉伸,讓 Tesseract 對淺色字、彩色底更穩 */
  function enhanceForOcr(canvas) {
    const g = canvas.getContext('2d');
    const im = g.getImageData(0, 0, canvas.width, canvas.height);
    const d = im.data, hist = new Uint32Array(256);
    for (let i = 0; i < d.length; i += 4) {
      const y = (d[i] * 299 + d[i + 1] * 587 + d[i + 2] * 114) / 1000 | 0;
      d[i] = y; hist[y]++;
    }
    const n = d.length / 4;
    let lo = 0, hi = 255, acc = 0;
    while (lo < 255 && (acc += hist[lo]) < n * 0.01) lo++;
    acc = 0;
    while (hi > 0 && (acc += hist[hi]) < n * 0.01) hi--;
    const span = Math.max(1, hi - lo);
    for (let i = 0; i < d.length; i += 4) {
      const v = Math.max(0, Math.min(255, (d[i] - lo) * 255 / span));
      d[i] = d[i + 1] = d[i + 2] = v;
    }
    g.putImageData(im, 0, 0);
    return canvas;
  }

  const toBlob = (canvas, type, q) => new Promise(r => canvas.toBlob(r, type, q));

  /** 讀入照片 → {image: 存檔用 JPEG Blob, thumb: 縮圖 Blob, bitmap} */
  async function prepare(file, rotate = 0) {
    const bmp = await loadBitmap(file);
    const image = await toBlob(drawScaled(bmp, 1600, rotate), 'image/jpeg', 0.85);
    return { image, bitmap: bmp, rotate };
  }

  /* ---------- Tesseract ---------- */
  let tessWorker = null, tessLangs = '';
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      if ([...document.scripts].some(s => s.src === src)) return resolve();
      const s = document.createElement('script');
      s.src = src; s.async = true; s.crossOrigin = 'anonymous';
      s.onload = resolve;
      s.onerror = () => reject(new Error('無法載入 OCR 引擎(需要網路連線下載一次)'));
      document.head.appendChild(s);
    });
  }

  async function tesseract(prep, { langs = 'chi_tra', onProgress } = {}) {
    await loadScript(TESSERACT_URL);
    if (!tessWorker || tessLangs !== langs) {
      if (tessWorker) await tessWorker.terminate();
      tessWorker = await root.Tesseract.createWorker(langs.split('+'), 1, {
        logger: m => onProgress && onProgress(m.status, m.progress),
      });
      // PSM 11(稀疏文字):名片常是左右分欄、大小字混排,逐塊找字比預設的單一區塊(PSM 6)準
      await tessWorker.setParameters({ preserve_interword_spaces: '1', tessedit_pageseg_mode: '11' });
      tessLangs = langs;
    }
    const canvas = enhanceForOcr(drawScaled(prep.bitmap, 2400, prep.rotate));
    const { data } = await tessWorker.recognize(canvas);
    const text = data.text || '';
    const parsed = root.CardParser.parseCardText(text);
    return { text, fields: parsed.fields, unmatched: parsed.unmatched, confidence: data.confidence };
  }

  /* ---------- Claude ---------- */
  const CLAUDE_FIELDS = ['name', 'nameEn', 'company', 'companyEn', 'department', 'title', 'mobile', 'tel', 'fax',
    'email', 'website', 'address', 'taxId', 'lineId', 'other', 'rawText'];
  const SCHEMA = {
    type: 'object',
    properties: Object.fromEntries(CLAUDE_FIELDS.map(k => [k, { type: 'string' }])),
    required: CLAUDE_FIELDS,
    additionalProperties: false,
  };
  const PROMPT = `這是一張名片的照片(可能是中文、英文或日文,也可能正反面都在同一張圖)。請讀出名片上的所有資訊,填入 JSON 欄位:
- name:姓名(中文名優先;只有外文名就填外文名)。nameEn:英文/外文姓名(和 name 不同時才填)。
- company:公司全名(中文優先)。companyEn:公司英文名。department:部門。title:職稱(多個用「 / 」分隔)。
- mobile:手機;tel:市話/公司電話(含分機寫成「 #123」);fax:傳真。多個號碼用「 / 」分隔,保留原本的區碼與 + 國碼格式。
- email、website、address(完整地址,中文地址優先)、taxId(統一編號)、lineId。
- other:名片上其他有用但不屬於上述欄位的資訊(例如服務項目、其他社群帳號),沒有就空字串。
- rawText:名片上所有文字,依原本排列逐行抄寫。
看不清楚或名片上沒有的欄位填空字串,不要猜測或補造。`;

  async function claude(prep, { apiKey, model = 'claude-opus-5-5', onProgress } = {}) {
    if (!apiKey) throw new Error('請先在「設定」輸入 Anthropic API 金鑰');
    onProgress && onProgress('載入 Claude SDK', 0.1);
    let mod;
    try { mod = await import(ANTHROPIC_SDK_URL); } catch (e) { throw new Error('無法載入 Claude SDK(需要網路連線)'); }
    const Anthropic = mod.default || mod.Anthropic;
    const client = new Anthropic({ apiKey, dangerouslyAllowBrowser: true, maxRetries: 2 });
    const data = await blobToBase64(prep.image);
    onProgress && onProgress('Claude 辨識中', 0.4);
    const params = {
      model,
      max_tokens: 8000,
      output_config: { effort: 'low', format: { type: 'json_schema', schema: SCHEMA } },
      messages: [{
        role: 'user',
        content: [
          { type: 'image', source: { type: 'base64', media_type: 'image/jpeg', data } },
          { type: 'text', text: PROMPT },
        ],
      }],
    };
    if (FALLBACK_MODELS.includes(model)) { // 模型婉拒時由伺服器改用備援模型重跑
      params.betas = ['server-side-fallback-2026-07-01'];
      params.fallbacks = 'default';
    }
    let msg;
    try {
      msg = await client.beta.messages.create(params);
    } catch (e) {
      if (e instanceof Anthropic.AuthenticationError) throw new Error('API 金鑰無效,請到「設定」確認');
      if (e instanceof Anthropic.PermissionDeniedError) throw new Error('這把金鑰沒有使用此模型的權限');
      if (e instanceof Anthropic.RateLimitError) throw new Error('呼叫太頻繁或額度不足,請稍後再試');
      if (e instanceof Anthropic.BadRequestError) throw new Error('請求被拒:' + (e.message || '格式錯誤'));
      if (e instanceof Anthropic.APIConnectionError) throw new Error('連不上 Claude API,請檢查網路');
      throw e;
    }
    if (msg.stop_reason === 'refusal') throw new Error('Claude 無法處理這張圖片,請改用本機 OCR');
    if (msg.stop_reason === 'max_tokens') throw new Error('回應被截斷,請再試一次');
    const block = msg.content.find(b => b.type === 'text');
    if (!block) throw new Error('Claude 沒有回傳內容');
    const out = JSON.parse(block.text);
    const fields = {};
    for (const k of CLAUDE_FIELDS) if (k !== 'rawText' && k !== 'other' && out[k]) fields[k] = String(out[k]).trim();
    onProgress && onProgress('完成', 1);
    return { text: out.rawText || '', fields, unmatched: out.other ? [out.other] : [], model: msg.model };
  }

  function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result).split(',')[1]);
      r.onerror = () => reject(r.error);
      r.readAsDataURL(blob);
    });
  }

  root.CardOCR = { prepare, tesseract, claude, blobToBase64, CLAUDE_FIELDS };
})(typeof self !== 'undefined' ? self : this);
