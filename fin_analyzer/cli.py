"""Command line entry point: `python -m fin_analyzer fetch|analyze`."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .analysis import analyze
from .commentary import Commentary, Commentator, llm_available, llm_payload
from .config import Company, load_fin_config
from .models import load_company, save_company
from .report import write_reports
from .sources import fetch_company, http_fetch

log = logging.getLogger("fin_analyzer")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fin_analyzer", description="標的公司財報分析工具(台股)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="從 FinMind 下載財報、月營收與股價")
    fetch.add_argument("-c", "--config", default="config/fin_targets.yaml")
    fetch.add_argument("--tickers", nargs="+", help="只下載這些代號(預設 target + peers)")
    fetch.add_argument("--start", help="起始日期,預設取設定檔 start_date")
    fetch.add_argument("--data-dir", help="覆寫資料資料夾")

    an = sub.add_parser("analyze", help="分析標的公司並產生報告")
    an.add_argument("-c", "--config", default="config/fin_targets.yaml")
    an.add_argument("--target", help="覆寫標的代號")
    an.add_argument("--peers", nargs="*", help="覆寫同業代號(不帶值 = 不做同業比較)")
    an.add_argument("--data-dir", help="覆寫資料資料夾")
    an.add_argument("-o", "--out", help="報告輸出資料夾")
    an.add_argument("--no-llm", action="store_true", help="不使用 Claude 評論")
    an.add_argument("--model", help="Claude 模型 ID(預設 claude-opus-5-5 或 FIN_ANALYZER_MODEL)")
    an.add_argument("--tz", default="Asia/Taipei")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    cfg = load_fin_config(args.config)
    if args.data_dir:
        cfg.data_dir = args.data_dir

    if args.command == "fetch":
        known = {c.ticker: c for c in cfg.companies}
        tickers = args.tickers or list(known)
        failed = []
        for t in tickers:
            try:
                data = fetch_company(
                    t, args.start or cfg.start_date, http_fetch,
                    field_map=cfg.finmind_field_map, ytd=cfg.finmind_ytd,
                    name=known[t].name if t in known else "",
                )
            except Exception as exc:
                log.error("%s: fetch failed: %s", t, exc)
                failed.append(t)
                continue
            folder = save_company(data, cfg.data_dir)
            print(f"{data.label}: {len(data.periods)} 季、{len(data.monthly_revenue)} 個月營收 -> {folder}")
        return 1 if failed and len(failed) == len(tickers) else 0

    # analyze
    if args.target:
        cfg.target = next((c for c in cfg.companies if c.ticker == args.target), Company(args.target))
    if args.peers is not None:
        known = {c.ticker: c for c in cfg.peers}
        cfg.peers = [known.get(t, Company(t)) for t in args.peers]
    cfg.peers = [c for c in cfg.peers if c.ticker != cfg.target.ticker]

    target = analyze(load_company(cfg.target.ticker, cfg.data_dir, cfg.target.name), cfg, cfg.target.segment)
    peers = []
    for c in cfg.peers:
        try:
            peers.append(analyze(load_company(c.ticker, cfg.data_dir, c.name), cfg, c.segment))
        except (FileNotFoundError, ValueError) as exc:
            log.warning("peer %s skipped: %s", c.ticker, exc)

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


if __name__ == "__main__":
    sys.exit(main())
