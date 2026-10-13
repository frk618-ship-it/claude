"""One comparison run: parse spec -> gather quotes from enabled sources -> analyse."""

from __future__ import annotations

import logging
from datetime import date

from .compare import Comparison, analyse
from .config import Settings
from .fx import FxConverter
from .models import Quote
from .sources import ClaudeMarketSearch, llm_available, search_pchome, search_quote_book
from .spec import parse_spec

log = logging.getLogger(__name__)


def run_search(
    query: str,
    settings: Settings,
    *,
    quantity: str = "",
    use_web: bool = True,
    use_book: bool = True,
    use_pchome: bool | None = None,
    model: str | None = None,
    client=None,
    fx: FxConverter | None = None,
    pchome_fetch=None,
    today: date | None = None,
) -> Comparison:
    spec = parse_spec(query)
    quotes: list[Quote] = []
    market_summary = ""

    if use_book:
        found = search_quote_book(spec, settings.quote_book)
        log.info("quote book: %d rows", len(found))
        quotes += found

    if use_pchome if use_pchome is not None else settings.pchome:
        found = search_pchome(query, pchome_fetch) if pchome_fetch else search_pchome(query)
        log.info("PChome: %d items", len(found))
        quotes += found

    if use_web and settings.web_search:
        if client is not None or llm_available():
            searcher = ClaudeMarketSearch(
                model=model, client=client, max_searches=settings.max_searches,
                user_location=settings.user_location, blocked_domains=settings.blocked_domains,
            )
            found = searcher.search(spec, quantity, settings.search_hints)
            log.info("Claude web search: %d quotes", len(found))
            quotes += found
            market_summary = searcher.market_summary
        else:
            log.warning("ANTHROPIC_API_KEY not set; skipping Claude web search (only the quote book is used)")

    if fx is None:
        fx = FxConverter(settings.base_currency, settings.fx_rates)
        if settings.live_fx:
            fx.refresh()
    return analyse(spec, quotes, settings, fx, today=today, market_summary=market_summary)
