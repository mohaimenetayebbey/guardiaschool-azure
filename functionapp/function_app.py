
import os
import uuid
import time
import logging
from datetime import datetime, timezone

from flask import Flask, request, jsonify, g
from azure.identity import ManagedIdentityCredential
from azure.data.tables import TableServiceClient
from azure.core.exceptions import AzureError

# ============================================================
# 1. LOGGING CONFIGURATION
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True
)

logger = logging.getLogger("calculator-api")

# Optional: set LOG_LEVEL=DEBUG for additional application logs
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logger.setLevel(getattr(logging, log_level, logging.INFO))

# Avoid excessively verbose SDK HTTP logs that may expose headers
logging.getLogger("azure").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# ============================================================
# 2. FLASK CONFIGURATION
# ============================================================

app = Flask(__name__)

STORAGE_ACCOUNT_NAME = os.getenv(
    "STORAGE_ACCOUNT_NAME",
    "stcalcatb"
)

TABLE_NAME = os.getenv(
    "TABLE_NAME",
    "Calculations"
)

TABLE_ENDPOINT = (
    f"https://{STORAGE_ACCOUNT_NAME}.table.core.windows.net"
)

logger.info("=" * 65)
logger.info("Starting Azure Calculator API")
logger.info("Storage Account : %s", STORAGE_ACCOUNT_NAME)
logger.info("Table Name      : %s", TABLE_NAME)
logger.info("Table Endpoint  : %s", TABLE_ENDPOINT)
logger.info("Authentication  : System-assigned Managed Identity")
logger.info("Log Level       : %s", log_level)
logger.info("=" * 65)

# ============================================================
# 3. MANAGED IDENTITY AND TABLE STORAGE CLIENT
# ============================================================

logger.info("[INIT] Creating ManagedIdentityCredential")

credential = ManagedIdentityCredential()

logger.info("[INIT] ManagedIdentityCredential created successfully")
logger.info("[INIT] Token acquisition will occur on first Azure request")

logger.info("[INIT] Creating Azure Table Storage client")

table_service = TableServiceClient(
    endpoint=TABLE_ENDPOINT,
    credential=credential
)

table_client = table_service.get_table_client(
    table_name=TABLE_NAME
)

logger.info("[INIT] Table Storage client initialized")
logger.info("[INIT] Note: connectivity and RBAC are not validated yet")

# ============================================================
# 4. HTTP REQUEST LOGGING
# ============================================================

@app.before_request
def before_request():
    g.start_time = time.perf_counter()
    g.request_id = str(uuid.uuid4())[:8]

    logger.info("-" * 65)
    logger.info("[%s] Incoming HTTP request", g.request_id)
    logger.info("[%s] Method      : %s", g.request_id, request.method)
    logger.info("[%s] Path        : %s", g.request_id, request.path)
    logger.info("[%s] Client IP   : %s", g.request_id, request.remote_addr)
    logger.info("[%s] Content Type: %s", g.request_id, request.content_type)


@app.after_request
def after_request(response):
    elapsed_ms = (
        time.perf_counter() - g.start_time
    ) * 1000

    logger.info(
        "[%s] HTTP response: %s | Duration: %.2f ms",
        g.request_id,
        response.status_code,
        elapsed_ms
    )

    response.headers["X-Request-ID"] = g.request_id

    logger.info("-" * 65)

    return response

# ============================================================
# 5. HEALTH CHECK
# ============================================================

@app.route("/health", methods=["GET"])
def health():
    logger.info("[%s] Health check requested", g.request_id)

    return jsonify({
        "status": "healthy",
        "service": "azure-calculator-api",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }), 200

# ============================================================
# 6. CALCULATION API
# ============================================================

@app.route("/api/calculate", methods=["POST"])
def calculate():

    request_id = g.request_id

    logger.info("[%s] STEP 1 - Parsing JSON request", request_id)

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        logger.warning("[%s] Invalid JSON request body", request_id)
        return jsonify({"error": "Invalid JSON body"}), 400

    operation = data.get("operation")
    a = data.get("a")
    b = data.get("b")

    logger.info(
        "[%s] Request parameters: operation=%s, a=%s, b=%s",
        request_id, operation, a, b
    )

    logger.info("[%s] STEP 2 - Validating parameters", request_id)

    if operation not in ["add", "subtract", "multiply", "divide"]:
        logger.warning(
            "[%s] Unsupported operation: %s",
            request_id, operation
        )
        return jsonify({"error": "Unsupported operation"}), 400

    if (
        isinstance(a, bool)
        or isinstance(b, bool)
        or not isinstance(a, (int, float))
        or not isinstance(b, (int, float))
    ):
        logger.warning("[%s] Invalid numeric parameters", request_id)
        return jsonify({"error": "a and b must be numbers"}), 400

    logger.info("[%s] Parameter validation successful", request_id)

    logger.info("[%s] STEP 3 - Performing calculation", request_id)

    if operation == "add":
        result = a + b
    elif operation == "subtract":
        result = a - b
    elif operation == "multiply":
        result = a * b
    else:
        if b == 0:
            logger.warning("[%s] Division by zero rejected", request_id)
            return jsonify({"error": "Division by zero"}), 400
        result = a / b

    logger.info(
        "[%s] Calculation completed: %s(%s, %s) = %s",
        request_id, operation, a, b, result
    )

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

    logger.info("[%s] STEP 4 - Preparing Table Storage entity", request_id)
    logger.info("[%s] PartitionKey: %s", request_id, entity["PartitionKey"])
    logger.info("[%s] RowKey      : %s", request_id, row_key)

    logger.info("[%s] STEP 5 - Authenticating with Managed Identity", request_id)
    logger.info("[%s] Authentication method: ManagedIdentityCredential", request_id)

    storage_start = time.perf_counter()

    try:
        logger.info(
            "[%s] STEP 6 - Sending create_entity request to %s",
            request_id,
            TABLE_ENDPOINT
        )

        # Azure SDK obtains an Entra ID token using the VM's
        # Managed Identity and sends an authenticated HTTPS request.
        table_client.create_entity(entity=entity)

        storage_ms = (time.perf_counter() - storage_start) * 1000

        logger.info("[%s] Azure Table Storage write successful", request_id)
        logger.info("[%s] Storage operation duration: %.2f ms", request_id, storage_ms)
        logger.info("[%s] Stored RowKey: %s", request_id, row_key)

    except AzureError as exc:
        logger.exception(
            "[%s] Azure Storage operation failed: %s",
            request_id,
            type(exc).__name__
        )

        logger.error(
            "[%s] Check Managed Identity, RBAC, Private Endpoint and DNS",
            request_id
        )

        return jsonify({
            "error": "Azure Table Storage operation failed",
            "requestId": request_id
        }), 502

    except Exception:
        logger.exception(
            "[%s] Unexpected error during Storage operation",
            request_id
        )

        return jsonify({
            "error": "Internal server error",
            "requestId": request_id
        }), 500

    logger.info("[%s] STEP 7 - Returning successful response", request_id)

    return jsonify({
        "operation": operation,
        "a": a,
        "b": b,
        "result": result,
        "rowKey": row_key,
        "status": "stored",
        "requestId": request_id
    }), 200


# ============================================================
# 7. START APPLICATION
# ============================================================

if __name__ == "__main__":
    logger.info("[STARTUP] Starting Flask server on 0.0.0.0:5000")

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
