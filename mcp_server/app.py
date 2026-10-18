"""MCP HTTP server: read-only tools over the menu-intelligence domain.

Uses the official ``mcp`` package's ``MCPServer`` (formerly FastMCP) with
streamable-HTTP transport. Auth is a separate ASGI bearer middleware so ``/health``
stays open while every other path requires ``MCP_AUTH_TOKEN``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_server import tools
from mcp_server.auth import BearerAuthMiddleware, load_auth_token
from mcp_server.serialize import DEFAULT_LIMIT, HARD_MAX
from src.enrichment.schema import GroupingDimension
from src.synthetic import MARKET_LABEL

logger = logging.getLogger(__name__)

SERVER_INSTRUCTIONS = (
    "Read-only Wagamama menu pricing intelligence. "
    f"{tools.SYNTHETIC_HISTORY_CAVEAT} {tools.SNAPSHOT_CAVEAT} "
    "Call list_companies for valid company names; never invent chains. "
    f"Grouping dimensions: {[d.value for d in GroupingDimension]}. "
    f"Default market benchmark label: {MARKET_LABEL!r}."
)


def build_mcp() -> MCPServer:
    """Register tools and the glossary resource on a fresh MCPServer."""
    mcp = MCPServer(
        name="menu-intelligence",
        instructions=SERVER_INSTRUCTIONS,
    )

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @mcp.resource(
        "menu://glossary",
        name="glossary",
        description=(
            "Short definitions: buckets, Market whole, synthetic history vs "
            "scraped snapshots."
        ),
        mime_type="text/markdown",
    )
    def menu_glossary() -> str:
        return tools.glossary()

    @mcp.tool(
        description=(
            "List the own brand and competitor companies present in price history. "
            f"{tools.SYNTHETIC_HISTORY_CAVEAT}"
        )
    )
    def list_companies() -> dict[str, Any]:
        return tools.list_companies()

    @mcp.tool(
        description=(
            "Latest price-history date plus scrape status for registered sites "
            f"(for 'as of' answers). {tools.SYNTHETIC_HISTORY_CAVEAT}"
        )
    )
    def data_freshness() -> dict[str, Any]:
        return tools.data_freshness()

    @mcp.tool(
        description=(
            "Board-level KPIs (CAGR, total change, fastest bucket, latest averages). "
            "Optional source filters to one company; omit for the whole set. "
            f"{tools.SYNTHETIC_HISTORY_CAVEAT}"
        )
    )
    def price_kpis(source: str | None = None) -> dict[str, Any]:
        return tools.price_kpis(source)

    @mcp.tool(
        description=(
            "Average price and cumulative % change by company and grouping "
            f"dimension ({[d.value for d in GroupingDimension]}). "
            f"Rows capped (default {DEFAULT_LIMIT}, max {HARD_MAX}). "
            f"{tools.SYNTHETIC_HISTORY_CAVEAT}"
        )
    )
    def price_trends(
        source: str,
        dimension: str = GroupingDimension.BUCKET.value,
        include_total: bool = True,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        return tools.price_trends(
            source,
            dimension,
            include_total=include_total,
            limit=limit,
        )

    @mcp.tool(
        description=(
            "Latest Wagamama prices vs a market benchmark by dimension. "
            f"Default market is {MARKET_LABEL!r}; pass a saved custom market name "
            "when mappings DB is present. "
            f"{tools.SYNTHETIC_HISTORY_CAVEAT}"
        )
    )
    def compare_to_market(
        dimension: str = GroupingDimension.BUCKET.value,
        market_name: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        return tools.compare_to_market(
            dimension,
            market_name,
            limit=limit,
        )

    @mcp.tool(
        description=(
            "Restaurant-website vs Deliveroo markup. Omit company for per-company "
            "summary; pass company for bucket drilldown; pass company+bucket for "
            f"items. Rows capped (default {DEFAULT_LIMIT}, max {HARD_MAX}). "
            f"{tools.SNAPSHOT_CAVEAT}"
        )
    )
    def delivery_markup(
        company: str | None = None,
        bucket: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        return tools.delivery_markup(company, bucket, limit=limit)

    @mcp.tool(
        description=(
            "High / low / average menu prices from restaurant snapshots. Omit "
            "company for per-company summary; pass company for bucket drilldown; "
            "pass company+bucket for items. "
            f"Rows capped (default {DEFAULT_LIMIT}, max {HARD_MAX}). "
            f"{tools.SNAPSHOT_CAVEAT}"
        )
    )
    def price_range(
        company: str | None = None,
        bucket: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        return tools.price_range(company, bucket, limit=limit)

    @mcp.tool(
        description=(
            "Case-insensitive dish name search on the latest quarter of price "
            f"history. Rows capped (default {DEFAULT_LIMIT}, max {HARD_MAX}). "
            f"{tools.SYNTHETIC_HISTORY_CAVEAT}"
        )
    )
    def find_dishes(
        query: str,
        source: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        return tools.find_dishes(query, source, limit=limit)

    return mcp


def create_app(*, token: str | None = None, host: str | None = None):
    """Build the streamable-HTTP Starlette app wrapped in bearer auth.

    ``host`` is the address the process binds to. It defaults to ``MCP_HOST``
    (``127.0.0.1`` when unset). The MCP SDK turns on DNS-rebinding protection
    only for loopback addresses, and that check allows ``Host: localhost`` /
    ``127.0.0.1`` only. A public deploy must set ``MCP_HOST=0.0.0.0`` or every
    client request is rejected with ``Invalid Host header``.
    """
    if host is None:
        host = os.environ.get("MCP_HOST", "127.0.0.1")
    mcp = build_mcp()
    inner = mcp.streamable_http_app(host=host)
    return BearerAuthMiddleware(inner, token=token)


def listen_port() -> int:
    """Return the TCP port to bind.

    ``MCP_PORT`` is an optional local override and must be digits, for example
    ``8000``. Render sets its own variable, ``PORT``, to a number such as
    ``10000``. A non-numeric ``MCP_PORT`` is skipped so that value is used.
    """
    mcp_port = os.environ.get("MCP_PORT", "").strip()
    if mcp_port:
        if mcp_port.isdigit():
            return int(mcp_port)
        logger.warning(
            "MCP_PORT=%r is not an integer, so it is ignored. "
            "Delete it on Render; the platform's PORT value is used instead.",
            mcp_port,
        )
    platform_port = os.environ.get("PORT", "").strip()
    if platform_port.isdigit():
        return int(platform_port)
    return 8000


def main() -> None:
    """CLI entrypoint: ``python -m mcp_server``."""
    import uvicorn

    token = load_auth_token()
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = listen_port()
    app = create_app(token=token, host=host)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
