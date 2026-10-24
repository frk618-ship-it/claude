"""Publish the built site behind a secret path and a password.

GitHub Pages has no access control, so the site is made private by obscurity plus encryption:

- everything is served under a long random path (SITE_PATH), nothing links to it from the
  site root, and robots are told not to index anything;
- every HTML page is encrypted (AES-256-GCM, key from the password via PBKDF2-SHA256) and
  replaced by a small unlock page that decrypts it in the browser. A link ending in
  ``#k=<password>`` unlocks directly, so "anyone with the link" can read it; the fragment
  never reaches the server. The derived key is remembered in the browser session (or on the
  device, if the visitor ticks the box), so moving between pages doesn't ask again;
- raw data files (.json / .csv) are not published.

SITE_PATH and SITE_PASSWORD come from the environment (GitHub Actions secrets). When either
is missing, only a placeholder page is published, so the content is never exposed by mistake.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import shutil
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ITERATIONS = 210_000
PATH_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
SKIP_SUFFIXES = {".json", ".csv"}

PLACEHOLDER = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Not found</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f4f6fb;color:#55607a;font:16px system-ui,sans-serif}</style>
</head><body><p>{message}</p></body></html>"""

SHELL = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><meta name="referrer" content="no-referrer"><title>需要密碼</title>
<style>
:root{--bg:#f4f6fb;--fg:#162033;--muted:#55607a;--accent:#3b5bdb;--up:#e03131}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);font:16px/1.6 "Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif;padding:16px}
form{background:#fff;border-top:4px solid var(--accent);border-radius:6px;padding:24px;width:min(100%,360px);display:grid;gap:12px}
h1{margin:0;font-size:1.15rem}p{margin:0;font-size:.88rem;color:var(--muted)}
input[type=password]{font:inherit;border:2px solid #dfe4ef;border-radius:6px;padding:10px 12px}input[type=password]:focus{outline:none;border-color:var(--accent)}
label{font-size:.85rem;color:var(--muted);display:flex;gap:6px;align-items:center}
button{font:inherit;font-weight:600;border:0;border-radius:6px;padding:10px;background:var(--accent);color:#fff;cursor:pointer}
.err{color:var(--up);font-size:.85rem;min-height:1.3em}
@media (prefers-color-scheme:dark){:root{--bg:#0f1424;--fg:#e8ecf6;--muted:#9aa5c0}form{background:#171e33}input[type=password]{background:#0f1424;color:var(--fg);border-color:#283252}}
</style></head><body>
<form id="f" hidden><h1>這個網站需要密碼</h1><p>請輸入密碼,或使用含有金鑰的分享連結開啟。</p>
<input type="password" id="pw" autocomplete="current-password" aria-label="密碼" required>
<label><input type="checkbox" id="rm"> 在這個裝置上記住</label><button>開啟</button><div class="err" id="err" role="alert"></div></form>
<noscript>需要啟用 JavaScript 才能開啟這個頁面。</noscript>
<script>
(function(){
const P = __PAYLOAD__;
const b64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
const enc = u => btoa(String.fromCharCode(...new Uint8Array(u)));
const KEY = 'site-key-' + P.s;
const get = st => { try { return st.getItem(KEY); } catch (e) { return null; } };
const put = (st, v) => { try { st.setItem(KEY, v); } catch (e) {} };
async function derive(pw) {
  const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(pw), 'PBKDF2', false, ['deriveBits']);
  return crypto.subtle.deriveBits({name: 'PBKDF2', salt: b64(P.s), iterations: P.n, hash: 'SHA-256'}, base, 256);
}
async function open(raw) {
  const k = await crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['decrypt']);
  const html = new TextDecoder().decode(await crypto.subtle.decrypt({name: 'AES-GCM', iv: b64(P.i)}, k, b64(P.c)));
  document.open(); document.write(html); document.close();
}
async function tryKey(raw) {
  try { await open(raw); } catch (e) { return false; }
  return true;
}
async function main() {
  const f = document.getElementById('f'), err = document.getElementById('err');
  const saved = get(sessionStorage) || get(localStorage);
  if (saved && await tryKey(b64(saved))) return;
  const m = location.hash.match(/^#k=(.+)$/);
  if (m) {
    const raw = await derive(decodeURIComponent(m[1]));
    put(sessionStorage, enc(raw));
    try { history.replaceState(null, '', location.pathname + location.search); } catch (e) {}
    if (await tryKey(raw)) return;
    err.textContent = '連結中的金鑰不正確。';
  }
  f.hidden = false; document.getElementById('pw').focus();
  f.onsubmit = async e => {
    e.preventDefault(); err.textContent = '解鎖中…';
    const raw = await derive(document.getElementById('pw').value), v = enc(raw);
    put(sessionStorage, v); if (document.getElementById('rm').checked) put(localStorage, v);
    if (!await tryKey(raw)) { err.textContent = '密碼不正確,請再試一次。'; try { sessionStorage.removeItem(KEY); localStorage.removeItem(KEY); } catch (x) {} }
  };
}
// swap documents only once this unlock page has finished parsing, or the decrypted page
// gets written into a half-built document (wrong title, duplicated head)
document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', main) : main();
})();
</script></body></html>"""


def derive_key(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS, 32)


def site_salt(path: str) -> bytes:
    """Stable per secret path, so a key remembered in the browser survives daily rebuilds."""
    return hashlib.sha256(("target-news-site:" + path).encode("utf-8")).digest()[:16]


def encrypt_page(page: str, key: bytes, salt: bytes) -> str:
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, page.encode("utf-8"), None)  # ciphertext || tag, as WebCrypto expects
    payload = {"s": base64.b64encode(salt).decode(), "i": base64.b64encode(iv).decode(),
               "c": base64.b64encode(ct).decode(), "n": ITERATIONS}
    return SHELL.replace("__PAYLOAD__", json.dumps(payload))


def decrypt_page(shell: str, password: str) -> str:
    """Inverse of encrypt_page (for tests and checks)."""
    payload = json.loads(re.search(r"const P = (\{.*?\});", shell).group(1))
    key = derive_key(password, base64.b64decode(payload["s"]))
    return AESGCM(key).decrypt(base64.b64decode(payload["i"]), base64.b64decode(payload["c"]), None).decode("utf-8")


def placeholder(message: str) -> str:
    return PLACEHOLDER.replace("{message}", html.escape(message))


def relink(page: str, public_base: str, path: str, tops: list[str]) -> str:
    """Point absolute links into the site (public_base + news/ ...) at the secret path instead."""
    base = public_base.rstrip("/") + "/"
    for top in tops:
        page = page.replace(base + top + "/", f"{base}{path}/{top}/")
    return page


def protect(src: str | Path, out: str | Path, path: str | None, password: str | None,
            public_base: str = "") -> tuple[int, str]:
    """Write the protected site to `out`. Returns (pages encrypted, status message).

    `public_base` is the site's public root URL (e.g. https://user.github.io/repo/); absolute links
    to its sections, such as the dashboard's link back to the news site, are moved under the path."""
    src, out = Path(src), Path(out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    if not path or not password:
        (out / "index.html").write_text(placeholder("這個網站目前不公開。"), encoding="utf-8")
        return 0, "SITE_PATH / SITE_PASSWORD not set: published a placeholder only"
    if not PATH_RE.match(path):
        raise ValueError("SITE_PATH must be 16-64 characters of letters, digits, '-' or '_'")
    for name in ("index.html", "404.html"):
        (out / name).write_text(placeholder("找不到這個頁面。"), encoding="utf-8")
    salt = site_salt(path)
    key = derive_key(password, salt)
    base, pages = out / path, 0
    tops = sorted(d.name for d in src.iterdir() if d.is_dir())
    for f in sorted(p for p in src.rglob("*") if p.is_file()):
        rel = f.relative_to(src)
        if rel == Path("index.html") or f.suffix.lower() in SKIP_SUFFIXES:
            continue  # the root redirect would reveal the path; raw data stays unpublished
        dest = base / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if f.suffix.lower() == ".html":
            page = f.read_text(encoding="utf-8")
            if public_base:
                page = relink(page, public_base, path, tops)
            dest.write_text(encrypt_page(page, key, salt), encoding="utf-8")
            pages += 1
        else:
            shutil.copyfile(f, dest)
    return pages, f"{pages} pages encrypted under the secret path"
