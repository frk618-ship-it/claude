"""Public procurement notices as demand signals — the sources that name what is being bought.

* 政府電子採購網(government e-procurement): tenders and awards whose title names a material
  (「異構化工場 INCONEL 600 管配件」「70MeV 試運轉用金屬靶材」), searched through the open
  pcc-api.openfun.app mirror. One signal per case (unit + job number), from its latest notice.
* 公開資訊觀測站重大訊息(TWSE / TPEx open data): listed companies must announce acquiring
  machinery or plant works; the filing names the counterparty and the amount
  (「矽品取得營業用機器設備達十億元,交易相對人:弘塑科技」). The open data holds one day only,
  so the procurement workflow collects it every day.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from datetime import date, timedelta
from typing import Callable

from target_news_agent.sources import http_fetch

from .config import Config
from .signals import Signal, account_in_name, classify, signal_id

log = logging.getLogger(__name__)

Fetcher = Callable[[str], bytes]

PCC_SEARCH = "https://pcc-api.openfun.app/api/searchbytitle?query={q}&page={page}"
PCC_NOTICE = "https://web.pcc.gov.tw/prkms/tender/common/noticeDate/redirectPublic?ds={ds}&fn={fn}.xml"
MOPS_FEEDS = [
    ("上市", "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"),
    ("上櫃", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O"),
]
MOPS_LINK = "https://mops.twse.com.tw/mops/#/web/t05st01"

# notice type → intent; awards are contracts, everything else is still an open demand
_AWARD = ("決標",)
_SKIP_TYPES = ("更正", "定期彙送", "撤銷")
# a filing is about buying something when its subject says so
_FILING_SUBJECT = re.compile(r"取得.{0,20}(機器設備|設備|廠務|工程|廠房|原物料|材料)|採購|招標|發包")
_RELATED = re.compile(r"子公司|母公司|關係人|同一集團|聯屬")
_CO_SUFFIX = re.compile(r"(股份有限公司|有限公司|\(股\)公司|公司)$")


def _short(name: str) -> str:
    """「詠光生技有限公司 (YUON KUANG Biotechnology Co., Ltd.)」→「詠光生技」"""
    name = re.sub(r"\s*[(（][A-Za-z][^)）]*[)）]\s*$", "", (name or "").strip())
    return _CO_SUFFIX.sub("", name) or name


def _roc_date(s: str) -> str:
    """'1151105' (ROC year 115) → '2026-11-05'."""
    s = re.sub(r"\D", "", s or "")
    if len(s) < 7:
        return ""
    try:
        return date(int(s[:-4]) + 1911, int(s[-4:-2]), int(s[-2:])).isoformat()
    except ValueError:
        return ""


def _ymd(n) -> str:
    s = str(n or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else ""


# ── government e-procurement ─────────────────────────────────────────────

def tender_signal(rec: dict, config: Config) -> Signal | None:
    """One pcc record → a signal, or None when it names no product line we sell."""
    brief = rec.get("brief") or {}
    kind, title = brief.get("type", ""), brief.get("title", "")
    if not title or any(t in kind for t in _SKIP_TYPES):
        return None
    unit = _short(rec.get("unit_name") or "")
    names = (brief.get("companies") or {}).get("name_key") or {}
    winners: list[str] = []
    for n, keys in names.items():
        if any("得標廠商" in k for k in keys):
            acc = account_in_name(_short(n), config.accounts)
            w = acc.name if acc else _short(n)
            if w not in winners:
                winners.append(w)
    bidders = [_short(n) for n in names if _short(n) not in winners]
    award = any(t in kind for t in _AWARD) and "無法決標" not in kind
    head = f"{unit} {kind}:{title}"
    parts = []
    if winners:
        parts.append("得標廠商:" + "、".join(winners[:3]))
    elif bidders:
        parts.append("投標廠商:" + "、".join(bidders[:3]))
    if rec.get("budget_amount"):
        parts.append(f"預算金額:{rec['budget_amount']}")
    snippet = ";".join(parts)
    sig = classify(config, title=head, snippet=snippet, link=PCC_NOTICE.format(ds=rec.get("date"), fn=rec.get("filename", "")),
                   source="政府電子採購網", published=_ymd(rec.get("date")), origin="tender")
    if sig is None:
        return None
    intent = "contract" if award else "tender"
    sig.intents = [intent] + [i for i in sig.intents if i != intent]
    # the buyer (a listed company when the unit belongs to one, e.g. 台灣中油…煉製事業部 → 中油) and the winners;
    # no name guessing from the title (「大林廠烷化工場」 is a plant, not a company)
    acc = account_in_name(unit, config.accounts)
    buyer = acc.name if acc else unit
    sig.orgs = [buyer] + [a for a in sig.accounts if a != buyer] + [w for w in winners if w != buyer]
    sig.region = "TW" if "TW" in config.regions else sig.region
    sig.relevance = 1.0
    sig.noise = False
    sig.id = "t-" + signal_id(f"{rec.get('unit_id')}|{rec.get('job_number')}")
    return sig


def fetch_tenders(config: Config, days: int, fetch: Fetcher = http_fetch, *, asof: date | None = None,
                  sleep: Callable[[float], None] = time.sleep) -> list[Signal]:
    """Search tender titles for each material term; keep the latest notice of each case in the window."""
    since = ((asof or date.today()) - timedelta(days=days)).isoformat()
    latest: dict[str, tuple[str, Signal]] = {}
    for term in config.settings.procurement.get("pcc_queries", []):
        try:
            data = json.loads(fetch(PCC_SEARCH.format(q=urllib.parse.quote(str(term)), page=1)))
        except Exception as exc:  # one failed search must not stop the run
            log.warning("procurement search %s failed: %s", term, exc)
            continue
        for rec in data.get("records") or []:
            day = _ymd(rec.get("date"))
            if not day or day < since:
                continue
            sig = tender_signal(rec, config)
            if sig and (sig.id not in latest or day > latest[sig.id][0]):
                latest[sig.id] = (day, sig)
        sleep(config.settings.request_delay)
    out = [s for _, s in latest.values()]
    log.info("government tenders: %d cases in the last %d days", len(out), days)
    return out


# ── listed companies' material announcements ─────────────────────────────

def _field(text: str, label: str) -> str:
    """The value after「<label>…:」, on the same line or the next one (filings use both layouts)."""
    m = re.search(rf"(?:{label})[^:：\n]*[:：]\s*([^\n]+)", text or "")
    return m.group(1).strip().rstrip(",，;；。 ") if m else ""


def filing_signal(row: dict, market: str, config: Config) -> Signal | None:
    """One 重大訊息 row → a signal when it announces buying equipment / works from an outside party."""
    subject = re.sub(r"\s+", "", row.get("主旨 ") or row.get("主旨") or "")
    if not _FILING_SUBJECT.search(subject) or "處分" in subject[:6]:
        return None
    body = row.get("說明") or ""
    party = _field(body, "交易相對人|契約相對人")
    m = re.search(r"^\s*與公司之?關係\s*[:：]\s*([^\n]*)", body, re.M)
    relation = m.group(1) if m else ""
    if _RELATED.search(relation) or re.search(r"關係人交易\s*[:：]\s*是", body) or re.search(r"向(本公司|母公司|子公司|關係人)", subject):
        return None                           # moving assets inside a group is not a purchase
    company = _short(row.get("公司名稱") or row.get("CompanyName") or "")
    code = row.get("公司代號") or row.get("SecuritiesCompanyCode") or ""
    what = _field(body, "標的物之名稱及性質|契約種類")
    m = re.search(r"總金額\s*[:：]\s*([^\n；;，]+)", body)
    amount = m.group(1).strip() if m else ""
    party_s = _short(party)
    subject = re.sub(r"^(本公司)?(代)?(子|孫)?公司", "", subject).replace("公告", "", 1)   # 「代子公司矽品…公告取得…」→「矽品…取得…」
    head = f"{company}({code}):{subject}"
    if party_s and party_s not in head:
        head += f",交易相對人:{party_s}"
    snippet = ";".join(x for x in [f"公開資訊觀測站{market}重大訊息", f"標的:{what}" if what else "", f"總金額:{amount}" if amount else ""] if x)
    published = _roc_date(row.get("發言日期") or row.get("出表日期") or "")
    sig = classify(config, title=head, snippet=snippet, link=MOPS_LINK, source=f"公開資訊觀測站({market})",
                   published=published, origin="filing")
    if sig is None:
        return None
    sig.intents = ["contract"] + [i for i in sig.intents if i != "contract"]
    # the buyer and the counterparty, as listed companies where they are; no name guessing
    names = []
    for n in [company, party_s]:
        acc = account_in_name(n, config.accounts) if n else None
        n = acc.name if acc else n
        if n and n not in names:
            names.append(n)
    sig.orgs = names + [a for a in sig.accounts if a not in names]
    sig.relevance = 1.0
    sig.noise = False
    sig.id = "f-" + signal_id(f"{code}|{published}|{subject}|{party_s}")
    return sig


def fetch_filings(config: Config, fetch: Fetcher = http_fetch) -> list[Signal]:
    out: list[Signal] = []
    for market, url in MOPS_FEEDS:
        rows = None
        for attempt in range(3):              # the day's file is large; connections sometimes drop
            try:
                rows = json.loads(fetch(url))
                break
            except Exception as exc:
                log.warning("filings %s attempt %d failed: %s", market, attempt + 1, exc)
                time.sleep(2 * (attempt + 1))
        if rows is None:
            continue
        out += [s for s in (filing_signal(r, market, config) for r in rows) if s]
    log.info("company filings: %d purchase announcements", len(out))
    return out


def collect_procurement(config: Config, days: int, fetch: Fetcher = http_fetch, **kw) -> list[Signal]:
    p = config.settings.procurement
    sigs: list[Signal] = []
    if p.get("pcc", True):
        sigs += fetch_tenders(config, days, fetch, **kw)
    if p.get("mops", True):
        sigs += fetch_filings(config, fetch)
    return sigs
