"""The "訂閱通知" button on the news and precious-metal pages.

The site is static, so the button cannot store an address itself: it asks for the visitor's email,
then opens their mail program with a ready-made request to the alert mailbox (subject
"[TTMC-NOTIFY] SUBSCRIBE", the address in the body). An hourly job reads that mailbox, adds the
address to the notification list and sends a confirmation (metal_alert/subscribers.py).

The mailbox address comes from SUBSCRIBE_EMAIL at build time (the workflows pass SMTP_USER, whose inbox
the subscription job reads, and ALERT_EMAIL_FROM as SUBSCRIBE_EMAIL_ALT); without one the button is left out.
"""

from __future__ import annotations

import html
import json
import os
import re
from email.utils import parseaddr

TOKEN = "[TTMC-NOTIFY]"
_ADDRESS = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

STYLE = """
.sub-btn{font:inherit;font-weight:600;cursor:pointer}
dialog.sub-dlg{border:0;border-radius:8px;padding:0;width:min(92vw,420px);color:var(--fg,#131415);background:var(--surface,#fbfcfd);box-shadow:0 20px 50px rgba(19,20,21,.25)}
dialog.sub-dlg::backdrop{background:rgba(19,20,21,.45)}
.sub-dlg form{display:grid;gap:12px;padding:24px;border-top:4px solid var(--accent,#173c66);border-radius:8px}
.sub-dlg h2{margin:0;font-size:1.2rem;line-height:1.3}.sub-dlg p{margin:0;font-size:.9rem;line-height:1.6;color:var(--muted,#4d4e4f)}
.sub-dlg input{font:inherit;height:46px;padding:0 12px;border:1px solid var(--axis,#c3c6cc);border-radius:6px;background:var(--surface,#fbfcfd);color:var(--fg,#131415)}
.sub-dlg .row{display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap}
.sub-dlg button{font:inherit;font-weight:600;height:42px;padding:0 16px;border-radius:6px;cursor:pointer;border:1px solid var(--axis,#c3c6cc);background:transparent;color:var(--fg,#131415)}
.sub-dlg button.go{background:var(--accent,#173c66);border-color:var(--accent,#173c66);color:#fbfcfd}
.sub-dlg .ask{display:grid;gap:12px}.sub-dlg .done{display:none}.sub-dlg.sent .ask{display:none}.sub-dlg.sent .done{display:grid;gap:12px}
.sub-dlg code{font-family:var(--font-data,ui-monospace,monospace);font-size:.85rem;background:var(--accent-soft,#e4eaf2);padding:1px 6px;border-radius:4px;word-break:break-all}
.sub-dlg .err{color:var(--up,#c62f2f);font-size:.85rem;min-height:1.2em}
"""

SCRIPT = """
(function(){
  var BOX = %(box)s, TOKEN = %(token)s, dlg = document.getElementById('sub-dlg');
  if (!dlg || !dlg.showModal) return;
  var form = dlg.querySelector('form'), input = dlg.querySelector('input'), err = dlg.querySelector('.err');
  document.querySelectorAll('[data-subscribe]').forEach(function(b){
    b.addEventListener('click', function(){ dlg.classList.remove('sent'); err.textContent = ''; dlg.showModal(); input.focus(); });
  });
  dlg.querySelectorAll('[data-close]').forEach(function(b){ b.addEventListener('click', function(){ dlg.close(); }); });
  form.addEventListener('submit', function(e){
    e.preventDefault();
    var addr = input.value.trim();
    if (!/^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$/.test(addr)) { err.textContent = '請輸入正確的 email。'; return; }
    var subject = TOKEN + ' SUBSCRIBE 訂閱靶材週報與貴金屬預警';
    var body = '訂閱信箱:' + addr + '\\n\\n請直接寄出這封信;約一小時內會收到確認信。';
    dlg.querySelector('.addr').textContent = addr;
    dlg.classList.add('sent');
    location.href = 'mailto:' + BOX + '?subject=' + encodeURIComponent(subject) + '&body=' + encodeURIComponent(body);
  });
})();
"""


def mailbox() -> str:
    """The first real address among SUBSCRIBE_EMAIL (the SMTP login, whose inbox the job reads) and
    SUBSCRIBE_EMAIL_ALT (ALERT_EMAIL_FROM, which may be "Name <addr>" or only a name)."""
    for key in ("SUBSCRIBE_EMAIL", "SUBSCRIBE_EMAIL_ALT"):
        value = os.environ.get(key, "").strip()
        addr = parseaddr(value)[1]
        m = _ADDRESS.search(addr if _ADDRESS.fullmatch(addr or "") else value)
        if m:
            return m.group(0)
    return ""


def button(cls: str = "btn") -> str:
    """The button itself; place it wherever the page has its actions. Empty when no mailbox is configured."""
    return f'<button type="button" class="{cls} sub-btn" data-subscribe>訂閱通知</button>' if mailbox() else ""


def dialog() -> str:
    """The dialog, its style and script; add once per page that has a button."""
    box = mailbox()
    if not box:
        return ""
    e = html.escape
    return (
        f"<style>{STYLE}</style>"
        f'<dialog class="sub-dlg" id="sub-dlg" aria-labelledby="sub-h"><form method="dialog" novalidate>'
        f'<div class="ask"><h2 id="sub-h">訂閱 email 通知</h2>'
        f"<p>訂閱後會收到<strong>貴金屬價格預警</strong>(出現進出場訊號時)與每週一的<strong>靶材產業週報</strong>。</p>"
        f'<input type="email" name="email" placeholder="you@example.com" aria-label="你的 email" autocomplete="email" required>'
        f'<div class="err" role="alert"></div>'
        f'<p>按「下一步」會開啟你的郵件程式,寄出那封已填好的訂閱信即完成。</p>'
        f'<div class="row"><button type="button" data-close>取消</button><button class="go" type="submit">下一步</button></div></div>'
        f'<div class="done"><h2>請寄出訂閱信</h2>'
        f'<p>郵件程式應已開啟一封寄給 <code>{e(box)}</code> 的信;寄出後約一小時內,<span class="addr"></span> 會收到確認信。</p>'
        f'<p>沒有開啟郵件程式?請自行寄信到 <code>{e(box)}</code>,主旨填 <code>{e(TOKEN)} SUBSCRIBE</code>,內文寫「訂閱信箱:你的 email」。</p>'
        f'<p>取消訂閱:主旨改為 <code>{e(TOKEN)} UNSUBSCRIBE</code>。</p>'
        f'<div class="row"><button class="go" type="button" data-close>完成</button></div></div>'
        f"</form></dialog><script>{SCRIPT % {'box': json.dumps(box), 'token': json.dumps(TOKEN)}}</script>"
    )
