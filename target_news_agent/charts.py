"""Charts for the news site, drawn to the data-viz method.

The form follows the data's job (magnitude -> bars, a focus day among others -> emphasis
columns, a headline price -> stat tile + sparkline, two measures over time -> one indexed axis,
one share of a whole -> meter); colour comes from the page's role tokens (--series-*, --accent,
--deemph, ink), never from literal hex. Marks are thin with a 4px rounded data end and a square
baseline end, grids are solid hairlines, labels are selective and use ink colours, and every mark
has a hover / keyboard-focus tooltip (CHART_SCRIPT) that only repeats values already shown in text.

Bars and columns are HTML so their Chinese labels keep their real size on phones; the line
chart and sparklines are SVG.
"""

from __future__ import annotations

import html
import math

CHART_STYLE = """
.ch{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:14px 16px;display:grid;gap:10px;align-content:start;min-width:0}
.ch h3{margin:0;font-size:.95rem;font-weight:700}.ch .chs{font-size:.8rem;color:var(--muted);margin:-6px 0 0}
.ch-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr));gap:12px}
.hbar{display:grid;grid-template-columns:auto minmax(0,1fr);gap:6px 10px;align-items:center;font-size:.84rem}
.hbar .hl{color:var(--fg);white-space:nowrap}
.hbar .ht{display:flex;align-items:center;gap:6px;min-width:0;border-radius:2px;outline-offset:2px}
.hbar .hm{height:14px;min-width:2px;background:var(--series-1);border-radius:0 4px 4px 0}
.hbar .hv{color:var(--muted);font-size:.78rem;white-space:nowrap;font-variant-numeric:tabular-nums}
.hbar .ht:hover .hm,.hbar .ht:focus-visible .hm{background:var(--accent-2)}
.cols{position:relative;height:150px;margin:6px 0 0 30px;border-bottom:1px solid var(--axis)}
.cols .gl{position:absolute;left:0;right:0;border-top:1px solid var(--grid)}
.cols .gl span{position:absolute;right:calc(100% + 6px);top:-.6em;font-size:.7rem;color:var(--faint);font-variant-numeric:tabular-nums}
.cols .bars{position:absolute;inset:0;display:flex;align-items:flex-end;gap:2px}
.cols .col{flex:1;height:100%;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;outline-offset:2px;border-radius:2px}
.cols .cm{width:min(24px,70%);background:var(--deemph);border-radius:4px 4px 0 0;min-height:2px}
.cols .col.on .cm{background:var(--accent)}.cols .col:hover .cm,.cols .col:focus-visible .cm{background:var(--accent-2)}
.cols .cv{font-size:.72rem;color:var(--fg);font-weight:700;margin-bottom:3px;font-variant-numeric:tabular-nums}
.cols-x{display:flex;gap:2px;margin-left:30px;font-size:.7rem;color:var(--faint);font-variant-numeric:tabular-nums}
.cols-x span{flex:1;text-align:center;white-space:nowrap;overflow:visible}
.spark{display:block;width:100%;max-width:150px;height:34px;overflow:visible}
.lc{overflow-x:auto}.lc svg{display:block;width:100%;min-width:420px;height:auto;overflow:visible}
.lc text{font-family:var(--font-body);font-size:12px;fill:var(--muted)}.lc .lt{fill:var(--fg);font-weight:700}
.lc .grid{stroke:var(--grid);stroke-width:1}.lc .base{stroke:var(--axis);stroke-width:1}
.lc g[data-tip]{outline:none}.lc g[data-tip]:focus-visible circle.pt{stroke:var(--fg)}
.legend{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:.8rem;color:var(--muted)}
.legend i{display:inline-block;width:16px;height:2px;margin-right:6px;vertical-align:middle;border-radius:1px}
.meter{display:grid;gap:6px}.meter .mt{height:12px;border-radius:6px;background:var(--accent-track);overflow:hidden}
.meter .mf{height:100%;background:var(--accent);border-radius:6px 0 0 6px}.meter .mr{display:flex;justify-content:space-between;gap:12px;font-size:.84rem}
.meter .mr b{font-size:1.4rem}
table.tv{border-collapse:collapse;font-size:.8rem;width:100%}table.tv th,table.tv td{padding:4px 8px;border-bottom:1px solid var(--grid);text-align:right;font-variant-numeric:tabular-nums}
table.tv th:first-child,table.tv td:first-child{text-align:left}table.tv th{color:var(--muted);font-weight:500}
details.tvd summary{cursor:pointer;font-size:.8rem;color:var(--muted)}
.ctip{position:fixed;z-index:50;pointer-events:none;background:var(--surface);color:var(--fg);border:1px solid var(--line);border-radius:4px;
  padding:6px 9px;font-size:.8rem;line-height:1.5;white-space:pre-line;max-width:260px}
@media (prefers-reduced-motion:reduce){.hbar .hm,.cols .cm{transition:none}}
"""

CHART_SCRIPT = """
(function(){var tip=document.createElement('div');tip.className='ctip';tip.hidden=true;document.body.appendChild(tip);
function show(el,x,y){tip.textContent=el.getAttribute('data-tip');tip.hidden=false;var w=tip.offsetWidth,h=tip.offsetHeight;
tip.style.left=Math.max(8,Math.min(innerWidth-w-8,x+12))+'px';tip.style.top=(y-h-10<8?y+16:y-h-10)+'px';}
[].forEach.call(document.querySelectorAll('[data-tip]'),function(el){
el.addEventListener('pointermove',function(e){show(el,e.clientX,e.clientY);});el.addEventListener('pointerleave',function(){tip.hidden=true;});
el.addEventListener('focus',function(){var b=el.getBoundingClientRect();show(el,b.left+b.width/2,b.top);});el.addEventListener('blur',function(){tip.hidden=true;});});})();
"""


def esc(v) -> str:
    return html.escape(str(v), quote=True)


def nice_ticks(lo: float, hi: float, count: int = 4) -> list[float]:
    """Round tick values covering [lo, hi]."""
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    ticks, v = [], start
    while v < hi + step * 0.999:
        ticks.append(round(v, 10))
        v += step
    return ticks


def _num(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 or float(v).is_integer() else f"{v:,.1f}"


def hbar(rows: list[tuple[str, float]], unit: str = "", total: float | None = None) -> str:
    """Horizontal bars for magnitude across nominal categories: one colour, value at the tip."""
    if not rows:
        return ""
    top = max(v for _, v in rows) or 1
    total = total if total is not None else sum(v for _, v in rows)
    out = []
    for label, v in rows:
        share = f"(占 {v / total:.0%})" if total else ""
        out.append(
            f'<span class="hl">{esc(label)}</span>'
            f'<span class="ht" tabindex="0" data-tip="{esc(f"{label}:{_num(v)} {unit}{share}")}">'
            f'<span class="hm" style="width:{v / top * 82:.1f}%"></span><span class="hv">{_num(v)}{esc(unit)}</span></span>'
        )
    return f'<div class="hbar" role="list">{"".join(out)}</div>'


def columns(rows: list[tuple[str, float]], highlight: str | None = None, unit: str = "", label=lambda k: k) -> str:
    """Columns over time with one emphasised column (accent) and the rest in the de-emphasis grey.
    Only the emphasised column carries a value label; the axis and tooltips carry the rest."""
    if not rows:
        return ""
    ticks = nice_ticks(0, max(v for _, v in rows), 3)
    top = ticks[-1] or 1
    grid = "".join(f'<div class="gl" style="bottom:{t / top * 100:.2f}%"><span>{_num(t)}</span></div>' for t in ticks)
    highlight = highlight if highlight is not None else rows[-1][0]
    every = max(1, math.ceil(len(rows) / 7))
    bars, xs = [], []
    for i, (k, v) in enumerate(rows):
        on = k == highlight
        val = f'<span class="cv">{_num(v)}</span>' if on else ""
        bars.append(
            f'<div class="col{" on" if on else ""}" tabindex="0" data-tip="{esc(f"{label(k)}:{_num(v)} {unit}")}">'
            f'{val}<div class="cm" style="height:{v / top * 100:.2f}%"></div></div>'
        )
        show = on or i == 0 or i == len(rows) - 1 or (i % every == 0 and len(rows) - 1 - i >= every * 0.6)
        xs.append(f"<span>{esc(label(k)) if show else ''}</span>")
    return (f'<div class="cols" role="img" aria-label="{esc(f"每日{unit}數")}">{grid}<div class="bars">{"".join(bars)}</div></div>'
            f'<div class="cols-x" aria-hidden="true">{"".join(xs)}</div>')


def sparkline(values: list[float], label: str = "") -> str:
    """Trend under a stat tile: the series in the de-emphasis grey, the latest point in the accent."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return ""
    w, h, pad = 150, 34, 5
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    pts = [(pad + i * (w - 2 * pad) / (len(vals) - 1), h - pad - (v - lo) / span * (h - 2 * pad)) for i, v in enumerate(vals)]
    d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    x, y = pts[-1]
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(label)}">'
            f'<polyline points="{d}" fill="none" stroke="var(--deemph)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="var(--accent)" stroke="var(--surface)" stroke-width="2"/></svg>')


def index_lines(years: list[int], kinds: list[str], series: list[dict]) -> str:
    """Two or more measures on one axis, indexed to the first year (= 100). Segments into a
    forecast year are dashed (dashing is reserved for projection). Legend + end labels + table."""
    colours = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)"]
    idx = [[v / s["values"][0] * 100 for v in s["values"]] for s in series]
    W, H, L, R, T, B = 520, 240, 40, 118, 12, 28
    ticks = nice_ticks(min(min(i) for i in idx), max(max(i) for i in idx), 4)
    lo, hi = ticks[0], ticks[-1]
    x = lambda yr: L + (yr - years[0]) / ((years[-1] - years[0]) or 1) * (W - L - R)
    y = lambda v: H - B - (v - lo) / (hi - lo) * (H - T - B)
    parts = [f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
             f'<text x="{L - 6}" y="{y(t) + 4:.1f}" text-anchor="end">{_num(t)}</text>' for t in ticks]
    parts.append(f'<line class="base" x1="{L}" x2="{W - R}" y1="{H - B}" y2="{H - B}"/>')
    for yr, k in zip(years, kinds):
        parts.append(f'<text x="{x(yr):.1f}" y="{H - 8}" text-anchor="middle">{yr}{"" if k == "實績" else "(" + esc(k) + ")"}</text>')
    ends = []
    for s, iv, c in zip(series, idx, colours):
        for i in range(1, len(years)):
            dash = ' stroke-dasharray="5 4"' if kinds[i] == "預測" else ""
            parts.append(f'<line x1="{x(years[i - 1]):.1f}" y1="{y(iv[i - 1]):.1f}" x2="{x(years[i]):.1f}" y2="{y(iv[i]):.1f}" '
                         f'stroke="{c}" stroke-width="2" stroke-linecap="round"{dash}/>')
        for i, yr in enumerate(years):
            tipt = f'{yr} {s["name"]}:{_num(s["values"][i])} {s.get("unit", "")}(指數 {iv[i]:.0f},{kinds[i]})'
            parts.append(f'<g tabindex="0" data-tip="{esc(tipt)}"><circle cx="{x(yr):.1f}" cy="{y(iv[i]):.1f}" r="12" fill="transparent"/>'
                         f'<circle class="pt" cx="{x(yr):.1f}" cy="{y(iv[i]):.1f}" r="4" fill="{c}" stroke="var(--surface)" stroke-width="2"/></g>')
        ends.append([y(iv[-1]), s["name"], iv[-1]])
    ends.sort()
    for i in range(1, len(ends)):  # keep end labels apart without detaching them from their lines
        ends[i][0] = max(ends[i][0], ends[i - 1][0] + 16)
    for ey, name, v in ends:
        parts.append(f'<text x="{W - R + 10}" y="{ey + 4:.1f}"><tspan class="lt">{v:.0f}</tspan> {esc(name)}</text>')
    legend = "".join(f'<span><i style="background:{c}"></i>{esc(s["name"])}</span>' for s, c in zip(series, colours))
    head = "".join(f"<th>{esc(s['name'])}({esc(s.get('unit', ''))})</th>" for s in series)
    body = "".join(
        f"<tr><td>{yr} {esc(k)}</td>" + "".join(f"<td>{_num(s['values'][i])}</td>" for s in series) + "</tr>"
        for i, (yr, k) in enumerate(zip(years, kinds))
    )
    return (f'<div class="legend">{legend}<span>虛線 = 預測區間</span></div>'
            f'<div class="lc"><svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc("、".join(s["name"] for s in series))}指數走勢">{"".join(parts)}</svg></div>'
            f'<details class="tvd"><summary>查看數據表</summary><table class="tv"><tr><th>年度</th>{head}</tr>{body}</table></details>')


def meter(share: float, label: str, note: str = "") -> str:
    """One share of a whole: accent fill on a lighter track of the same hue."""
    pct = max(0.0, min(1.0, share))
    return (f'<div class="meter" role="img" aria-label="{esc(f"{label} 約 {pct:.0%}")}">'
            f'<div class="mr"><span>{esc(label)}</span><b>約 {pct:.0%}</b></div>'
            f'<div class="mt"><div class="mf" style="width:{pct * 100:.1f}%"></div></div>'
            + (f'<span class="sub">{esc(note)}</span>' if note else "") + "</div>")
