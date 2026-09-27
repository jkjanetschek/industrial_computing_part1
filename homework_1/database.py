# ===========================================================================
# database.py  --  sqlite3 helper for Tool 1 (SQL database lookup)
# ===========================================================================
# stdlib-only (sqlite3). Key classes: Connection, Cursor, Row.

import os
import sqlite3
from pathlib import Path



BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("INVENTORY_DB", "inventory.db"))
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH
SCHEMA_PATH = BASE_DIR / "schema.sql"


def get_connection() -> sqlite3.Connection:
    """Open a configured SQLite connection for one database operation."""
    # Create the parent directory when using a custom path such as /data/inventory.db.
    # parents=True: also creates missing parent folders.
    # exist_ok=True: does not raise an error if the folder already exists
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    # Return rows that support both column-name access and conversion to dict.
    # By default, SQLite returns query results as tuples:
    # qlite3.Row = SQLite returns a row that supports column names: row["sku"]; It can also be converted to a dictionary
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the inventory schema and seed data if they do not exist."""
    # The context manager commits successful changes and closes the connection.
    with get_connection() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def fetch_product(sku: str) -> dict | None:
    """Return one product by SKU, or None when the SKU is not in the database."""
    with get_connection() as conn:
        # Bind the SKU as a SQL parameter instead of interpolating it into the query.
        row = conn.execute(
            "SELECT sku, name, category, stock_qty, unit_price, reorder_level "
            "FROM products WHERE sku = ?",
            (sku,),
        ).fetchone()

    # Convert the SQLite Row into a plain dictionary for the MCP response.
    return dict(row) if row is not None else None


def fetch_products() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM products").fetchall()
        return [dict(row) for row in rows]
