import os
import uuid
from datetime import datetime, timezone

from flask import Flask, request, jsonify
from azure.identity import DefaultAzureCredential
from azure.data.tables import TableServiceClient

app = Flask(__name__)

# Storage Account name, NOT its full URL
STORAGE_ACCOUNT_NAME = os.getenv(
    "STORAGE_ACCOUNT_NAME",
    "stcalcatb"
)

TABLE_NAME = "Calculations"

TABLE_ENDPOINT = (
    f"https://{STORAGE_ACCOUNT_NAME}.table.core.windows.net"
)

# Authenticate using the VM's Managed Identity
credential = DefaultAzureCredential(
    exclude_interactive_browser_credential=True
)

table_service = TableServiceClient(
    endpoint=TABLE_ENDPOINT,
    credential=credential
)

table_client = table_service.get_table_client(
    table_name=TABLE_NAME
)


@app.route("/api/calculate", methods=["POST"])
def calculate():

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "error": "Invalid JSON body"
        }), 400

    operation = data.get("operation")
    a = data.get("a")
    b = data.get("b")

    if operation not in ["add", "subtract", "multiply", "divide"]:
        return jsonify({
            "error": "Unsupported operation"
        }), 400

    if (
        isinstance(a, bool)
        or isinstance(b, bool)
        or not isinstance(a, (int, float))
        or not isinstance(b, (int, float))
    ):
        return jsonify({
            "error": "a and b must be numbers"
        }), 400

    if operation == "add":
        result = a + b

    elif operation == "subtract":
        result = a - b

    elif operation == "multiply":
        result = a * b

    elif operation == "divide":
        if b == 0:
            return jsonify({
                "error": "Division by zero"
            }), 400

        result = a / b

    row_key = str(uuid.uuid4())

    entity = {
        "PartitionKey": "calculations",
        "RowKey": row_key,
        "Operation": operation,
        "A": float(a),
        "B": float(b),
        "Result": float(result),
        "CreatedAt": datetime.now(timezone.utc).isoformat()
    }

    try:
        table_client.create_entity(entity=entity)

    except Exception:
        app.logger.exception("Failed to write to Table Storage")
        return jsonify({
            "error": "Storage operation failed"
        }), 500

    return jsonify({
        "operation": operation,
        "a": a,
        "b": b,
        "result": result,
        "rowKey": row_key,
        "status": "stored"
    }), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"}), 200


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
