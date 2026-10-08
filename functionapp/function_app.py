"""Azure Functions Python v2: calculation API backed by Azure Table Storage."""
import json
import logging
import os
import uuid
from datetime import datetime, timezone

import azure.functions as func
from azure.core.exceptions import AzureError
from azure.data.tables import TableClient
from azure.identity import DefaultAzureCredential

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


@app.route(route="calculate", methods=["POST"])
def calculate(req: func.HttpRequest) -> func.HttpResponse:
    try:
        payload = req.get_json()
        if not isinstance(payload, dict):
            raise ValueError("JSON object required")
        a, b = payload["a"], payload["b"]
        operation = payload.get("operation", "add")
        if type(a) not in (int, float) or type(b) not in (int, float):
            raise ValueError("a and b must be numeric")
        if operation not in {"add", "subtract", "multiply", "divide"}:
            raise ValueError("operation must be add, subtract, multiply or divide")
        if operation == "divide" and b == 0:
            raise ValueError("division by zero")
        result = {"add": lambda: a + b, "subtract": lambda: a - b,
                  "multiply": lambda: a * b, "divide": lambda: a / b}[operation]()
    except (ValueError, KeyError, TypeError) as exc:
        return func.HttpResponse(json.dumps({"error": str(exc)}), status_code=400,
                                 mimetype="application/json")

    account = os.getenv("DATA_STORAGE_ACCOUNT")
    if not account:
        return func.HttpResponse('{"error":"DATA_STORAGE_ACCOUNT setting missing"}',
                                 status_code=500, mimetype="application/json")
    entity = {
        "PartitionKey": "calculations", "RowKey": str(uuid.uuid4()),
        "Operation": operation, "A": float(a), "B": float(b),
        "Result": float(result), "CreatedAt": datetime.now(timezone.utc).isoformat()
    }
    try:
        with TableClient(
            endpoint=f"https://{account}.table.core.windows.net",
            table_name="Calculations",
            credential=DefaultAzureCredential(exclude_interactive_browser_credential=True),
        ) as table:
            table.create_entity(entity)
    except AzureError:
        logging.exception("Table Storage write failed")
        return func.HttpResponse('{"error":"Table Storage write failed"}',
                                 status_code=502, mimetype="application/json")
    return func.HttpResponse(json.dumps({
        "operation": operation, "a": a, "b": b, "result": result,
        "rowKey": entity["RowKey"], "status": "stored"
    }), status_code=200, mimetype="application/json")
