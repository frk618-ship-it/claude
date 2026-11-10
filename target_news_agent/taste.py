"""The motion layer every page carries, after taste-skill 2.5 (github.com/Leonxlnx/taste-skill,
references/motion.md): the hero's one entrance on load, the hover and press layer (buttons step 1px,
linked cards lift 2px, underlines draw) and, on news pages, one dealt moment ("word-highlight": the
period's summary lights up clause by clause as it scrolls through the screen).

Taken over as the skill's rules and numbers, written for this site's markup; no file of the kit is
copied. Content is whole without script and under reduced motion: motion only adds to it.
"""

from __future__ import annotations

FONT_LINKS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@400;600&amp;family=Geist+Mono:wght@400;500'
    '&amp;family=Noto+Sans+TC:wght@400;500;700&amp;display=swap">'
)

STYLE = """
/* taste-skill 2.5 motion layer · pairing: geist (display and text Geist, figures Geist Mono; Chinese in Noto Sans TC) */
:root{--m-in:150ms;--m-out:120ms;--m-press:90ms;--m-image:600ms;--m-ease:cubic-bezier(.2,.8,.2,1);--m-ease-enter:cubic-bezier(.22,1,.36,1);
  --m-lift:-2px;--m-shadow:0 10px 24px rgba(19,20,21,.08)}
/* buttons: the fill steps toward the ink and the box rises 1px; a press sinks it 1px and shrinks it 1.5% */
:is(.btn,.chip,button.f,.g-btn,.tab,.extlinks a,.seg button){transition:background-color var(--m-out) var(--m-ease),color var(--m-out) var(--m-ease),
  border-color var(--m-out) var(--m-ease),transform var(--m-press) var(--m-ease)}
:is(.btn,.chip,button.f,.g-btn,.tab,.extlinks a,.seg button):active{transform:translateY(1px) scale(.985)}
/* linked cards lift 2px, their border darkens a step and a soft shadow comes in; cards without a link stay still */
:is(.card:has(a[href]),.lead-story,button.card,.hs,.ilist .irow){transition:transform var(--m-out) var(--m-ease),box-shadow var(--m-out) var(--m-ease),border-color var(--m-out) var(--m-ease)}
/* underlines draw left to right under titles and section links */
:is(.card h3 a,.lead-title a,.secnav a,.wordmark){text-decoration:none;background:linear-gradient(currentColor,currentColor) 0 100%/0 1px no-repeat;
  transition:background-size var(--m-out) var(--m-ease),color var(--m-out) var(--m-ease)}
:is(.card h3 a,.lead-title a,.secnav a):focus-visible{background-size:100% 1px}
@media (hover:hover) and (pointer:fine){
  :is(.btn,.chip,button.f,.g-btn,.tab,.extlinks a):hover{transform:translateY(-1px);transition-duration:var(--m-in)}
  :is(.btn,.chip,button.f,.g-btn,.tab,.extlinks a):active{transform:translateY(1px) scale(.985)}
  :is(.card:has(a[href]),.lead-story,button.card,.hs):hover{transform:translateY(var(--m-lift));box-shadow:var(--m-shadow);transition-duration:var(--m-in)}
  :is(.card h3 a,.lead-title a,.secnav a):hover{background-size:100% 1px;transition-duration:var(--m-in)}
}
/* the hero's entrance, once on load: the title rises out of its mask, then the line above it, the lead and the
   buttons fade up 12px, 60ms apart; the hero's picture settles from .98. CSS only, so the page is whole without script */
[data-enter] h1{animation:m-rise 700ms var(--m-ease-enter) both}
[data-enter] :is(.dateline,.lede,.actions,.sub,.meta,.extlinks,.tabs){animation:m-up 520ms var(--m-ease-enter) both;animation-delay:calc(350ms + var(--m-i,0) * 60ms)}
[data-enter] .lede{--m-i:1}[data-enter] .actions,[data-enter] .meta{--m-i:2}[data-enter] .extlinks,[data-enter] .tabs{--m-i:3}
[data-enter] :is(.lead-story,[data-enter-media]){animation:m-settle 900ms var(--m-ease-enter) both}
@keyframes m-rise{from{transform:translateY(.45em);clip-path:inset(0 0 100% 0)}to{transform:none;clip-path:inset(0 0 -20% 0)}}
@keyframes m-up{from{opacity:0;transform:translateY(12px)}}
@keyframes m-settle{from{transform:scale(.98)}}
/* the dealt moment: a statement that lights up as it scrolls through the screen */
.m-highlight .m-w{color:var(--faint,#7c7d80);transition:color 300ms var(--m-ease)}.m-highlight .m-w.on{color:inherit}
@media (prefers-reduced-motion:reduce){
  [data-enter] *,[data-enter] h1{animation:none!important}
  :is(.btn,.chip,button.f,.g-btn,.tab,.extlinks a,.card,.lead-story,.hs){transform:none!important}
  .m-highlight .m-w{color:inherit}
}
"""

SCRIPT = """
(function(){
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  document.querySelectorAll('[data-moment="word-highlight"]').forEach(function(p){
    // split the statement into clauses; each lights up as the paragraph passes the middle of the screen
    var parts = p.textContent.split(/(?<=[,;:、。!?,;:])/);
    if (parts.length < 3) return;
    p.textContent = '';
    var spans = parts.map(function(t){ var s = document.createElement('span'); s.className = 'm-w'; s.textContent = t; p.appendChild(s); return s; });
    p.classList.add('m-highlight');
    var ticking = false;
    function update(){
      ticking = false;
      var r = p.getBoundingClientRect(), vh = innerHeight;
      var progress = (vh * 0.85 - r.top) / (r.height + vh * 0.35);
      var lit = Math.max(0, Math.min(spans.length, Math.round(progress * spans.length)));
      spans.forEach(function(s, i){ s.classList.toggle('on', i < lit); });
    }
    addEventListener('scroll', function(){ if (!ticking) { ticking = true; requestAnimationFrame(update); } }, {passive: true});
    addEventListener('resize', update);
    update();
  });
})();
"""


def layer() -> str:
    """The style and script every page appends (fab_nav carries it)."""
    return f"<style>{STYLE}</style><script>{SCRIPT}</script>"
