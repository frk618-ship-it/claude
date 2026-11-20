"""Full Taiwan balance sheets from MOPS (公開資訊觀測站), for line items FinMind's condensed sheet leaves out.

FinMind republishes a condensed balance sheet: it carries 應收帳款淨額 and 應收票據淨額 but no 應付票據 or
related-party trade lines, so receivables / payables days miss notes and intra-group balances. MOPS's
balance-sheet query (t164sb03) returns every line of the consolidated statement as an HTML table; this
module fetches it and sums the trade lines:

    receivables = 應收票據淨額 + 應收帳款淨額 + 應收票據－關係人淨額 + 應收帳款－關係人淨額
    payables    = 應付票據 + 應付帳款 + 應付票據－關係人 + 應付帳款－關係人

Amounts on MOPS are in NT$ thousands; they are returned in NT$ like the rest of the data.
"""

from __future__ import annotations

import html
import logging
import re
import time
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

# (url, form fields, body kind); tried in order until one returns a statement table
ENDPOINTS = [
    ("https://mopsov.twse.com.tw/mops/web/ajax_t164sb03",
     {"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1", "queryName": "co_id", "inpuType": "co_id",
      "TYPEK": "all", "isnew": "false"}, "form"),
    ("https://mops.twse.com.tw/mops/web/ajax_t164sb03",
     {"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1", "queryName": "co_id", "inpuType": "co_id",
      "TYPEK": "all", "isnew": "false"}, "form"),
]
UA = "Mozilla/5.0 (X11; Linux x86_64) fin-analyzer/0.1"
LABEL_SPACE = re.compile(r"[\s　\xa0]+")

RECEIVABLE_LINES = ("應收票據淨額", "應收帳款淨額", "應收票據－關係人淨額", "應收帳款－關係人淨額")
PAYABLE_LINES = ("應付票據", "應付帳款", "應付票據－關係人", "應付帳款－關係人")


def _post(url: str, fields: dict, timeout: int = 30) -> str:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _number(text: str) -> float | None:
    t = text.replace(",", "").replace("\xa0", "").strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def parse_statement(page: str) -> dict[str, float]:
    """{line label: first amount column} from a MOPS statement table (labels normalised, amounts as shown)."""
    out: dict[str, float] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S | re.I):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S | re.I)]
        if len(cells) < 2:
            continue
        label = LABEL_SPACE.sub("", cells[0])
        value = _number(cells[1])
        if label and value is not None and label not in out:
            out[label] = value
    return out


def fetch_balance(co_id: str, year: int, season: int, post=_post, pause: float = 3.0) -> tuple[dict[str, float], str]:
    """(lines in NT$, endpoint used) for a calendar year and quarter; ({}, reason) when every endpoint fails."""
    roc = str(year - 1911)
    reasons = []
    for url, base, _kind in ENDPOINTS:
        try:
            page = post(url, {**base, "co_id": co_id, "year": roc, "season": f"{season:02d}"})
        except Exception as exc:  # network refusal, 4xx/5xx
            reasons.append(f"{url}: {exc}")
            continue
        lines = parse_statement(page)
        if any(k in lines for k in ("資產總計", "資產總額", "負債總計", "負債總額")):
            time.sleep(pause)  # MOPS throttles bursts ("查詢過於頻繁")
            return {k: v * 1000 for k, v in lines.items()}, url
        reasons.append(f"{url}: no statement table ({LABEL_SPACE.sub(' ', re.sub(r'<[^>]+>', ' ', page))[:160]!r})")
        time.sleep(pause)
    return {}, " | ".join(reasons)


def trade_totals(lines: dict[str, float]) -> dict[str, float | None]:
    """receivables / payables including notes and related parties; None when no such line is present."""
    def total(names):
        vals = [lines[n] for n in names if n in lines]
        return sum(vals) if vals else None
    return {"receivables": total(RECEIVABLE_LINES), "payables": total(PAYABLE_LINES)}
