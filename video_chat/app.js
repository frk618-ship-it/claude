// Lumi 視訊交友:純前端示範。主播資料為隨機產生的虛構資料,狀態存在 localStorage。
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];

  // ---------- storage ----------
  const store = {
    get(k, d) { try { const v = localStorage.getItem("lumi:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("lumi:" + k, JSON.stringify(v)); } catch {} },
  };
  const state = {
    user: store.get("user", null),
    points: store.get("points", 100),
    favs: new Set(store.get("favs", [])),
    cat: "全部", onlineOnly: false, sort: "hot", q: "",
  };
  const save = () => { store.set("user", state.user); store.set("points", state.points); store.set("favs", [...state.favs]); };

  // ---------- data ----------
  let seed = 520;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const pick = a => a[Math.floor(rnd() * a.length)];
  const NAMES = ["小雨", "Mia", "安安", "Yuki", "晴晴", "Coco", "糖糖", "Luna", "小鹿", "Nana", "米米", "Ivy", "朵朵", "Rina", "苡晴", "Hana", "柚子", "Zoe", "芊芊", "Momo", "可可", "Sora", "語彤", "Lily"];
  const CITIES = ["台北", "新北", "桃園", "台中", "台南", "高雄", "新竹", "宜蘭"];
  const CATS = ["全部", "熱門", "新人", "聊天", "唱歌", "才藝", "遊戲", "旅行"];
  const TAGS = { 聊天: ["傾聽", "幽默", "知性"], 唱歌: ["KTV", "抒情", "吉他"], 才藝: ["舞蹈", "繪畫", "鋼琴"], 遊戲: ["手遊", "Switch", "桌遊"], 旅行: ["攝影", "美食", "露營"] };
  const BIOS = ["喜歡下班後喝杯熱可可,聽你說說今天發生的事。", "週末常去爬山拍照,可以聊旅行和美食。", "會一點吉他,心情好就唱給你聽。", "貓派,也喜歡看電影,最近在追推理劇。", "安靜的人,但打開話匣子就停不下來。"];
  const HUES = [[350, 20], [280, 320], [200, 240], [20, 45], [160, 190], [330, 260]];
  const STATUS = ["online", "online", "online", "busy", "offline"];
  const hosts = NAMES.map((name, i) => {
    const cat = pick(CATS.slice(3));
    const h = pick(HUES);
    return {
      id: i + 1, name, cat, age: 20 + Math.floor(rnd() * 10), city: pick(CITIES),
      tags: [cat, ...TAGS[cat].filter(() => rnd() > .4)].slice(0, 3),
      status: pick(STATUS), isNew: rnd() > .78, viewers: Math.floor(rnd() * 900) + 20,
      price: [20, 30, 40, 50, 60][Math.floor(rnd() * 5)], hot: Math.floor(rnd() * 100000),
      joined: Math.floor(rnd() * 400), bio: pick(BIOS),
      bg: `linear-gradient(160deg, hsl(${h[0]} 75% 70%), hsl(${h[1]} 70% 50%))`,
    };
  });
  const byId = id => hosts.find(h => h.id === id);
  const STATUS_TXT = { online: "在線", busy: "通話中", offline: "離線" };
  const SILHOUETTE = '<svg viewBox="0 0 100 80" fill="#fff"><circle cx="50" cy="28" r="18"/><path d="M10 80c0-22 18-36 40-36s40 14 40 36z"/></svg>';

  // ---------- ui helpers ----------
  const toast = msg => { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove("show"), 2200); };
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const renderPoints = () => { $("#points").textContent = state.points; };
  const renderUser = () => { $("#loginBtn").textContent = state.user ? state.user.nick : "登入"; };

  function card(h) {
    return `<article class="card" data-id="${h.id}">
      <div class="thumb" style="background:${h.bg}">
        <span class="initial">${esc(h.name[0])}</span>${SILHOUETTE}
        <span class="status ${h.status}"><i></i>${STATUS_TXT[h.status]}</span>
        <button class="fav ${state.favs.has(h.id) ? "on" : ""}" data-fav="${h.id}" aria-label="收藏">${state.favs.has(h.id) ? "♥" : "♡"}</button>
        ${h.isNew ? '<span class="badge-new">新人</span>' : ""}
        ${h.status !== "offline" ? `<span class="viewers">${h.viewers} 人氣</span>` : ""}
      </div>
      <div class="info">
        <div class="name"><b>${esc(h.name)}</b><span class="price">${h.price} 點/分</span></div>
        <div class="meta">${h.age} 歲 · ${h.city}</div>
        <div class="tags">${h.tags.map(t => `<span class="tag">${esc(t)}</span>`).join("")}</div>
      </div>
    </article>`;
  }

  function filtered() {
    let list = hosts.filter(h => {
      if (state.cat === "熱門" && h.hot < 50000) return false;
      if (state.cat === "新人" && !h.isNew) return false;
      if (!["全部", "熱門", "新人"].includes(state.cat) && h.cat !== state.cat) return false;
      if (state.onlineOnly && h.status === "offline") return false;
      if (state.q && !(h.name + h.tags.join("") + h.city).toLowerCase().includes(state.q.toLowerCase())) return false;
      return true;
    });
    const order = { online: 0, busy: 1, offline: 2 };
    const cmp = { hot: (a, b) => b.hot - a.hot, new: (a, b) => a.joined - b.joined, price: (a, b) => a.price - b.price }[state.sort];
    return list.sort((a, b) => order[a.status] - order[b.status] || cmp(a, b));
  }

  function renderGrid() {
    const list = filtered();
    $("#grid").innerHTML = list.map(card).join("");
    $("#empty").hidden = list.length > 0;
    $("#statOnline").textContent = hosts.filter(h => h.status === "online").length;
  }
  function renderFav() {
    const list = hosts.filter(h => state.favs.has(h.id));
    $("#favGrid").innerHTML = list.map(card).join("");
    $("#favEmpty").hidden = list.length > 0;
  }
  function renderTabs() {
    $("#tabs").innerHTML = CATS.map(c => `<button class="tab ${c === state.cat ? "active" : ""}" data-cat="${c}">${c}</button>`).join("");
  }
  function renderRank(kind = "day") {
    const mult = { day: 1, week: 6.3, month: 24.8 }[kind];
    const salt = { day: 0, week: 7, month: 13 }[kind];
    const list = [...hosts].map(h => ({ h, s: Math.round(((h.hot * (1 + ((h.id * salt) % 5) / 10)) * mult) / 10) })).sort((a, b) => b.s - a.s).slice(0, 15);
    $("#rankList").innerHTML = list.map(({ h, s }, i) => `<li data-id="${h.id}">
      <span class="no">${i + 1}</span>
      <span class="avatar" style="background:${h.bg}">${esc(h.name[0])}</span>
      <span><b>${esc(h.name)}</b><br><span class="muted small">${h.city} · ${h.tags.join(" / ")}</span></span>
      <span class="tag hide-sm">${STATUS_TXT[h.status]}</span>
      <span class="score">${s.toLocaleString()} ♥</span></li>`).join("");
  }

  // ---------- views ----------
  function show(view) {
    $$(".view").forEach(v => (v.hidden = v.id !== "view-" + view));
    $$("#nav a").forEach(a => a.classList.toggle("active", a.dataset.view === view));
    $("#nav").classList.remove("open");
    if (view === "fav") renderFav();
    if (view === "rank") renderRank($("#rankTabs .active").dataset.r);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // ---------- profile ----------
  function openProfile(id) {
    const h = byId(id);
    $("#profileBody").innerHTML = `<button class="close" data-close aria-label="關閉">✕</button>
      <div class="thumb" style="background:${h.bg}"><span class="initial">${esc(h.name[0])}</span>${SILHOUETTE}
        <span class="status ${h.status}"><i></i>${STATUS_TXT[h.status]}</span></div>
      <div class="body">
        <div><h2>${esc(h.name)}</h2><p class="muted small">ID ${String(h.id).padStart(5, "0")}</p></div>
        <p>${esc(h.bio)}</p>
        <dl><dt>年齡</dt><dd>${h.age}</dd><dt>地區</dt><dd>${h.city}</dd><dt>專長</dt><dd>${h.tags.join("、")}</dd>
          <dt>人氣</dt><dd>${h.hot.toLocaleString()}</dd><dt>費用</dt><dd>${h.price} 點 / 分鐘</dd></dl>
        <div class="actions">
          <button class="btn primary" data-call="${h.id}" ${h.status !== "online" ? "disabled" : ""}>${h.status === "online" ? "開始視訊" : STATUS_TXT[h.status] + ",稍後再試"}</button>
          <button class="btn ghost" data-fav="${h.id}">${state.favs.has(h.id) ? "♥ 已收藏" : "♡ 收藏"}</button>
        </div>
      </div>`;
    $("#profileModal").showModal();
  }

  // ---------- room ----------
  const GIFTS = [["🌹", "玫瑰", 10], ["☕", "咖啡", 30], ["🧸", "熊熊", 88], ["💎", "鑽石", 520]];
  const REPLIES = ["哈哈真的嗎?", "今天過得怎麼樣?", "我也這麼覺得耶", "你好有趣 😄", "再多說一點嘛", "謝謝你陪我聊天"];
  let room = null;

  function needLogin() {
    if (state.user) return false;
    toast("請先登入"); $("#loginModal").showModal(); return true;
  }
  function addMsg(text, cls = "") {
    const d = document.createElement("div"); d.className = "msg " + cls; d.textContent = text;
    $("#chat").append(d); $("#chat").scrollTop = $("#chat").scrollHeight;
  }
  function spend(n) {
    if (state.points < n) { toast("點數不足,請儲值"); openTopup(); return false; }
    state.points -= n; save(); renderPoints(); return true;
  }

  async function startCall(id) {
    if (needLogin()) return;
    const h = byId(id);
    if (state.points < h.price) { toast("點數不足一分鐘,請先儲值"); openTopup(); return; }
    $("#profileModal").close();
    room = { h, sec: 0, stream: null, timer: null };
    $("#room").hidden = false; document.body.style.overflow = "hidden";
    $("#remote").style.background = h.bg; $("#remote").className = "remote";
    $("#remote").innerHTML = `<span class="initial">${esc(h.name[0])}</span><span class="connecting">連線中…</span>`;
    $("#roomHost").innerHTML = `<span class="avatar" style="background:${h.bg}">${esc(h.name[0])}</span><span><b>${esc(h.name)}</b><br><small>${h.price} 點/分</small></span>`;
    $("#timer").textContent = "00:00";
    $("#chat").innerHTML = "";
    addMsg(`正在與 ${h.name} 建立連線,每分鐘 ${h.price} 點`, "sys");
    $("#camBtn").classList.remove("off"); $("#micBtn").classList.remove("off");
    try {
      room.stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
      $("#selfVideo").srcObject = room.stream;
    } catch { addMsg("未開啟鏡頭,對方只會聽到 / 看到虛擬畫面", "sys"); }
    setTimeout(() => {
      if (!room) return;
      $("#remote").classList.add("live"); $(".connecting", $("#remote"))?.remove();
      addMsg(`嗨 ${state.user.nick}!很高興認識你 👋`);
      spend(h.price);
      room.timer = setInterval(tick, 1000);
    }, 1400);
  }
  function tick() {
    room.sec++;
    const m = String(Math.floor(room.sec / 60)).padStart(2, "0"), s = String(room.sec % 60).padStart(2, "0");
    $("#timer").textContent = `${m}:${s}`;
    if (room.sec % 60 === 0 && !spend(room.h.price)) endCall();
  }
  function endCall() {
    if (!room) return;
    clearInterval(room.timer);
    room.stream?.getTracks().forEach(t => t.stop());
    $("#selfVideo").srcObject = null;
    const mins = Math.ceil(room.sec / 60) || 1;
    toast(`通話結束,共 ${$("#timer").textContent},花費約 ${mins * room.h.price} 點`);
    room = null; $("#room").hidden = true; document.body.style.overflow = "";
  }
  function sendGift([icon, name, cost]) {
    if (!room || !spend(cost)) return;
    const d = document.createElement("div"); d.textContent = `${icon} 送出 ${name}`;
    $("#giftFly").append(d); setTimeout(() => d.remove(), 3000);
    addMsg(`你送出了 ${icon} ${name}(${cost} 點)`, "sys");
    setTimeout(() => room && addMsg(`哇,謝謝你的${name}!${icon}`), 800);
    room.h.hot += cost * 10;
  }

  // ---------- topup ----------
  const PLANS = [[150, 150, ""], [300, 330, "+10%"], [990, 1150, "熱門"], [2990, 3600, "+20%"]];
  function openTopup() {
    $("#plans").innerHTML = PLANS.map(([nt, pts, tag], i) => `<button class="plan" data-plan="${i}">${tag ? `<em>${tag}</em>` : ""}<b>${pts.toLocaleString()} 點</b><small>NT$ ${nt.toLocaleString()}</small></button>`).join("");
    $("#topupModal").showModal();
  }

  const INFO = {
    terms: ["服務條款", "本站為介面示範,不提供真實的視訊或交易服務。使用者需年滿 18 歲,並應尊重每一位聊天對象。"],
    privacy: ["隱私權政策", "本示範不會上傳任何資料。暱稱、點數與收藏只存在你的瀏覽器(localStorage);鏡頭畫面只在本機預覽,不會傳送到任何地方。"],
    help: ["常見問題", "Q:如何開始視訊?<br>A:登入後點選在線主播,按「開始視訊」。<br><br>Q:點數如何計算?<br>A:接通時先扣第一分鐘,之後每滿一分鐘扣一次。"],
    host: ["成為主播", "示範網站暫不開放主播申請。"],
  };

  // ---------- events ----------
  document.addEventListener("click", e => {
    const t = e.target.closest("[data-view],[data-fav],[data-call],[data-cat],[data-close],[data-plan],[data-info],[data-r],.card,.rank li");
    if (!t) return;
    if (t.matches("[data-view]")) { e.preventDefault(); show(t.dataset.view); return; }
    if (t.matches("[data-fav]")) {
      e.stopPropagation();
      const id = +t.dataset.fav;
      state.favs.has(id) ? state.favs.delete(id) : state.favs.add(id); save();
      toast(state.favs.has(id) ? "已加入收藏" : "已取消收藏");
      renderGrid(); if (!$("#view-fav").hidden) renderFav();
      if ($("#profileModal").open) openProfile(id);
      return;
    }
    if (t.matches("[data-call]")) { startCall(+t.dataset.call); return; }
    if (t.matches("[data-cat]")) { state.cat = t.dataset.cat; renderTabs(); renderGrid(); return; }
    if (t.matches("[data-close]")) { t.closest("dialog").close(); return; }
    if (t.matches("[data-plan]")) {
      const [, pts] = PLANS[+t.dataset.plan]; state.points += pts; save(); renderPoints();
      $("#topupModal").close(); toast(`已儲值 ${pts} 點(示範)`); return;
    }
    if (t.matches("[data-info]")) { e.preventDefault(); const [h, b] = INFO[t.dataset.info]; $("#infoBody").innerHTML = `<h3>${h}</h3><p class="muted">${b}</p>`; $("#infoModal").showModal(); return; }
    if (t.matches("[data-r]")) { $$("#rankTabs .tab").forEach(x => x.classList.toggle("active", x === t)); renderRank(t.dataset.r); return; }
    if (t.matches(".card, .rank li")) openProfile(+t.dataset.id);
  });
  $$("dialog").forEach(d => d.addEventListener("click", e => { if (e.target === d) d.close(); }));

  $("#search").addEventListener("input", e => { state.q = e.target.value.trim(); renderGrid(); if ($("#view-home").hidden) show("home"); });
  $("#onlineOnly").addEventListener("change", e => { state.onlineOnly = e.target.checked; renderGrid(); });
  $("#sort").addEventListener("change", e => { state.sort = e.target.value; renderGrid(); });
  $("#menuBtn").addEventListener("click", () => $("#nav").classList.toggle("open"));
  $("#pointsBtn").addEventListener("click", openTopup);
  $("#randomBtn").addEventListener("click", () => {
    const on = hosts.filter(h => h.status === "online");
    if (on.length) openProfile(on[Math.floor(Math.random() * on.length)].id);
  });
  $("#loginBtn").addEventListener("click", () => {
    if (state.user) { if (confirm(`登出 ${state.user.nick}?`)) { state.user = null; save(); renderUser(); } return; }
    $("#loginModal").showModal();
  });
  $$("#genderSeg button").forEach(b => b.addEventListener("click", () => $$("#genderSeg button").forEach(x => x.classList.toggle("active", x === b))));
  $("#loginModal").addEventListener("close", () => {
    if ($("#loginModal").returnValue !== "ok") return;
    const nick = $("#nickInput").value.trim(); if (!nick) return;
    state.user = { nick, g: $("#genderSeg .active").dataset.g }; save(); renderUser();
    toast(`歡迎,${nick}!送你 100 點體驗金已在帳戶中`);
  });
  $("#checkinBtn").addEventListener("click", () => {
    if (needLogin()) return;
    const today = new Date().toDateString();
    if (store.get("checkin") === today) return toast("今天已經簽到過了");
    store.set("checkin", today); state.points += 5; save(); renderPoints(); toast("簽到成功 +5 點");
  });
  $("#themeBtn").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dark ? "light" : "dark"; store.set("theme", document.documentElement.dataset.theme);
  });

  // room controls
  $("#hangBtn").addEventListener("click", endCall);
  $("#camBtn").addEventListener("click", e => { const v = room?.stream?.getVideoTracks()[0]; if (v) { v.enabled = !v.enabled; e.currentTarget.classList.toggle("off", !v.enabled); } });
  $("#micBtn").addEventListener("click", e => { const a = room?.stream?.getAudioTracks()[0]; if (a) { a.enabled = !a.enabled; e.currentTarget.classList.toggle("off", !a.enabled); } });
  $("#gifts").innerHTML = GIFTS.map((g, i) => `<button class="gift" data-gift="${i}"><span>${g[0]}</span>${g[1]}<small>${g[2]}</small></button>`).join("");
  $("#gifts").addEventListener("click", e => { const b = e.target.closest("[data-gift]"); if (b) sendGift(GIFTS[+b.dataset.gift]); });
  $("#chatForm").addEventListener("submit", e => {
    e.preventDefault(); const v = $("#chatInput").value.trim(); if (!v || !room) return;
    addMsg(v, "me"); $("#chatInput").value = "";
    setTimeout(() => room && addMsg(REPLIES[Math.floor(Math.random() * REPLIES.length)]), 900 + Math.random() * 1200);
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && room) endCall(); });

  // simulate status changes
  setInterval(() => {
    const h = hosts[Math.floor(Math.random() * hosts.length)];
    h.status = pick(STATUS); h.viewers = Math.max(10, h.viewers + Math.floor(Math.random() * 60 - 30));
    if (!$("#view-home").hidden && !room) renderGrid();
  }, 8000);

  // age gate + init
  const theme = store.get("theme"); if (theme) document.documentElement.dataset.theme = theme;
  if (!store.get("adult")) $("#ageGate").hidden = false;
  $("#ageYes").addEventListener("click", () => { store.set("adult", true); $("#ageGate").hidden = true; });
  $("#ageNo").addEventListener("click", () => { location.href = "about:blank"; });
  renderTabs(); renderGrid(); renderPoints(); renderUser();
})();
