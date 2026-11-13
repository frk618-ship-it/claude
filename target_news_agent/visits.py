"""Visitor counter on the news homepage, counted by GoatCounter (goatcounter.com).

The site lives under a secret path, so nothing about the real address leaves the page: the visit
is reported under the fixed path PATH with no referrer, and the browser's own Referer header is
cut to the origin by the page's referrer policy. GoatCounter counts each browser once a day as a
visitor; the footer shows its total from the public counter endpoint, which only answers once
"Allow adding visitor counts on your website" is switched on in the GoatCounter site settings.
Until it does (or if the script is blocked), the line stays hidden and the page is unchanged.
"""

from __future__ import annotations

import json

SITE = "https://rodhsu.goatcounter.com"
PATH = "/news/"
TITLE = "靶材新聞首頁"
START, END = "<!--visits-->", "<!--/visits-->"

STYLE = ".foot p[hidden]{display:none}"

_SCRIPT = """
(function(){
  var site = %(site)s, path = %(path)s, box = document.getElementById('visits');
  function show(){
    if (!window.fetch || !box) return;
    // a path with no visits yet answers 404 with {"count": "0"}, so read the body whatever the status
    fetch(site + '/counter/' + encodeURIComponent(path) + '.json').then(function(r){ return r.json(); })
      .then(function(d){ if (d && d.count != null) { box.querySelector('.n').textContent = d.count; box.hidden = false; } })
      .catch(function(){});
  }
  var s = document.createElement('script');
  s.async = true; s.src = 'https://gc.zgo.at/count.js';
  s.setAttribute('data-goatcounter', site + '/count');
  s.setAttribute('data-goatcounter-settings', '{"no_onload": true}');
  s.onload = function(){
    try { window.goatcounter.count({path: path, title: %(title)s, referrer: ''}); } catch (e) {}
    setTimeout(show, 800);
  };
  s.onerror = show;
  document.head.appendChild(s);
})();
"""


def counter_line(icon_html: str) -> str:
    """The footer line (hidden until the count arrives) and the script that fills it."""
    script = _SCRIPT % {"site": json.dumps(SITE), "path": json.dumps(PATH), "title": json.dumps(TITLE, ensure_ascii=False)}
    return (f'{START}<p id="visits" hidden>{icon_html}<span><strong>訪客人數</strong>'
            f'累計 <span class="n mono"></span> 人次(同一瀏覽器每天計一次)。</span></p>'
            f"<script>{script}</script>{END}")
