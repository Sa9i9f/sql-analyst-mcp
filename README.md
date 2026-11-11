# sql-analyst-mcp

A small, read-only MCP server for querying a local CSV file with SQL.
`server.py` loads the local CSV into an in-memory SQLite table named `posts` and
handles the MCP tools. The dataset is not included in this repository; no
database file is created or modified.

## Setup

Requires Python 3.9 or newer. The server uses only the Python standard library.
Put data next to `server.py`, or set `SQL_ANALYST_CSV` to the path
of your CSV before starting the server.

```sh
python server.py
```

The server communicates over MCP stdio. For an MCP client that uses a
`mcpServers` JSON configuration, add:

```json
{
  "mcpServers": {
    "sql-analyst-mcp": {
      "command": "python3",
      "args": ["/absolute/path/to/sql_mcp/server.py"]
    }
  }
}
```

## Tools

- `list_tables` lists available tables.
- `describe_table(table_name)` returns column names, SQLite types, and nullability.
- `run_query(sql)` runs a single read-only `SELECT` query. Results are capped at
  500 rows; the response reports when it was truncated.

SQLite's authorizer rejects writes and other non-read database operations. The
connection is also placed in query-only mode. Numeric CSV columns are loaded as
SQLite integers or real numbers; other values, including timestamps and JSON
arrays, remain text.
