/* 名片資料庫:瀏覽器 IndexedDB。名片資料(cards)與圖片(images,Blob)分開存,清單載入不必讀圖。
 * 資料只存在這台裝置的這個瀏覽器;換裝置請用「匯出 → 備份 JSON」再匯入。 */
(function (root) {
  'use strict';
  const DB_NAME = 'card-scanner', VERSION = 1;
  let dbp = null;

  function open() {
    if (dbp) return dbp;
    dbp = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains('cards')) {
          const s = db.createObjectStore('cards', { keyPath: 'id' });
          s.createIndex('updatedAt', 'updatedAt');
          s.createIndex('company', 'company');
        }
        if (!db.objectStoreNames.contains('images')) db.createObjectStore('images');
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
      req.onblocked = () => reject(new Error('資料庫被其他分頁占用,請關閉其他分頁後重新整理'));
    });
    return dbp;
  }

  function tx(stores, mode, fn) {
    return open().then(db => new Promise((resolve, reject) => {
      const t = db.transaction(stores, mode);
      let result;
      Promise.resolve(fn(t)).then(r => { result = r; }, reject);
      t.oncomplete = () => resolve(result);
      t.onerror = () => reject(t.error);
      t.onabort = () => reject(t.error || new Error('交易中止'));
    }));
  }
  const req2p = r => new Promise((resolve, reject) => { r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });

  function newId() {
    return (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));
  }

  const Store = {
    newId,
    allCards: () => tx(['cards'], 'readonly', t => req2p(t.objectStore('cards').getAll())),
    getCard: id => tx(['cards'], 'readonly', t => req2p(t.objectStore('cards').get(id))),
    /** 新增或更新名片;image(Blob)有給才寫入圖片 */
    saveCard(card, image) {
      const now = Date.now();
      card.id = card.id || newId();
      card.createdAt = card.createdAt || now;
      card.updatedAt = now;
      card.hasImage = card.hasImage || !!image;
      return tx(['cards', 'images'], 'readwrite', t => {
        t.objectStore('cards').put(card);
        if (image) t.objectStore('images').put(image, card.id);
        return card;
      });
    },
    putMany(cards, images) { // 匯入用:保留原本的時間戳
      return tx(['cards', 'images'], 'readwrite', t => {
        const s = t.objectStore('cards'), im = t.objectStore('images');
        for (const c of cards) {
          s.put(c);
          if (images && images.has(c.id)) im.put(images.get(c.id), c.id);
        }
        return cards.length;
      });
    },
    deleteCard: id => tx(['cards', 'images'], 'readwrite', t => { t.objectStore('cards').delete(id); t.objectStore('images').delete(id); }),
    getImage: id => tx(['images'], 'readonly', t => req2p(t.objectStore('images').get(id))),
    clearAll: () => tx(['cards', 'images'], 'readwrite', t => { t.objectStore('cards').clear(); t.objectStore('images').clear(); }),
    async estimate() {
      try { return navigator.storage && navigator.storage.estimate ? await navigator.storage.estimate() : null; } catch (e) { return null; }
    },
    async persist() {
      try { return navigator.storage && navigator.storage.persist ? await navigator.storage.persist() : false; } catch (e) { return false; }
    },
  };

  root.CardStore = Store;
})(typeof self !== 'undefined' ? self : this);
