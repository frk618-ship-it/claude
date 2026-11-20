"""Command line entry point: `python -m fin_analyzer fetch|dashboard|analyze`."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .analysis import analyze
from .commentary import Commentary, Commentator, llm_available, llm_payload
from .config import Company, FinConfig, load_fin_config
from .dashboard import build_dashboard_data, refresh_switcher, write_dashboard
from .logos import fetch_logos
from .models import load_company, save_company
from .report import write_reports
from .sources import fetch_company, http_fetch
from .yahoo import fetch_fx, fetch_yahoo

log = logging.getLogger("fin_analyzer")

DEFAULT_CONFIG = "config/target_makers.yaml"


def fetch_one(c: Company, cfg: FinConfig, start: str):
    """Taiwan tickers go to FinMind (with monthly revenue); everything else to Yahoo Finance."""
    if c.market == "TW":
        return fetch_company(c.ticker, start, http_fetch, field_map=cfg.finmind_field_map, ytd=cfg.finmind_ytd, name=c.name)
    previous = None
    try:
        previous = load_company(c.ticker, cfg.data_dir, c.name)
    except (FileNotFoundError, ValueError):
        pass  # first fetch
    return fetch_yahoo(c.ticker, name=c.name, currency=c.currency, previous=previous)


def _complete_trade_lines(data, cfg: FinConfig, online: bool = True) -> int:
    """Taiwan: receivables / payables with notes and related parties from MOPS (FinMind's sheet is condensed)."""
    from .mops import apply_cache, load_cache, update_cache

    folder = Path(cfg.data_dir) / data.ticker
    cache = load_cache(folder)
    if online:
        try:
            cache = update_cache(folder, data.ticker, data.periods)
        except Exception as exc:  # MOPS unreachable: keep what is cached
            log.warning("%s: MOPS trade lines not updated: %s", data.ticker, exc)
    return apply_cache(data, cache)


def cmd_mops_trade(args, cfg: FinConfig) -> int:
    """Update saved Taiwan companies' receivables / payables from MOPS without refetching FinMind."""
    tw = [c for c in cfg.companies if c.market == "TW" and (not args.tickers or c.ticker in args.tickers)]
    for c in tw:
        try:
            data = load_company(c.ticker, cfg.data_dir, c.name, c.par_value)
        except (FileNotFoundError, ValueError) as exc:
            log.warning("%s skipped: %s", c.ticker, exc)
            continue
        n = _complete_trade_lines(data, cfg)
        save_company(data, cfg.data_dir)
        print(f"{data.label}: {n} 季的應收/應付帳款及票據已依公開資訊觀測站更新")
    return 0


def cmd_fetch(args, cfg: FinConfig) -> int:
    known = {c.ticker: c for c in cfg.companies}
    tickers = args.tickers or list(known)
    failed = []
    for t in tickers:
        c = known.get(t, Company(t))
        try:
            data = fetch_one(c, cfg, args.start or cfg.start_date)
        except Exception as exc:
            log.error("%s: fetch failed: %s", t, exc)
            failed.append(t)
            continue
        if c.market == "TW":
            _complete_trade_lines(data, cfg, online=not args.no_mops)
        folder = save_company(data, cfg.data_dir)
        print(f"{data.label}[{c.market}/{data.currency}]: {len(data.periods)} 季、{len(data.monthly_revenue)} 個月營收 -> {folder}")

    currencies = sorted({c.currency for c in cfg.companies if c.currency != cfg.display_currency})
    if currencies and not args.no_fx:
        try:
            rates = fetch_fx(currencies, cfg.display_currency)
            if rates:
                path = save_fx(cfg, rates)
                print(f"匯率 {rates} -> {path}")
        except Exception as exc:
            log.warning("FX fetch failed (config fx_rates will be used): %s", exc)
    return 1 if failed and len(failed) == len(tickers) else 0


def save_fx(cfg: FinConfig, rates: dict[str, float]):
    path = Path(cfg.data_dir) / "fx.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"base": cfg.display_currency, "rates": rates, "source": "Yahoo Finance",
               "asof": datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_analyses(cfg: FinConfig, companies: list[Company]):
    analyses, missing = [], []
    for c in companies:
        try:
            analyses.append(analyze(load_company(c.ticker, cfg.data_dir, c.name, c.par_value), cfg, c))
        except (FileNotFoundError, ValueError) as exc:
            log.warning("%s skipped: %s", c.ticker, exc)
            missing.append(f"{c.name or c.ticker}({c.ticker})")
    return analyses, missing


def cmd_dashboard(args, cfg: FinConfig) -> int:
    if args.focus:
        match = next((c for c in cfg.companies if c.ticker == args.focus), None)
        if match is None:
            raise SystemExit(f"--focus {args.focus} is not in the config")
        cfg.peers = [c for c in cfg.companies if c is not match]
        cfg.target = match
    analyses, missing = load_analyses(cfg, cfg.companies)
    if not analyses:
        log.error("no company data in %s; run `python -m fin_analyzer fetch` first", cfg.data_dir)
        return 1
    data = build_dashboard_data(analyses, cfg, datetime.now(ZoneInfo(args.tz)), missing)
    paths = write_dashboard(data, args.out or cfg.dashboard_dir)
    print(f"儀表板:{len(analyses)} 家公司(缺 {len(missing)} 家)-> " + " , ".join(map(str, paths)))
    return 0


def cmd_analyze(args, cfg: FinConfig) -> int:
    if args.target:
        cfg.target = next((c for c in cfg.companies if c.ticker == args.target), Company(args.target))
    if args.peers is not None:
        known = {c.ticker: c for c in cfg.companies}
        cfg.peers = [known.get(t, Company(t)) for t in args.peers]
    cfg.peers = [c for c in cfg.peers if c.ticker != cfg.target.ticker]

    target = analyze(load_company(cfg.target.ticker, cfg.data_dir, cfg.target.name, cfg.target.par_value), cfg, cfg.target)
    peers, _ = load_analyses(cfg, cfg.peers)
    # The Markdown peer table compares ratios only; amounts are in each company's own currency.
    commentary = Commentary()
    if not args.no_llm:
        if llm_available():
            payload = llm_payload(target, peers, cfg.industry_notes, cfg.amount_unit)
            commentary = Commentator(model=args.model).comment(payload, cfg.amount_unit_label)
        else:
            log.info("ANTHROPIC_API_KEY not set; report generated without Claude commentary")

    generated = datetime.now(ZoneInfo(args.tz))
    paths = write_reports(target, peers, cfg, commentary, args.out or cfg.report_dir, generated)
    print(f"{target.data.label} {target.latest}:{len(target.signals)} 個訊號、同業 {len(peers)} 家 -> " + " , ".join(map(str, paths)))
    return 0


def cmd_logos(args, cfg: FinConfig) -> int:
    status = fetch_logos(cfg.companies, force=args.force)
    for ticker, st in status.items():
        print(f"{ticker:<10} {st}")
    return 0


def cmd_switcher(args, cfg: FinConfig) -> int:
    for path in args.html:
        print(f"{path}: {'page switcher refreshed' if refresh_switcher(path) else 'no switcher markers, left as is'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fin_analyzer", description="靶材製造商財務指標儀表板與財報分析工具")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="下載財報、月營收、股價與匯率(台股 FinMind,其他 Yahoo Finance)")
    fetch.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    fetch.add_argument("--tickers", nargs="+", help="只下載這些代號(預設設定檔中所有公司)")
    fetch.add_argument("--start", help="FinMind 起始日期,預設取設定檔 start_date")
    fetch.add_argument("--data-dir", help="覆寫資料資料夾")
    fetch.add_argument("--no-fx", action="store_true", help="不更新匯率")
    fetch.add_argument("--no-mops", action="store_true", help="台股不連公開資訊觀測站補應付票據等科目(只套用已快取的)")

    mt = sub.add_parser("mops-trade", help="台股:以公開資訊觀測站完整資產負債表更新應收/應付帳款及票據(含關係人),不重抓 FinMind")
    mt.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    mt.add_argument("--tickers", nargs="+")
    mt.add_argument("--data-dir", help="覆寫資料資料夾")

    dash = sub.add_parser("dashboard", help="產生跨公司比較的互動 HTML 儀表板")
    dash.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    dash.add_argument("--focus", help="覆寫預設強調的公司代號")
    dash.add_argument("--data-dir", help="覆寫資料資料夾")
    dash.add_argument("-o", "--out", help="輸出資料夾(預設 settings.dashboard_dir)")
    dash.add_argument("--tz", default="Asia/Taipei")

    lg = sub.add_parser("logos", help="下載各公司官網圖示到 fin_analyzer/assets/logos/(已有檔案的公司略過)")
    lg.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    lg.add_argument("--force", action="store_true", help="已有圖示的公司也重新下載")

    sw = sub.add_parser("switcher", help="把已產生的儀表板 HTML 的切換頁面按鈕換成網站目前的頁面清單(發布時用)")
    sw.add_argument("html", nargs="+", help="儀表板 index.html(同資料夾需有 dashboard.json)")
    sw.add_argument("-c", "--config", default=DEFAULT_CONFIG)

    an = sub.add_parser("analyze", help="針對單一公司產生深度財報分析報告(Markdown)")
    an.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    an.add_argument("--target", help="覆寫分析對象代號(預設 focus)")
    an.add_argument("--peers", nargs="*", help="覆寫同業代號(不帶值 = 不做同業比較)")
    an.add_argument("--data-dir", help="覆寫資料資料夾")
    an.add_argument("-o", "--out", help="報告輸出資料夾")
    an.add_argument("--no-llm", action="store_true", help="不使用 Claude 評論")
    an.add_argument("--model", help="Claude 模型 ID(預設 claude-opus-5-5 或 FIN_ANALYZER_MODEL)")
    an.add_argument("--tz", default="Asia/Taipei")

    it = sub.add_parser("items", help="列出台股公司 FinMind 原始報表科目(檢查某個欄位對應到哪些科目;需要網路)")
    it.add_argument("ticker", help="台股代號,例如 3663")
    it.add_argument("--statement", choices=["balance", "income", "cashflow"], default="balance")
    it.add_argument("--match", default="", help="只列出 type 或中文科目名稱含這段文字(regex)的科目")
    it.add_argument("--start", default="", help="起始日期(預設最近約 15 個月)")
    it.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    it.add_argument("--source", choices=["finmind", "mops"], default="finmind",
                    help="mops = 公開資訊觀測站完整資產負債表(含應付票據、關係人科目)")
    it.add_argument("--quarter", default="", help="mops 用:季度,例如 2026Q3(預設最近兩個已公布的季度)")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    cfg = load_fin_config(args.config)
    if getattr(args, "data_dir", None):
        cfg.data_dir = args.data_dir
    return {"fetch": cmd_fetch, "dashboard": cmd_dashboard, "analyze": cmd_analyze, "logos": cmd_logos,
            "switcher": cmd_switcher, "items": cmd_items, "mops-trade": cmd_mops_trade}[args.command](args, cfg)


def _recent_quarters(today, n: int = 2) -> list[tuple[int, int]]:
    """The n latest quarters whose statements are due (45 days after quarter end; Q4 in March)."""
    from datetime import date, timedelta

    q, y, out = (today.month - 1) // 3 + 1, today.year, []
    while len(out) < n:
        q -= 1
        if q == 0:
            q, y = 4, y - 1
        end = date(y, q * 3, 28) + timedelta(days=4)
        due = end + timedelta(days=90 if q == 4 else 46)
        if due <= today:
            out.append((y, q))
    return out


def _items_mops(args) -> int:
    import re
    from datetime import date

    from .mops import fetch_balance, trade_totals

    m = re.fullmatch(r"(\d{4})Q([1-4])", args.quarter.upper()) if args.quarter else None
    quarters = [(int(m.group(1)), int(m.group(2)))] if m else _recent_quarters(date.today())
    pat = re.compile(args.match) if args.match else None
    ok = False
    for y, q in quarters:
        lines, src = fetch_balance(args.ticker, y, q)
        print(f"== {args.ticker} {y}Q{q} 公開資訊觀測站資產負債表 ({len(lines)} 個科目;單位:元) 來源:{src}")
        if not lines:
            continue
        ok = True
        for k, v in lines.items():
            if not pat or pat.search(k):
                print(f"{k}\t{v:,.0f}")
        t = trade_totals(lines)
        print("合計:應收帳款及票據(含關係人)\t" + (f"{t['receivables']:,.0f}" if t["receivables"] is not None else "—"))
        print("合計:應付帳款及票據(含關係人)\t" + (f"{t['payables']:,.0f}" if t["payables"] is not None else "—"))
    return 0 if ok else 1


def cmd_items(args, cfg) -> int:
    """Raw FinMind line items, period by period, so a field mapping can be checked against the statements."""
    import re
    from datetime import date, timedelta

    from .sources import DATASETS, http_fetch

    if args.source == "mops":
        return _items_mops(args)
    start = args.start or (date.today() - timedelta(days=460)).isoformat()
    rows = http_fetch(DATASETS[args.statement], {"data_id": args.ticker, "start_date": start})
    pat = re.compile(args.match) if args.match else None
    periods = sorted({r["date"] for r in rows})
    table: dict[tuple[str, str], dict[str, float]] = {}
    for r in rows:
        name = str(r.get("origin_name") or "")
        if pat and not (pat.search(str(r["type"])) or pat.search(name)):
            continue
        table.setdefault((str(r["type"]), name), {})[r["date"]] = r["value"]
    print(f"{args.ticker} {args.statement} ({len(table)} 個科目;單位:元)")
    print("\t".join(["type", "科目", *periods]))
    for (typ, name), vals in sorted(table.items()):
        print("\t".join([typ, name, *(f"{vals[p]:,.0f}" if isinstance(vals.get(p), (int, float)) else "" for p in periods)]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
