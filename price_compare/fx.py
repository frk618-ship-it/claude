"""Currency conversion: Bank of Taiwan daily rates when reachable, configured rates otherwise."""

from __future__ import annotations

import csv
import io
import logging
import urllib.request

log = logging.getLogger(__name__)

BOT_CSV_URL = "https://rate.bot.com.tw/xrt/flcsv/0/day"
ALIASES = {"NTD": "TWD", "NT$": "TWD", "NT": "TWD", "RMB": "CNY", "US$": "USD", "$": "USD", "¥": "JPY", "€": "EUR", "￥": "CNY"}


def normalise_currency(code: str) -> str:
    c = (code or "").strip().upper()
    return ALIASES.get(c, c)


def parse_bot_csv(text: str) -> dict[str, float]:
    """Mid spot rate per currency from the Bank of Taiwan CSV (TWD per 1 unit).

    Each row: 幣別, 本行買入, 現金, 即期, 遠期…, 本行賣出, 現金, 即期, 遠期…
    """
    rates: dict[str, float] = {}
    for row in csv.reader(io.StringIO(text.lstrip("﻿"))):
        if len(row) < 4 or not row[0].strip().isalpha() or len(row[0].strip()) != 3:
            continue
        try:
            sell_at = row.index("本行賣出")
            buy, sell = float(row[3]), float(row[sell_at + 2])
        except (ValueError, IndexError):
            continue
        if buy > 0 and sell > 0:
            rates[row[0].strip().upper()] = round((buy + sell) / 2, 4)
    return rates


class FxConverter:
    def __init__(self, base: str = "TWD", rates: dict[str, float] | None = None):
        self.base = normalise_currency(base)
        self.rates = {normalise_currency(k): v for k, v in (rates or {}).items()}
        self.rates[self.base] = 1.0
        self.live = False

    def refresh(self, fetch=None) -> bool:
        """Overlay Bank of Taiwan rates (only meaningful when base is TWD)."""
        if self.base != "TWD":
            return False
        try:
            if fetch is None:
                req = urllib.request.Request(BOT_CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = resp.read()
            else:
                data = fetch(BOT_CSV_URL)
            live = parse_bot_csv(data.decode("utf-8-sig") if isinstance(data, bytes) else data)
        except Exception as exc:  # offline / blocked: keep configured rates
            log.warning("Bank of Taiwan FX fetch failed, using configured rates: %s", exc)
            return False
        if live:
            self.rates.update(live)
            self.live = True
        return bool(live)

    def convert(self, amount: float, currency: str) -> float | None:
        rate = self.rates.get(normalise_currency(currency))
        return None if rate is None else amount * rate
