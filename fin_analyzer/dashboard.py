"""Cross-company financial dashboard for sputtering-target makers.

Builds one JSON document (latest metrics, quarterly trends, Taiwan monthly revenue,
signals) with amounts converted to a single display currency, and renders it into a
self-contained interactive HTML page (no external scripts).
"""

from __future__ import annotations

import csv
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from .analysis import POSITIVE, RISK, WATCH, Analysis
from .config import FinConfig
from .logos import logo_data_uri
from .metrics import AMOUNT_KEYS

log = logging.getLogger(__name__)

TEMPLATE = Path(__file__).with_name("dashboard_template.html")
OG_IMAGE = Path(__file__).with_name("assets") / "og.png"  # 1200x630 share-preview image

# key, label, kind (pct / x / days / amt / num / score), better (high / low / None), group
DASHBOARD_METRICS: list[tuple[str, str, str, str | None, str]] = [
    ("revenue_ttm", "近四季營收", "amt", "high", "規模與成長"),
    ("revenue_ttm_yoy", "近四季營收年增", "pct", "high", "規模與成長"),
    ("revenue_yoy", "單季營收年增", "pct", "high", "規模與成長"),
    ("market_cap", "市值", "amt", "high", "規模與成長"),
    ("gross_margin_ttm", "毛利率", "pct", "high", "獲利能力"),
    ("operating_margin_ttm", "營業利益率", "pct", "high", "獲利能力"),
    ("net_margin_ttm", "淨利率", "pct", "high", "獲利能力"),
    ("roe_ttm", "ROE", "pct", "high", "獲利能力"),
    ("roa_ttm", "ROA", "pct", "high", "獲利能力"),
    ("asset_turnover_ttm", "總資產週轉率", "x", "high", "營運效率"),
    ("dso", "應收帳款天數", "days", "low", "營運效率"),
    ("dio", "存貨天數", "days", "low", "營運效率"),
    ("ccc", "現金轉換循環", "days", "low", "營運效率"),
    ("current_ratio", "流動比率", "x", "high", "財務結構"),
    ("debt_ratio", "負債比率", "pct", "low", "財務結構"),
    ("net_debt_to_equity", "淨負債/權益", "pct", "low", "財務結構"),
    ("interest_coverage_ttm", "利息保障倍數", "x", "high", "財務結構"),
    ("ocf_to_net_income_ttm", "營業現金流/淨利", "x", "high", "現金流量"),
    ("fcf_margin_ttm", "自由現金流/營收", "pct", "high", "現金流量"),
    ("capex_to_revenue_ttm", "資本支出/營收", "pct", None, "現金流量"),
    ("pe_ttm", "本益比", "x", "low", "評價"),
    ("pb", "股價淨值比", "x", "low", "評價"),
    ("ev_ebitda_ttm", "EV/EBITDA", "x", "low", "評價"),
    ("fscore", "Piotroski F-score", "score", "high", "綜合評分"),
    ("zscore", "Altman Z''", "x", "high", "綜合評分"),
]

TREND_KEYS = ["revenue", "revenue_yoy", "gross_margin", "operating_margin", "net_margin", "roe_ttm", "dio", "debt_ratio"]
TREND_LABELS = {
    "revenue": ("單季營收", "amt"),
    "revenue_yoy": ("單季營收年增", "pct"),
    "gross_margin": ("單季毛利率", "pct"),
    "operating_margin": ("單季營業利益率", "pct"),
    "net_margin": ("單季淨利率", "pct"),
    "roe_ttm": ("ROE(近四季)", "pct"),
    "dio": ("存貨天數", "days"),
    "debt_ratio": ("負債比率", "pct"),
}


def load_fx(cfg: FinConfig) -> dict:
    """FX rates to the display currency: data_dir/fx.json (written by `fetch`) over config defaults."""
    rates = dict(cfg.fx_rates)
    source, asof = "設定檔參考匯率", ""
    path = Path(cfg.data_dir) / "fx.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("base", cfg.display_currency) == cfg.display_currency and saved.get("rates"):
            rates.update({k.upper(): float(v) for k, v in saved["rates"].items()})
            source, asof = saved.get("source", "Yahoo Finance"), saved.get("asof", "")
    rates[cfg.display_currency] = 1.0
    return {"base": cfg.display_currency, "rates": rates, "source": source, "asof": asof}


def _convert(value: float | None, rate: float | None) -> float | None:
    return None if value is None or rate is None else value * rate


def company_entry(a: Analysis, fx: dict, unit: float, focus: bool) -> dict:
    c = a.company
    currency = a.data.currency or (c.currency if c else "")
    rate = fx["rates"].get(currency)
    if rate is None:
        log.warning("%s: no FX rate for %s; amounts left blank", a.data.ticker, currency)
    m = a.latest_metrics
    latest: dict[str, float | None] = {}
    for key, *_ in DASHBOARD_METRICS:
        if key == "fscore":
            v = float(a.fscore.score) if a.fscore.score is not None else None
        elif key == "zscore":
            v = a.zscore.value
        elif key in a.valuation:
            v = a.valuation[key]
        else:
            v = m.get(key)
        if key in AMOUNT_KEYS and v is not None:
            v = _convert(v, rate)
            v = None if v is None else v / unit
        latest[key] = v

    trend = {}
    for p in a.data.periods[-12:]:
        row = {}
        for k in TREND_KEYS:
            v = a.quarterly[p].get(k)
            if k in AMOUNT_KEYS and v is not None:
                v = _convert(v, rate)
                v = None if v is None else v / unit
            row[k] = v
        trend[p] = row

    monthly = {mo: {"yoy": a.monthly[mo].get("yoy"), "revenue": a.monthly[mo]["revenue"] / unit} for mo in sorted(a.monthly)[-24:]}
    counts = {lvl: sum(s.level == lvl for s in a.signals) for lvl in (RISK, WATCH, POSITIVE)}
    return {
        "ticker": a.data.ticker,
        "name": a.data.name or (c.name if c else a.data.ticker),
        "short": c.short if c else "",
        "logo": logo_data_uri(a.data.ticker),
        "market": c.market if c else "",
        "region": c.region if c else "",
        "currency": currency,
        "fx_rate": rate,
        "target_focus": c.target_focus if c else "",
        "focus_label": c.focus_label if c else "",
        "segment": a.segment,
        "latest_quarter": a.latest,
        "yoy_quarters": int(m["revenue_yoy_quarters"]) if m.get("revenue_yoy_quarters") else None,
        "close": a.valuation.get("close"),
        "close_date": a.valuation.get("close_date"),
        "is_focus": focus,
        "note": a.data.note,
        "latest": latest,
        "trend": trend,
        "monthly": monthly if currency == "TWD" else {},
        "signals": [{"level": s.level, "topic": s.topic, "message": s.message} for s in a.signals],
        "signal_counts": counts,
        "fscore_details": [[name, ok] for name, ok in a.fscore.details],
        "zone": a.zscore.zone,
    }


def build_dashboard_data(analyses: list[Analysis], cfg: FinConfig, generated: datetime, missing: list[str] | None = None) -> dict:
    fx = load_fx(cfg)
    focus = cfg.target.ticker
    return {
        "title": cfg.title or "靶材製造商財務指標儀表板",
        "news_url": cfg.news_url,
        "site_url": cfg.site_url,
        "generated": generated.strftime("%Y-%m-%d %H:%M"),
        "display_currency": cfg.display_currency,
        "unit_label": f"億{_currency_label(cfg.display_currency)}" if cfg.amount_unit == 1e8 else cfg.amount_unit_label,
        "fx": fx,
        "metrics": [{"key": k, "label": lbl, "kind": kind, "better": better, "group": grp} for k, lbl, kind, better, grp in DASHBOARD_METRICS],
        "trend_metrics": [{"key": k, "label": TREND_LABELS[k][0], "kind": TREND_LABELS[k][1]} for k in TREND_KEYS],
        "companies": [company_entry(a, fx, cfg.amount_unit, a.data.ticker == focus) for a in analyses],
        "missing": missing or [],
        "industry_notes": cfg.industry_notes,
    }


def _currency_label(cur: str) -> str:
    return {"TWD": "台幣", "USD": "美元", "JPY": "日圓", "CNY": "人民幣"}.get(cur, cur)


def description(data: dict) -> str:
    regions = "".join(r for r in ("台", "日", "美", "中") if any(c["region"].startswith(r) for c in data["companies"]))
    who = f"{regions} {len(data['companies'])} 家" if regions else f"{len(data['companies'])} 家"
    return f"比較{who}濺鍍靶材製造商的營收成長、毛利率、ROE、現金流與評價,附靶材營收比重標示與自動風險訊號。"


def share_meta(data: dict) -> str:
    """Absolute og:image / og:url tags (crawlers ignore relative URLs); empty without site_url."""
    base = (data.get("site_url") or "").rstrip("/")
    if not base:
        return ""
    image = _html_escape(f"{base}/{OG_IMAGE.name}")
    return (f'<meta property="og:url" content="{_html_escape(base + "/")}">\n'
            f'<meta property="og:image" content="{image}">\n'
            '<meta property="og:image:width" content="1200">\n<meta property="og:image:height" content="630">\n'
            f'<meta name="twitter:image" content="{image}">')


def render_html(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")  # safe inside <script>
    html = TEMPLATE.read_text(encoding="utf-8")
    return (html.replace("__TITLE__", _html_escape(data["title"]))
            .replace("__DESCRIPTION__", _html_escape(description(data)))
            .replace("<!--__SHARE_META__-->", share_meta(data))
            .replace("/*__DATA__*/null", payload)
            .replace("<!--__FAB__-->", page_switcher(data)))


def page_switcher(data: dict) -> str:
    """The news site's floating page switcher; the site root is the folder above news_url (…/news/)."""
    news = (data.get("news_url") or "").rstrip("/")
    if not news:
        return ""
    from target_news_agent.fabnav import fab_nav
    return fab_nav("financials", news.rsplit("/", 1)[0] + "/", news_site_skip())


def news_site_skip(path: Path = Path(__file__).resolve().parents[1] / "config" / "watchlist.yaml") -> set[str]:
    """Pages the news site doesn't build (no forecasts / quiz configured), so the switcher doesn't link to a 404."""
    from target_news_agent.config import load_config
    try:
        config = load_config(path)
    except (OSError, ValueError):
        return set()
    return ({"forecast"} if not config.forecasts else set()) | ({"quiz"} if not config.quiz else set())


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def write_dashboard(data: dict, out_dir: str | Path) -> tuple[Path, Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    html_path, json_path, csv_path = out / "index.html", out / "dashboard.json", out / "comparison.csv"
    html_path.write_text(render_html(data), encoding="utf-8")
    if OG_IMAGE.exists():
        shutil.copyfile(OG_IMAGE, out / OG_IMAGE.name)
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    metrics = data["metrics"]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["代號", "公司", "市場", "幣別", "靶材比重", "最新季", *(
            f"{m['label']}({data['unit_label']})" if m["kind"] == "amt" else m["label"] for m in metrics)])
        for c in data["companies"]:
            w.writerow([c["ticker"], c["name"], c["region"], c["currency"], c["focus_label"], c["latest_quarter"],
                        *("" if c["latest"].get(m["key"]) is None else round(c["latest"][m["key"]], 6) for m in metrics)])
    return html_path, json_path, csv_path
