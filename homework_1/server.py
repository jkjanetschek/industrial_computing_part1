# ===========================================================================
# server.py  --  MCP Tool Server
# ===========================================================================

import asyncio
import os
from fastmcp import Client, FastMCP
from dotenv import load_dotenv

from audit import read_log, write_event
from database import fetch_product, init_db, fetch_products
from discount import apply_discount

load_dotenv()


server_mcp = FastMCP("task1_tools")
# idempotent initialization
init_db()


async def smoke_test() -> None:
    """Check that the MCP protocol exposes the expected interface."""
    async with Client(server_mcp) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        product = await client.call_tool(
            "lookup_product",
            {"sku": "CPU-1001"},
        )
        # call_tool() returns a CallToolResult. Its .data field contains the
        # dictionary returned by the MCP tool, while .content contains the
        # protocol text representation.
        # Example: product.data == {
        #     "found": True,
        #     "product": {"sku": "CPU-1001", "name": "AMD Ryzen 5 7600", ...},
        # }
        await client.call_tool(
            "log_event",
            {
                "actor": "server-mcp",
                "action": "smoke-test",
                "detail": "test",
            },
        )
        # read_resource() returns a list of resource-content objects, commonly
        # TextResourceContents(text="...").
        log_contents = await client.read_resource("audit://log")

    tool_names = {tool.name for tool in tools}
    resource_uris = {str(resource.uri) for resource in resources}
    expected_tools = {"lookup_product", "quote_discount", "log_event"}
    log_text = "\n".join(
        content.text for content in log_contents if hasattr(content, "text")
    )

    missing_tools = expected_tools - tool_names
    if missing_tools:
        raise RuntimeError(f"Missing MCP tools: {sorted(missing_tools)}")
    if "audit://log" not in resource_uris:
        raise RuntimeError("Missing MCP resource: audit://log")
    if product.data["product"]["sku"] != "CPU-1001":
        raise RuntimeError("Product lookup returned the wrong product")
    if "test" not in log_text:
        raise RuntimeError("Audit log does not contain the smoke-test event")

    print(f"MCP smoke test passed: {len(tools)} tools, {len(resources)} resources")



@server_mcp.tool
def lookup_product(sku: str) -> dict:
    """Look up one product in the inventory by its exact SKU (e.g. 'CPU-1001').

    Takes a single argument, `sku` (string). Returns
    {"found": True, "product": {...}} with the product's sku, name, category,
    stock_qty, unit_price, and reorder_level when the SKU exists, or
    {"found": False, "sku": ..., "message": ...} when it does not. There is no
    way to filter or search by name/category with this tool; use
    `lookup_products` to list everything.
    """
    product = fetch_product(sku)
    if product is None:
        return {"found": False, "sku": sku, "message": f"Product {sku!r} was not found"}
    return {"found": True, "product": product}


@server_mcp.tool
def lookup_products() -> list[dict]:
    """List every product currently in the inventory.

    Takes no arguments at all — do not pass filters, flags, or IDs of any
    kind. Returns a list of product records, each with sku, name, category,
    stock_qty, unit_price, and reorder_level. There is no separate "internal
    ID" field; `sku` is the only identifier a product has.
    """
    products = fetch_products()
    return products



@server_mcp.tool
def quote_discount(qty: int, unit_price: float) -> dict:
    """Calculate the tiered-discount price for an order.

    Takes `qty` (integer, number of units) and `unit_price` (float, price per
    unit before discount). Applies a flat discount rate based on qty (0% below
    10 units, 5% from 10-29, 12% at 30+) to the whole order and returns
    {"qty", "unit_price", "rate", "subtotal", "discount_amount", "total"}.
    """
    quote = apply_discount(qty, unit_price)
    return quote



@server_mcp.tool
def log_event(actor: str, action: str, detail: str = "") -> str:
    """Append one entry to the audit log.

    Takes `actor` (who/what triggered the event, e.g. "agent" or a user name),
    `action` (short label for what happened, e.g. "lookup_product"), and an
    optional `detail` string with extra context. Returns the literal string
    "logged" on success. This only writes to the log; use the `audit://log`
    resource to read it back.
    """

    write_event(actor, action, detail)
    return "logged"



@server_mcp.resource("audit://log")
def audit_log() -> str:
    return read_log()



if __name__ == "__main__":
    if os.getenv("MCP_SMOKE_TEST", "").lower() in {"1", "true", "yes"}:
        asyncio.run(smoke_test())
        raise SystemExit(0)

    transport = os.getenv("MCP_TRANSPORT", "http").lower()
    if transport == "stdio":
        server_mcp.run()
    elif transport == "http":
        server_mcp.run(transport="http", host="0.0.0.0", port=8000)
    else:
        raise ValueError("MCP_TRANSPORT must be 'stdio' or 'http'")


