"""Command line: `python -m lead_radar run | analyze | import | queries | site | demo`."""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path

from .config import load_config
from .page import write_site
from .report import build_report, latest_report, write_reports
from .signals import dedupe, reclassify
from .sources import iter_urls, load_csv
from .store import SignalStore

log = logging.getLogger("lead_radar")


def _today() -> date:
    # Reports are dated in Taipei time, like the news reports.
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Taipei")).date()


def _enrich(signals, cfg, enabled: bool, model: str | None) -> None:
    from .enrich import Enricher, llm_available

    if not enabled or not llm_available():
        return
    try:
        n = Enricher(cfg, model=model).enrich(signals)
        log.info("Claude enriched %d signals", n)
    except Exception as exc:  # enrichment is optional: never fail the run
        log.warning("Claude enrichment unavailable: %s", exc)


def _coverage_key(segment: str | None) -> str:
    return f"coverage_start:{segment}" if segment else "coverage_start"


def _analyse_and_write(store: SignalStore, cfg, asof: date, out: Path, site: str | None, segment: str | None = None) -> dict:
    cov = store.get_meta(_coverage_key(segment))
    cov = date.fromisoformat(cov) if cov else None
    signals = [s for s in (reclassify(x, cfg) for x in store.all()) if s is not None]   # current rules apply to history too
    if segment:
        from .segment import build_segment_report, render_accounts_csv, render_segment_markdown

        rep = build_segment_report(signals, cfg, segment, asof, coverage_start=cov, first_seen=store.first_seen())
        paths = write_reports(rep, out)
        paths[1].write_text(render_segment_markdown(rep), encoding="utf-8")
        acc = out / f"{rep['asof']}-accounts.csv"
        acc.write_text(render_accounts_csv(rep), encoding="utf-8")
        paths.append(acc)
    else:
        rep = build_report(signals, cfg, asof, coverage_start=cov, first_seen=store.first_seen())
        paths = write_reports(rep, out)
    if site:
        paths.append(write_site(rep, site))
    for p in paths:
        print(p)
    _summary(rep)
    return rep


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lead_radar", description="商機雷達:以熱點分析找出正在尋找靶材、純鈦 / 鈦合金、鎳基合金的潛在客戶")
    p.add_argument("-c", "--config", default="config/lead_radar.yaml")
    p.add_argument("--db", default="data/lead_radar/signals.sqlite3", help="需求訊號資料庫")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    seg_help = "只分析單一客群(設定檔 segments 的 key,例如 tw_semi_equipment);報告預設輸出到 reports/leads/segments/<key>/"

    r = sub.add_parser("run", help="蒐集新聞訊號 → 熱點分析 → 潛在客戶名單(排程用)")
    r.add_argument("--days", type=int, help="蒐集最近 N 天(預設見設定檔)")
    r.add_argument("--no-llm", action="store_true", help="不呼叫 Claude 判讀")
    r.add_argument("--model", help="Claude 模型(預設 claude-opus-5-5,或環境變數 LEAD_RADAR_MODEL)")
    r.add_argument("-o", "--out")
    r.add_argument("--site", help="同時輸出網頁到此資料夾,例如 site/leads")
    r.add_argument("--segment", help=seg_help)

    a = sub.add_parser("analyze", help="不連網,用資料庫既有訊號重新分析")
    a.add_argument("--asof", help="分析基準日 YYYY-MM-DD(預設今天)")
    a.add_argument("-o", "--out")
    a.add_argument("--site")
    a.add_argument("--segment", help=seg_help)

    i = sub.add_parser("import", help="匯入 CSV 訊號(展會名單、詢價紀錄、標案、海關資料)")
    i.add_argument("csv", nargs="+")
    i.add_argument("--no-llm", action="store_true")
    i.add_argument("--model")

    qp = sub.add_parser("queries", help="列出會抓取的搜尋網址(不連網)")
    qp.add_argument("--segment", help=seg_help)

    s = sub.add_parser("site", help="用最新一份報告產生靜態網頁")
    s.add_argument("-i", "--input", default="reports/leads")
    s.add_argument("-o", "--out", default="site/leads")
    s.add_argument("--nav-root", default="../", help="網站根目錄相對於此頁的路徑(客群頁在 leads/<key>/ 下時用 ../../)")

    d = sub.add_parser("demo", help="用虛構示範資料產生報告與網頁(離線)")
    d.add_argument("--csv", default="examples/lead_radar_demo.csv")
    d.add_argument("--asof", default="2026-11-02")
    d.add_argument("-o", "--out", default="examples/lead_radar_demo")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    segment = getattr(args, "segment", None)
    if segment and segment not in cfg.segments:
        log.error("找不到客群 %s;可用:%s", segment, "、".join(cfg.segments) or "(設定檔沒有 segments)")
        return 2
    if hasattr(args, "out") and args.out is None:
        args.out = f"reports/leads/segments/{segment}" if segment else "reports/leads"

    if args.command == "queries":
        if segment:
            from .segment import segment_config

            cfg = segment_config(cfg, segment)
        for job, url, _ in iter_urls(cfg, segment):
            print(f"{job.label}\t{job.edition}\t{url}")
        return 0

    if args.command == "site":
        path = latest_report(args.input)
        if not path:
            log.error("沒有報告:請先執行 `python -m lead_radar run`")
            return 1
        print(write_site(json.loads(path.read_text(encoding="utf-8")), args.out, root=args.nav_root))
        return 0

    if args.command == "demo":
        import tempfile

        asof = date.fromisoformat(args.asof)
        with tempfile.TemporaryDirectory() as tmp:
            store = SignalStore(Path(tmp) / "demo.sqlite3")
            store.upsert(load_csv(args.csv, cfg), asof)
            store.set_meta("coverage_start", "2026-08-01")
            rep = build_report(store.all(), cfg, asof, coverage_start=date(2026, 8, 1), first_seen=store.first_seen(),
                               generated=datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc))
            store.close()
        out = Path(args.out)
        write_reports(rep, out)
        print(write_site(rep, out, nav=False))
        _summary(rep)
        return 0

    store = SignalStore(args.db)
    try:
        if args.command == "run":
            if segment:
                from .segment import segment_config

                cfg = segment_config(cfg, segment)
            if args.days:
                cfg.settings.days = args.days
            from .sources import collect

            asof = _today()
            signals = dedupe(collect(cfg, segment=segment))
            known = {s.id: s for s in store.all()}
            fresh = [s for s in signals if not (known.get(s.id) and known[s.id].enriched)]
            _enrich(fresh, cfg, not args.no_llm, args.model)
            new = store.upsert(signals, asof)
            store.mark_coverage(asof, cfg.settings.days, _coverage_key(segment))
            log.info("%d signals collected, %d new", len(signals), new)
            _analyse_and_write(store, cfg, asof, Path(args.out), args.site, segment)
            return 0

        if args.command == "import":
            total = 0
            for f in args.csv:
                rows = load_csv(f, cfg)
                _enrich([s for s in rows if not s.need], cfg, not args.no_llm, args.model)
                total += store.upsert(rows, _today())
                log.info("%s: %d rows", f, len(rows))
            print(f"匯入 {total} 則新訊號;執行 `python -m lead_radar analyze` 更新名單")
            return 0

        if args.command == "analyze":
            asof = date.fromisoformat(args.asof) if args.asof else _today()
            _analyse_and_write(store, cfg, asof, Path(args.out), args.site, segment)
            return 0
    finally:
        store.close()
    return 2


def _summary(rep: dict) -> None:
    """Short console table; also the GitHub Actions job summary when running there."""
    lab = rep["labels"]
    st = rep["stats"]
    lines = [
        f"訊號 {st['signals']} 則 · 熱點 {st['hot_cells']} · 溫點 {st['warm_cells']} · 潛在客戶 {st['leads']} 家"
        f"(A {st['grades']['A']} / B {st['grades']['B']})",
        "",
        "| 熱點 | 等級 | 趨勢 | z | 訊號 |",
        "|---|---|---|---:|---:|",
    ]
    for c in rep["hotspots"][:8]:
        lines.append(f"| {lab['products'][c['product']]} × {lab['industries'].get(c['industry'], c['industry'])} × {lab['regions'][c['region']]} "
                     f"| {lab['levels'][c['level']]} | {lab['trends'].get(c['trend'], '')} | {c['z']:+.2f} | {c['count']} |")
    lines += ["", "| 等級 | 公司 | 分數 | 產品線 | 主要訊號 |", "|---|---|---:|---|---|"]
    for l in rep["leads"][:10]:
        lines.append(f"| {l['grade']} | {l['org']} | {l['score']:.0f} | {lab['products'][l['product']]} | {lab['intents'].get(l['top_intent'], '—')} |")
    if rep.get("accounts"):
        lines += ["", f"**{rep['segment']['name']}:開發順序**", "", "| 公司 | 機會分數 | 分級 | 主推產品 |", "|---|---:|---|---|"]
        lines += [f"| {a['org']} | {a['opportunity']:.0f} | {a['tier']} | {lab['products'][a['best_product']]} |" for a in rep["accounts"][:10]]
    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## 商機雷達\n\n" + text + "\n")
