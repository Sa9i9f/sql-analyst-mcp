import csv
import json
import os
import re
import sqlite3
import sys
from typing import Any, Dict, List

TABLE_NAME = "posts"
MAX_RESULT_ROWS = 500
PROTOCOL_VERSION = "2024-11-05"
DEFAULT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sharqiyatv.csv")


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def infer_column_type(values: List[str]) -> str:
    values = [value for value in values if value]
    if not values:
        return "TEXT"
    if all(re.fullmatch(r"[+-]?\d+", value) for value in values):
        return "INTEGER"
    try:
        for value in values:
            float(value)
        return "REAL"
    except ValueError:
        return "TEXT"


def authorize_read_only(action: int, arg1: Any, arg2: Any, database: Any, trigger: Any) -> int:
    if action == sqlite3.SQLITE_PRAGMA:
        return (
            sqlite3.SQLITE_OK
            if (arg1 or "").lower() == "table_info" and arg2 is None
            else sqlite3.SQLITE_DENY
        )

    allowed_actions = {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        getattr(sqlite3, "SQLITE_RECURSIVE", -1),
    }
    if action not in allowed_actions:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg1 or arg2 or "").lower() == "load_extension":
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def load_csv_database(path: str = None) -> sqlite3.Connection:
    path = path or os.environ.get("SQL_ANALYST_CSV", DEFAULT_CSV)
    if not os.path.isfile(path):
        raise FileNotFoundError(
            "CSV file not found. Place sharqiyatv.csv next to server.py or set SQL_ANALYST_CSV."
        )

    with open(path, "r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        columns = reader.fieldnames
        rows = list(reader)
    if not columns:
        raise ValueError("CSV file has no header row")

    types = {
        column: infer_column_type([row.get(column, "") or "" for row in rows])
        for column in columns
    }
    definitions = ", ".join(
        quote_identifier(column) + " " + types[column] for column in columns
    )
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE " + quote_identifier(TABLE_NAME) + " (" + definitions + ")"
    )

    placeholders = ", ".join("?" for _ in columns)
    insert = "INSERT INTO " + quote_identifier(TABLE_NAME) + " VALUES (" + placeholders + ")"
    records = []
    for row in rows:
        record = []
        for column in columns:
            value = row.get(column, "") or None
            if value is not None and types[column] == "INTEGER":
                value = int(value)
            elif value is not None and types[column] == "REAL":
                value = float(value)
            record.append(value)
        records.append(tuple(record))

    connection.executemany(insert, records)
    connection.commit()
    connection.execute("PRAGMA query_only = ON")
    connection.set_authorizer(authorize_read_only)
    return connection

try:
    db = load_csv_database()
except Exception as error:
    print("Unable to load CSV: " + str(error), file=sys.stderr)
    raise


TOOLS = [
    {
        "name": "list_tables",
        "description": "List the tables available in the CSV-backed SQLite database.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "describe_table",
        "description": "Describe a table's columns and SQLite types.",
        "inputSchema": {
            "type": "object",
            "properties": {"table_name": {"type": "string"}},
            "required": ["table_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_query",
        "description": "Run one read-only SELECT query; returns at most 500 rows.",
        "inputSchema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
            "additionalProperties": False,
        },
    },
]


def list_tables() -> Dict[str, Any]:
    rows = db.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return {"tables": [{"table_name": row[0]} for row in rows]}


def describe_table(table_name: str) -> Dict[str, Any]:
    table = db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    if table is None or table_name.startswith("sqlite_"):
        raise ValueError("Unknown table: " + table_name)

    quoted = quote_identifier(table_name)
    rows = db.execute("PRAGMA table_info(" + quoted + ")").fetchall()
    return {
        "table_name": table_name,
        "columns": [
            {
                "name": row[1],
                "type": row[2],
                "nullable": not bool(row[3]),
                "primary_key": bool(row[5]),
            }
            for row in rows
        ],
    }


def run_query(sql: str) -> Dict[str, Any]:
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("Provide a SELECT query")
    query = sql.lstrip().upper()
    if not (query.startswith("SELECT") or query.startswith("WITH")):
        raise ValueError("Only SELECT queries are allowed")

    cursor = db.execute(sql)
    rows = cursor.fetchmany(MAX_RESULT_ROWS + 1)
    truncated = len(rows) > MAX_RESULT_ROWS
    rows = rows[:MAX_RESULT_ROWS]
    return {
        "columns": [column[0] for column in (cursor.description or [])],
        "rows": [dict(row) for row in rows],
        "row_count": len(rows),
        "truncated": truncated,
        "max_rows": MAX_RESULT_ROWS,
    }


def call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if name == "list_tables":
        return list_tables()
    if name == "describe_table":
        return describe_table(arguments["table_name"])
    if name == "run_query":
        return run_query(arguments["sql"])
    raise ValueError("Unknown tool: " + str(name))


def handle_request(request: Dict[str, Any]) -> Dict[str, Any]:
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}

    if method == "initialize":
        result = {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "sql-analyst-mcp", "version": "1.0.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        try:
            value = call_tool(params.get("name", ""), params.get("arguments") or {})
            content = json.dumps(value, ensure_ascii=False)
            result = {"content": [{"type": "text", "text": content}], "isError": False}
        except Exception as error:
            result = {"content": [{"type": "text", "text": str(error)}], "isError": True}
    elif method and method.startswith("notifications/"):
        return {}
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Method not found"},
        }

    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve_stdio() -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            response = handle_request(request)
            if "id" in request:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except Exception as error:
            print("Invalid MCP message: " + str(error), file=sys.stderr)


if __name__ == "__main__":
    serve_stdio()
