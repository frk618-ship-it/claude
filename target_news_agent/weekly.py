"""Weekly report: the past Monday-to-Sunday in one page and one email.

The week is read from the daily reports dated Monday to Sunday. It lists every key story (4-5 stars,
reprints folded into one event) with why it matters, the companies reported most, the topic mix,
each day's headline and the precious-metal prices over the week (Sunday's quote against the last
quote before Monday, with the week's high and low).

- week-<monday>.html is built for every complete week in reports/ (complete = the latest report is
  dated Sunday or later), weekly.html is the latest one.
- `python -m target_news_agent weekly-email` sends last week's report to the precious-metal alert's
  email group (SMTP_* and ALERT_EMAIL_TO, see metal_alert/alerts.py), with a link to the page.
"""

from __future__ import annotations

import html as _html
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from .config import Config
from .site import (
    KEY_IMPORTANCE,
    REPORT_RE,
    SIGNAL_KINDS,
    _fab,
    _figures,
    _h2,
    _head,
    _headline_clause,
    _item_html,
    _mast,
    esc,
    hbar,
    icon,
    item_kind,
    pick_events,
    short_name,
    source_name,
    why,
)
from .translate import to_traditional

WEEKDAYS = "一二三四五六日"
MAX_EMAIL_STORIES = 15


def last_week(today: date) -> date:
    """Monday of the last complete week before `today`."""
    return today - timedelta(days=today.weekday() + 7)


def load_reports(reports_dir: str | Path) -> list[tuple[str, dict]]:
    """Daily reports, newest first."""
    import json

    paths = sorted((p for p in Path(reports_dir).iterdir() if REPORT_RE.match(p.name)), reverse=True)
    return [(p.stem, json.loads(p.read_text(encoding="utf-8"))) for p in paths]


def complete_weeks(reports: list[tuple[str, dict]]) -> list[date]:
    """Mondays of the weeks that have reports and have ended, newest first."""
    if not reports:
        return []
    latest = date.fromisoformat(reports[0][0])
    mondays = {date.fromisoformat(d) - timedelta(days=date.fromisoformat(d).weekday()) for d, _ in reports}
    return sorted((m for m in mondays if m + timedelta(days=6) <= latest), reverse=True)


def _metals(reports: list[tuple[str, dict]], monday: date) -> list[dict]:
    """Each metal's last quote in the week, against the last quote before it, with the week's range."""
    sunday = monday + timedelta(days=6)
    inside = sorted((d, r) for d, r in reports if monday <= date.fromisoformat(d) <= sunday)
    before = sorted((d, r) for d, r in reports if date.fromisoformat(d) < monday)
    out = []
    names = []
    for _, r in inside:
        for m in r.get("metals") or []:
            if m.get("code") not in names:
                names.append(m.get("code"))
    for code in names:
        prices = [m for _, r in inside for m in r.get("metals") or [] if m.get("code") == code and m.get("price")]
        if not prices:
            continue
        prev = next((m for _, r in reversed(before) for m in r.get("metals") or [] if m.get("code") == code and m.get("price")), None)
        last = prices[-1]
        base = prev["price"] if prev else prices[0].get("prev") or prices[0]["price"]
        out.append({"name": last.get("name", code), "code": code, "unit": last.get("unit", ""), "price": last["price"],
                    "change_pct": round((last["price"] - base) / base * 100, 2) if base else None,
                    "high": max(m["price"] for m in prices), "low": min(m["price"] for m in prices)})
    return out


def build_week(reports: list[tuple[str, dict]], config: Config, monday: date) -> dict:
    sunday = monday + timedelta(days=6)
    days = sorted(((d, r) for d, r in reports if monday <= date.fromisoformat(d) <= sunday), reverse=True)
    role_of = {e.name: e.role for e in config.entities}
    seen, items = set(), []
    for _, r in days:  # newest report first: its copy of a story carries the latest summary
        for it in r.get("items", []):
            if it.get("id") in seen:
                continue
            seen.add(it.get("id"))
            it = dict(it)
            it["kind"] = item_kind(it)
            it["roles"] = [role_of.get(e, "") for e in it.get("entities", [])]
            items.append(it)
    items.sort(key=lambda it: it.get("published") or "", reverse=True)
    events = pick_events(items, config)
    key = sorted((ev for ev in events if ev["kind"] in SIGNAL_KINDS and (ev.get("importance") or 0) >= KEY_IMPORTANCE),
                 key=lambda ev: (ev.get("importance", 0), ev.get("published") or ""), reverse=True)
    signal = [ev for ev in events if ev["kind"] in SIGNAL_KINDS]
    companies = Counter(short_name(e) for it in items for e in it.get("entities", [])
                        if role_of.get(e) not in ("topic", "metals", "recycling", None))
    headlines = []
    for d, r in sorted(days):
        overview = (r.get("digest") or {}).get("overview") or ""
        if overview and (r.get("digest") or {}).get("source") != "auto":
            first = _headline_clause(overview, limit=60) or overview.split("。")[0] + "。"
            headlines.append((d, first))
    return {"monday": monday.isoformat(), "sunday": sunday.isoformat(), "reports": len(days), "items": len(items),
            "events": len(events), "signal": len(signal), "key": key,
            "categories": Counter((ev.get("categories") or ["未分類"])[0] for ev in signal).most_common(),
            "companies": companies.most_common(10), "headlines": headlines, "metals": _metals(reports, monday)}


def _span(week: dict) -> str:
    m, s = date.fromisoformat(week["monday"]), date.fromisoformat(week["sunday"])
    return f"{m:%Y/%m/%d}(一)至 {s:%m/%d}(日)"


def _metal_rows(week: dict) -> str:
    rows = []
    for m in week["metals"]:
        ch = m["change_pct"]
        cls = "up" if ch and ch > 0 else "down" if ch and ch < 0 else ""
        sign = f"{ch:+.2f}%" if ch is not None else "—"
        rows.append(f'<tr><th scope="row">{esc(m["name"])} <span class="tk">{esc(m["code"])}</span></th><td class="num">{m["price"]:,}</td>'
                    f'<td class="num {cls}">{sign}</td><td class="num">{m["low"]:,} – {m["high"]:,}</td><td>{esc(m["unit"])}</td></tr>')
    return "".join(rows)


WEEK_STYLE = """
.mt{width:100%;border-collapse:collapse;font-size:.92rem}.mt th,.mt td{padding:var(--space-xs) var(--space-sm);border-bottom:1px solid var(--line);text-align:left}
.mt thead th{font-size:.78rem;color:var(--muted);font-weight:500}.mt .num{font-family:var(--font-data);text-align:right;white-space:nowrap}
.mt .up{color:var(--up)}.mt .down{color:var(--down)}.daily{margin:0;padding-left:1.2em;display:grid;gap:var(--space-xs)}.daily time{font-family:var(--font-data);color:var(--muted);margin-right:var(--space-xs)}
.weeks{display:flex;flex-wrap:wrap;gap:var(--space-xs)}.weeks a{font-family:var(--font-data);font-size:.85rem}
"""


def render_week(week: dict, config: Config, weeks: list[date], generated: str) -> str:
    key_html = "".join(_item_html(ev) for ev in week["key"]) or "<p>本週沒有評為 4 分以上的重點新聞。</p>"
    metals = (f'<table class="mt"><thead><tr><th>品項</th><th class="num">週末價</th><th class="num">週漲跌</th><th class="num">本週區間</th><th>單位</th></tr></thead>'
              f'<tbody>{_metal_rows(week)}</tbody></table><p class="sub">週漲跌:本週最後一筆收盤價對上週最後一筆;區間為本週每日報告的收盤價高低。</p>'
              if week["metals"] else "<p>本週沒有貴金屬報價。</p>")
    daily = "".join(f'<li><time>{d[5:].replace("-", "/")}({WEEKDAYS[date.fromisoformat(d).weekday()]})</time>{esc(h)}</li>'
                    for d, h in week["headlines"])
    week_links = "".join(f'<a href="week-{m.isoformat()}.html"{" aria-current=page" if m.isoformat() == week["monday"] else ""}>'
                         f'{m:%m/%d} 週</a>' for m in weeks[:12])
    lede = (f"本週蒐集 {week['items']} 則報導,合併為 {week['events']} 個事件,其中重點事件 {len(week['key'])} 件。"
            + (f"報導最多的是 {'、'.join(f'{n}({c})' for n, c in week['companies'][:3])}。" if week["companies"] else ""))
    return (
        _head(f"靶材產業週報 {week['monday']}", "靶材產業每週重點新聞、公司動態與貴金屬週行情。", config.settings.site_url,
              f"week-{week['monday']}.html")
        + f"""<style>{WEEK_STYLE}</style><div class="wrap">
{_mast("", [("#key", "重點事件"), ("#metals", "貴金屬"), ("#daily", "每日焦點"), ("#weeks", "歷次週報")])}
<section class="hero hero-plain" data-enter aria-labelledby="h1"><div class="hero-text">
<p class="dateline">{icon("calendar")}<span>{esc(_span(week))}</span></p>
<h1 id="h1">靶材產業週報</h1>
<p class="lede">{esc(lede)}</p>
<div class="actions"><a class="btn primary" href="#key">看重點事件</a><a class="btn" href="index.html">回到每日新聞</a></div></div></section>
{_figures([(str(week['items']), "本週報導(則)"), (str(week['events']), "新聞事件"), (str(len(week['key'])), "重點事件(4 分以上)"), (str(week['reports']), "每日報告(期)")])}
<section aria-labelledby="key">{_h2("key", "star", "本週重點事件", "評為 4-5 分,依評分與日期排序")}<div class="cards rows">{key_html}</div></section>
<section aria-labelledby="metals">{_h2("metals", "coin", "貴金屬週行情")}{metals}</section>
<div class="pair"><section aria-labelledby="cats">{_h2("cats", "bars", "主題分布", "新聞與公司公告事件")}<div class="ch">{hbar(week['categories'], "件", total=sum(n for _, n in week['categories']) or None)}</div></section>
<section aria-labelledby="cos">{_h2("cos", "building", "報導最多的公司", "本週報導則數")}<div class="ch">{hbar(week['companies'], "則")}</div></section></div>
<section aria-labelledby="daily">{_h2("daily", "calendar", "每日焦點", "各期總結的第一句")}<ol class="daily">{daily or "<li>本週沒有 AI 總結。</li>"}</ol></section>
<section aria-labelledby="weeks">{_h2("weeks", "list", "歷次週報")}<div class="weeks">{week_links}</div></section>
<footer>資料來源為公開新聞,摘要為改寫整理,不構成投資建議。更新於 <span class="mono">{esc(generated)}</span>。</footer>
</div>{_fab("news", config)}</body></html>"""
    )


def build_weekly_pages(reports: list[tuple[str, dict]], config: Config, out: Path, generated: str) -> list[Path]:
    weeks = complete_weeks(reports)
    written = []
    for i, monday in enumerate(weeks):
        page = render_week(build_week(reports, config, monday), config, weeks, generated)
        path = out / f"week-{monday.isoformat()}.html"
        path.write_text(page, encoding="utf-8")
        written.append(path)
        if i == 0:
            (out / "weekly.html").write_text(page, encoding="utf-8")
            written.append(out / "weekly.html")
    return written


# ---- email ---------------------------------------------------------------------------------------

def _e(v) -> str:
    return _html.escape(str(v if v is not None else ""), quote=True)


def email_subject(week: dict) -> str:
    return f"【靶材產業週報】{_span(week)}:重點事件 {len(week['key'])} 件"


def email_html(week: dict, page_url: str = "") -> str:
    """Inline-styled HTML (mail clients drop <style>); colours from the site's light palette."""
    navy, muted, line, up, down, orange = "#173c66", "#4d4e4f", "#dfe1e5", "#c62f2f", "#006300", "#b35a10"
    rows = []
    for m in week["metals"]:
        ch = m["change_pct"]
        color = up if ch and ch > 0 else down if ch and ch < 0 else muted
        rows.append(f'<tr><td style="padding:6px 10px;border-bottom:1px solid {line}">{_e(m["name"])}</td>'
                    f'<td style="padding:6px 10px;border-bottom:1px solid {line};text-align:right">{m["price"]:,}</td>'
                    f'<td style="padding:6px 10px;border-bottom:1px solid {line};text-align:right;color:{color};font-weight:600">{f"{ch:+.2f}%" if ch is not None else "—"}</td>'
                    f'<td style="padding:6px 10px;border-bottom:1px solid {line};text-align:right;color:{muted}">{m["low"]:,} – {m["high"]:,}</td></tr>')
    stories = []
    for ev in week["key"][:MAX_EMAIL_STORIES]:
        title = ev.get("title_zh") or to_traditional(ev.get("title") or "")
        link = ev.get("link") or ""
        title_html = f'<a href="{_e(link)}" style="color:{navy};font-weight:600;text-decoration:none">{_e(title)}</a>' if link.startswith(("https://", "http://")) else _e(title)
        companies = "、".join(dict.fromkeys(short_name(e) for e in ev.get("ev_entities") or ev.get("entities", [])))
        also = f",另有 {len(ev['also'])} 家媒體報導" if ev.get("also") else ""
        reason = why(ev)
        stories.append(
            f'<div style="padding:10px 0;border-bottom:1px solid {line}">'
            f'<div style="font-size:12px;color:{muted}">{_e((ev.get("published") or "")[5:10].replace("-", "/"))} {"★" * (ev.get("importance") or 0)}'
            f' {_e(companies)} {_e(source_name(ev.get("source")))}{_e(also)}</div>{title_html}'
            + (f'<div style="font-size:13px;color:{orange};font-weight:600;margin-top:2px">{_e(reason)}</div>' if reason else "")
            + (f'<div style="font-size:14px;color:{muted};margin-top:4px">{_e(ev.get("summary"))}</div>' if ev.get("summary") else "")
            + "</div>")
    more = len(week["key"]) - MAX_EMAIL_STORIES
    daily = "".join(f'<li style="margin:4px 0"><span style="color:{muted}">{d[5:].replace("-", "/")}</span> {_e(h)}</li>' for d, h in week["headlines"])
    button = (f'<p style="margin:20px 0"><a href="{_e(page_url)}" style="background:{navy};color:#fff;padding:10px 18px;border-radius:6px;'
              f'text-decoration:none;font-weight:600">開啟完整週報</a></p>' if page_url else "")
    top = "、".join(f"{n}({c})" for n, c in week["companies"][:5])
    return f"""<!doctype html><html><body style="margin:0;background:#f3f4f6">
<div style="max-width:680px;margin:0 auto;background:#fbfcfd;padding:24px;font-family:'Noto Sans TC','PingFang TC','Microsoft JhengHei',Arial,sans-serif;color:#131415;line-height:1.6">
<div style="border-top:4px solid {navy};padding-top:12px;font-size:13px;color:{muted}">{_e(_span(week))}</div>
<h1 style="font-size:22px;margin:6px 0 8px">靶材產業週報</h1>
<p style="margin:0 0 4px">本週 {week['items']} 則報導,合併為 {week['events']} 個事件,重點事件 {len(week['key'])} 件。</p>
{f'<p style="margin:0;color:{muted};font-size:14px">報導最多:{_e(top)}</p>' if top else ""}
{button}
<h2 style="font-size:17px;margin:24px 0 4px;color:{navy}">本週重點事件</h2>
{"".join(stories) or f'<p style="color:{muted}">本週沒有評為 4 分以上的重點新聞。</p>'}
{f'<p style="font-size:13px;color:{muted}">另有 {more} 件重點事件,請見完整週報。</p>' if more > 0 else ""}
<h2 style="font-size:17px;margin:24px 0 4px;color:{navy}">貴金屬週行情(USD/oz)</h2>
<table style="border-collapse:collapse;width:100%;font-size:14px"><tr style="color:{muted};font-size:12px"><th style="text-align:left;padding:6px 10px">品項</th><th style="text-align:right;padding:6px 10px">週末價</th><th style="text-align:right;padding:6px 10px">週漲跌</th><th style="text-align:right;padding:6px 10px">本週區間</th></tr>{"".join(rows)}</table>
{f'<h2 style="font-size:17px;margin:24px 0 4px;color:{navy}">每日焦點</h2><ul style="padding-left:18px;margin:0">{daily}</ul>' if daily else ""}
<p style="font-size:12px;color:{muted};margin-top:28px">資料來源為公開新聞,摘要為改寫整理,不構成投資建議。此信由靶材新聞 Agent 每週一自動寄出。</p>
</div></body></html>"""


def email_text(week: dict, page_url: str = "") -> str:
    lines = [f"靶材產業週報 {_span(week)}", f"本週 {week['items']} 則報導,合併為 {week['events']} 個事件,重點事件 {len(week['key'])} 件。", ""]
    if page_url:
        lines += [f"完整週報:{page_url}", ""]
    lines.append("■ 本週重點事件")
    for ev in week["key"][:MAX_EMAIL_STORIES]:
        title = ev.get("title_zh") or to_traditional(ev.get("title") or "")
        lines.append(f"・{(ev.get('published') or '')[5:10].replace('-', '/')} {'★' * (ev.get('importance') or 0)} {title}")
        if why(ev):
            lines.append(f"  {why(ev)}")
        if ev.get("link"):
            lines.append(f"  {ev['link']}")
    lines += ["", "■ 貴金屬週行情"]
    for m in week["metals"]:
        ch = f"{m['change_pct']:+.2f}%" if m["change_pct"] is not None else "—"
        lines.append(f"・{m['name']} {m['price']:,} {m['unit']} 週漲跌 {ch}(區間 {m['low']:,} – {m['high']:,})")
    lines += ["", "資料來源為公開新聞,摘要為改寫整理,不構成投資建議。"]
    return "\n".join(lines)
