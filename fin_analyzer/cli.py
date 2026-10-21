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
from .dashboard import build_dashboard_data, write_dashboard
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
    return fetch_yahoo(c.ticker, name=c.name, currency=c.currency)


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

    dash = sub.add_parser("dashboard", help="產生跨公司比較的互動 HTML 儀表板")
    dash.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    dash.add_argument("--focus", help="覆寫預設強調的公司代號")
    dash.add_argument("--data-dir", help="覆寫資料資料夾")
    dash.add_argument("-o", "--out", help="輸出資料夾(預設 settings.dashboard_dir)")
    dash.add_argument("--tz", default="Asia/Taipei")

    lg = sub.add_parser("logos", help="下載各公司官網圖示到 fin_analyzer/assets/logos/(已有檔案的公司略過)")
    lg.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    lg.add_argument("--force", action="store_true", help="已有圖示的公司也重新下載")

    an = sub.add_parser("analyze", help="針對單一公司產生深度財報分析報告(Markdown)")
    an.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    an.add_argument("--target", help="覆寫分析對象代號(預設 focus)")
    an.add_argument("--peers", nargs="*", help="覆寫同業代號(不帶值 = 不做同業比較)")
    an.add_argument("--data-dir", help="覆寫資料資料夾")
    an.add_argument("-o", "--out", help="報告輸出資料夾")
    an.add_argument("--no-llm", action="store_true", help="不使用 Claude 評論")
    an.add_argument("--model", help="Claude 模型 ID(預設 claude-opus-5-5 或 FIN_ANALYZER_MODEL)")
    an.add_argument("--tz", default="Asia/Taipei")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    cfg = load_fin_config(args.config)
    if getattr(args, "data_dir", None):
        cfg.data_dir = args.data_dir
    return {"fetch": cmd_fetch, "dashboard": cmd_dashboard, "analyze": cmd_analyze, "logos": cmd_logos}[args.command](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
