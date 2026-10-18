# Azure Student Lab — Public-to-Private Networking with VMs, Flask, Managed Identity and Table Storage

**Tracks:** AZ-900 · AZ-104 · AZ-500  
**Estimated duration:** 3–4 hours (allow extra time for Azure provisioning and RBAC propagation)  
**Platform:** Azure Portal first; Ubuntu 24.04 LTS; Python 3.12; Azure CLI; PowerShell on Windows  
**Goal:** Build a working Python calculator API, reach it publicly, isolate it, restore access over VNet peering, then privatize Azure Table Storage without using secrets.

> **Subscription note:** This lab uses two small VMs instead of an Azure Function App because some trial subscriptions cannot deploy the required Function App hosting plans. VMs, managed disks, public IPs and Private Endpoints can incur charges or consume Azure credits. Check quotas and permitted VM sizes first. Delete resources at the end.
>
> **Security note:** Flask's built-in development server is for this temporary classroom lab only. Do not expose it as a production service. Restrict public port 5000 to your own IP addresses and remove the public endpoint during the exercise. HTTP is unencrypted; do not transmit secrets.

## 1. Learning objectives

By the end, you will be able to:

- Explain resource groups, VNets, subnets, public/private IPs, NSGs, and VNet peering.
- Deploy Ubuntu VMs and test HTTP and SSH connectivity from different networks.
- Distinguish a missing network route, an NSG block, DNS resolution, and an RBAC error.
- Use a **system-assigned Managed Identity** from Python to write data into Azure Table Storage **without account keys or connection strings**.
- Test Azure Table Storage with Azure CLI from your **local PC** and from **VM-Client**, using different identities.
- Disable Storage public network access and restore private connectivity using a **Private Endpoint** and **Private DNS**.
- Validate the end-to-end flow and clean up resources.

## 2. Architecture and naming

Use unique names where Azure requires global uniqueness. Write down your actual values: commands below use placeholders such as `<STORAGE_ACCOUNT>`.

| Resource | Suggested name | Address / purpose |
|---|---|---|
| Resource group | `rg-private-lab` | Same Azure region for all resources |
| VNet-App | `vnet-app` | `10.10.0.0/16` |
| App subnet | `snet-app` | `10.10.1.0/24` |
| Private Endpoint subnet | `snet-private-endpoints` | `10.10.2.0/24` |
| VNet-Client | `vnet-client` | `10.20.0.0/16` |
| Client subnet | `snet-client` | `10.20.1.0/24` |
| Application VM | `vm-app` | Ubuntu 24.04, private IP in `10.10.1.0/24` |
| Client VM | `vm-client` | Ubuntu 24.04, private IP in `10.20.1.0/24` |
| Application NSG | `nsg-app` | SSH 22 and temporary HTTP 5000 |
| Client NSG | `nsg-client` | SSH 22 from student public IP |
| Storage account | `st<uniquelettersnumbers>` | Standard GPv2; must be globally unique, 3–24 lowercase letters/numbers |
| Table | `Calculations` | Azure Table Storage data |
| Storage Private Endpoint | `pe-storage-table` | Targets Storage **table** subresource |
| Private DNS zone | `privatelink.table.core.windows.net` | Linked to both VNets |

**Final traffic path:**

```text
Student PC --SSH--> VM-Client (public IP)
                         |
                         | curl / SSH over VNet peering (private IPs)
                         v
                      VM-App ---- Python Flask API :5000
                         |
                         | ManagedIdentityCredential (authentication)
                         | Azure RBAC: Storage Table Data Contributor (authorization)
                         | HTTPS to <STORAGE_ACCOUNT>.table.core.windows.net
                         v
                 Storage Private Endpoint (private IP)
                         |
                         v
                 Azure Table Storage / Calculations

Student PC -- Azure CLI --> Storage public endpoint: BLOCKED in final state
VM-Client -- Azure CLI / Managed Identity --> Storage Private Endpoint: ALLOWED
```

**Important:** Managed Identity is an *identity/authentication mechanism*, not a network hop. Private Endpoint and Private DNS provide the private network path. VM-App itself does **not** need a Private Endpoint: its private NIC IP is reachable from the peered VNet.

### Prerequisites

- Azure subscription with permissions to create VMs, VNets, NSGs, Storage, Private Endpoints, private DNS and role assignments. To assign roles you need `Owner`, `User Access Administrator`, or an equivalent role-assignment permission; `Contributor` alone is insufficient.
- A region with quota for two small Ubuntu VMs; choose a size permitted by your subscription. Two VMs and a Private Endpoint may not be free.
- Windows PC with **PowerShell**, an **SSH client** (`ssh -V`), **Azure CLI** (`az version`), and Internet access. Install Azure CLI if missing: [Microsoft's official install guide](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-windows).
- Your PC's public IPv4 address, found by opening `https://api.ipify.org` in a browser. Record it as `<MY_PUBLIC_IP>`.
- A terminal and the Azure Portal at `https://portal.azure.com`.
- Record the **actual** IPs, VM username and Storage account name as you proceed. Replace placeholders literally; do not type angle brackets.

---

## 3. Phase A — Deploy VNet-App and VM-App

### Step A1 — Create resource group and VNet-App

1. **Azure Portal → Resource groups → Create**. Name: `rg-private-lab`. Choose your region; **Review + create → Create**.
2. **Virtual networks → Create**. Name: `vnet-app`, resource group `rg-private-lab`, same region.
3. In **IP addresses**, set address space `10.10.0.0/16`; create subnet `snet-app` with `10.10.1.0/24` and `snet-private-endpoints` with `10.10.2.0/24`. Remove or adjust the default subnet if necessary.
4. **Review + create → Create**.

### Step A2 — Create VM-App

1. **Virtual machines → Create → Azure virtual machine**.
2. Resource group `rg-private-lab`; name `vm-app`; image **Ubuntu Server 24.04 LTS**; smallest permitted size with sufficient memory (e.g., a B-series size if available).
3. Authentication: **SSH public key**; username `azureuser`. Generate/download a key pair or use an existing public key. **Keep the private key secure on your PC**.
4. Under **Networking**: VNet `vnet-app`, subnet `snet-app`, **Public IP: create**, NIC NSG **Basic or Advanced** (use `nsg-app` for advanced). Permit **SSH (22)** only from `<MY_PUBLIC_IP>/32`, not the entire Internet. Azure's VM creation wizard may create an NSG automatically; use that NSG rather than accidentally creating two competing ones.
5. Under **Management → Identity** (or **Identity** on the deployed VM), enable **System assigned managed identity**. If not available in the wizard, enable it after deployment under **VM-App → Identity → System assigned → On → Save**.
6. Create the VM. Record `<APP_PUBLIC_IP>` and `<APP_PRIVATE_IP>` from **VM-App → Overview / Networking**.
7. Confirm the NSG's inbound SSH rule source is your public IPv4 `/32`. If the wizard created an `AllowSSH` rule with `Any`, tighten it.

### Step A3 — SSH and install VM-App prerequisites

On your **local PC** (PowerShell), run (replace the key path and IP):

```powershell
ssh -i "$HOME\.ssh\<YOUR_PRIVATE_KEY>" azureuser@<APP_PUBLIC_IP>
```

On **VM-App**:

```bash
sudo apt update
sudo apt install -y python3.12 python3-pip python3.12-venv curl dnsutils openssh-client
python3.12 --version
mkdir -p ~/calculator-api
cd ~/calculator-api
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install flask azure-identity azure-data-tables
```

**Do not** use `sudo pip install` into Ubuntu's system Python. Keep the virtual environment.

---

## 4. Phase B — Create public Storage and configure Managed Identity

### Step B1 — Create Storage and Table

1. **Storage accounts → Create**. Resource group `rg-private-lab`, same region; globally unique name `<STORAGE_ACCOUNT>`; **Standard** performance; **StorageV2 (general purpose v2)**. Choose **locally redundant storage (LRS)** for the lab.
2. **Networking:** initially **Enable public network access from all networks**. This is deliberate for the first tests. Keep **Require secure transfer** enabled.
3. Create the Storage account.
4. **Storage account → Data storage → Tables → + Table**; name it exactly **`Calculations`** (capital C). Create it. This is required: an absent table produces `TableNotFound`.
5. Record the account name (not its full URL).

### Step B2 — Grant VM-App write access

1. **Storage account → Access control (IAM) → Add → Add role assignment**.
2. Search for **Storage Table Data Contributor** (**not** generic `Contributor`).
3. **Members → Assign access to: Managed identity → Select members → Virtual machine → `vm-app`**.
4. **Review + assign**. Allow several minutes for permissions to propagate.
5. On VM-App, Flask will use `ManagedIdentityCredential()` directly. **No `az login` is required for the Python application**.

> Optional: For CLI tests from your local PC, grant your own Azure user **Storage Table Data Reader** on the Storage account. Portal management access does not automatically confer Table data access. The person doing the lab must have permission to assign this role.

### Step B3 — Create the complete Flask application on VM-App

On **VM-App**, create the file:

```bash
cd ~/calculator-api
nano app.py
```

Paste the complete Python 3.12 code:

```python
import logging
import os
import time
import uuid
from datetime import datetime, timezone

from azure.core.exceptions import AzureError
from azure.data.tables import TableServiceClient
from azure.identity import ManagedIdentityCredential
from flask import Flask, jsonify, request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("calculator-api")
logging.getLogger("azure").setLevel(logging.WARNING)

app = Flask(__name__)

STORAGE_ACCOUNT_NAME = os.environ.get("STORAGE_ACCOUNT_NAME", "").strip()
TABLE_NAME = os.environ.get("TABLE_NAME", "Calculations").strip()

if not STORAGE_ACCOUNT_NAME:
    raise RuntimeError("STORAGE_ACCOUNT_NAME must be set to the storage account NAME")

endpoint = f"https://{STORAGE_ACCOUNT_NAME}.table.core.windows.net"
credential = ManagedIdentityCredential()
table_client = TableServiceClient(
    endpoint=endpoint, credential=credential
).get_table_client(table_name=TABLE_NAME)

logger.info("Storage account: %s", STORAGE_ACCOUNT_NAME)
logger.info("Table: %s", TABLE_NAME)
logger.info("Endpoint: %s", endpoint)
logger.info("Authentication: VM system-assigned Managed Identity")


@app.get("/health")
def health():
    return jsonify(status="ok", service="calculator-api")


@app.post("/api/calculate")
def calculate():
    request_id = uuid.uuid4().hex[:8]
    started = time.monotonic()
    logger.info("[%s] POST /api/calculate from %s", request_id, request.remote_addr)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Expected a JSON object", requestId=request_id), 400

    operation = payload.get("operation")
    a = payload.get("a")
    b = payload.get("b")

    if operation not in ("add", "subtract", "multiply", "divide"):
        return jsonify(error="Invalid operation", requestId=request_id), 400
    if (isinstance(a, bool) or isinstance(b, bool)
            or not isinstance(a, (int, float))
            or not isinstance(b, (int, float))):
        return jsonify(error="a and b must be numbers", requestId=request_id), 400
    if operation == "divide" and b == 0:
        return jsonify(error="Cannot divide by zero", requestId=request_id), 400

    operations = {
        "add": lambda x, y: x + y,
        "subtract": lambda x, y: x - y,
        "multiply": lambda x, y: x * y,
        "divide": lambda x, y: x / y,
    }
    result = operations[operation](a, b)
    logger.info("[%s] Calculated %s(%s, %s) = %s", request_id, operation, a, b, result)

    row_key = str(uuid.uuid4())
    entity = {
        "PartitionKey": "calculations",
        "RowKey": row_key,
        "Operation": operation,
        "A": a,
        "B": b,
        "Result": result,
        "CreatedAt": datetime.now(timezone.utc).isoformat(),
    }

    try:
        logger.info("[%s] Writing to %s / %s using Managed Identity", request_id, endpoint, TABLE_NAME)
        table_client.create_entity(entity=entity)
    except AzureError:
        logger.exception("[%s] Azure Table Storage write failed", request_id)
        return jsonify(error="Azure Table Storage operation failed", requestId=request_id), 502

    logger.info("[%s] Stored row %s in %.1f ms", request_id, row_key,
                (time.monotonic() - started) * 1000)
    return jsonify(
        operation=operation, a=a, b=b, result=result,
        status="stored", rowKey=row_key, requestId=request_id,
    ), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
```

Set the environment variable **to the actual Storage account name**, not a URL:

```bash
export STORAGE_ACCOUNT_NAME="<STORAGE_ACCOUNT>"
export TABLE_NAME="Calculations"
source ~/calculator-api/.venv/bin/activate
python ~/calculator-api/app.py
```

Leave this terminal running for the first tests. In a **second SSH session** to VM-App, test locally:

```bash
curl -i http://127.0.0.1:5000/health
curl -i -X POST http://127.0.0.1:5000/api/calculate \
  -H 'Content-Type: application/json' \
  -d '{"operation":"multiply","a":12,"b":22}'
```

**Expected:** `/health` returns HTTP `200`; `/api/calculate` returns HTTP `200`, `result: 264`, `status: stored`, and a generated `rowKey`. If it fails with `TableNotFound`, create the `Calculations` table. If it fails with `403`, check **Storage Table Data Contributor** on VM-App's Managed Identity and wait for RBAC propagation.

### Step B4 — Make Flask persistent **before removing the public IP**

Stop the foreground Flask process with `Ctrl+C`. Create a systemd service on VM-App:

```bash
sudo tee /etc/systemd/system/calculator-api.service > /dev/null <<EOF_SERVICE
[Unit]
Description=Azure student calculator Flask API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=azureuser
WorkingDirectory=/home/azureuser/calculator-api
Environment=STORAGE_ACCOUNT_NAME=<STORAGE_ACCOUNT>
Environment=TABLE_NAME=Calculations
ExecStart=/home/azureuser/calculator-api/.venv/bin/python /home/azureuser/calculator-api/app.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF_SERVICE

sudo systemctl daemon-reload
sudo systemctl enable --now calculator-api
sudo systemctl status calculator-api --no-pager
curl -i http://127.0.0.1:5000/health
```

**Replace `<STORAGE_ACCOUNT>` inside the service definition before running it.** View logs with:

```bash
sudo journalctl -u calculator-api -n 60 --no-pager
```

---

## 5. Phase C — Public HTTP tests from local PC

### Step C1 — Allow temporary public HTTP access

1. **VM-App → Networking → Network settings / Inbound port rules**, or open `nsg-app` directly.
2. Add inbound rule: **Source IP addresses** = `<MY_PUBLIC_IP>/32`; **Destination port** `5000`; **Protocol TCP**; **Action Allow**; **Priority 300**; name `Allow-HTTP-Student-PC`.
3. If both a subnet NSG and a NIC NSG are attached, **both must allow** the traffic.

On **local PC**, PowerShell:

```powershell
Invoke-RestMethod -Uri "http://<APP_PUBLIC_IP>:5000/health" -Method GET

Invoke-RestMethod `
  -Uri "http://<APP_PUBLIC_IP>:5000/api/calculate" `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"operation":"multiply","a":12,"b":22}'
```

**Expected:** status `ok`, then `result: 264` and `status: stored`.

> Keep HTTP open only to the IPs needed for the exercise. If your client VM uses a different public outbound IP, add a second temporary rule for that IP in the next phase; do not broaden access to `Any` unless your instructor explicitly requires it for a tightly supervised short-lived demonstration.

---

## 6. Phase D — Deploy VNet-Client and test VM-App's public IP

### Step D1 — Create VNet-Client and VM-Client

1. **Virtual networks → Create** `vnet-client`, address space `10.20.0.0/16`, subnet `snet-client` `10.20.1.0/24`.
2. **Virtual machines → Create** `vm-client`, Ubuntu 24.04 LTS, same region, username `azureuser`, SSH public key, VNet `vnet-client`, subnet `snet-client`, **public IP enabled**.
3. NSG `nsg-client`: inbound SSH (22) only from `<MY_PUBLIC_IP>/32`. VM-Client does **not** need public inbound port 5000.
4. Enable **System-assigned Managed Identity** on VM-Client via **VM-Client → Identity → System assigned → On → Save**.
5. Record `<CLIENT_PUBLIC_IP>` and `<CLIENT_PRIVATE_IP>`.

On **local PC**:

```powershell
ssh -i "$HOME\.ssh\<YOUR_PRIVATE_KEY>" azureuser@<CLIENT_PUBLIC_IP>
```

On **VM-Client**, install diagnostics and Azure CLI:

```bash
sudo apt update
sudo apt install -y curl dnsutils iputils-ping openssh-client ca-certificates
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
az version
```

### Step D2 — Test VM-App over its **public IP** from VM-Client

On VM-Client:

```bash
curl -i --connect-timeout 5 http://<APP_PUBLIC_IP>:5000/health
```

If this times out, the VM-App NSG may permit only your PC's public IP. **Determine the actual public egress IP of VM-Client**:

```bash
curl -4 https://api.ipify.org
```

In `nsg-app`, add a **temporary** inbound allow TCP 5000 rule for `<CLIENT_EGRESS_PUBLIC_IP>/32`, e.g. priority `310`, name `Allow-HTTP-Client-Public`. Retry the curl command. Do not assume VM-Client's outbound IP equals its assigned inbound public IP; verify it.

**Expected:** HTTP 200. At this stage, the VNets are not peered; the traffic is using VM-App's public endpoint.

### Step D3 — Observe isolation before peering

From VM-Client:

```bash
curl -v --connect-timeout 5 http://<APP_PRIVATE_IP>:5000/health
```

**Expected:** connection fails or times out because VNet-Client has no private route to VNet-App. Do not rely on `ping`: ICMP can be blocked independently of TCP, so **curl is the authoritative application test**.

---

## 7. Phase E — Remove VM-App's public IP, then peer VNets

### Step E1 — Remove the public IP from VM-App

**Before this step:** confirm `calculator-api` is running as a systemd service and you have recorded VM-App's private IP.

1. **VM-App → Networking → Network interface** (open the NIC resource).
2. **IP configurations → `ipconfig1` → Public IP address → Disassociate / None → Save**. Portal wording varies.
3. Verify VM-App still has `<APP_PRIVATE_IP>` but **no public IP**.

Now test:

- Local PC → SSH to **old** `<APP_PUBLIC_IP>`: **should fail**.
- Local PC → HTTP to **old** `<APP_PUBLIC_IP>:5000`: **should fail**.
- VM-Client → HTTP to **old** `<APP_PUBLIC_IP>:5000`: **should fail**.
- VM-Client → HTTP to `<APP_PRIVATE_IP>:5000`: **still fails** before peering.

Use a 5-second timeout for curl tests. The old public IP may later be reassigned, so do not treat any unexpected response from it as proof of access to your VM.

> Removing the public IP does **not** automatically remove public outbound connectivity from the VM; outbound behavior depends on Azure networking configuration. For this lab, verify the service is running before removing the IP, and do not rely on new package downloads afterward.

### Step E2 — Create **bidirectional VNet Peering**

1. **VNet-App → Peerings → Add**.
2. Peer to `vnet-client`, and configure the reverse peering from `vnet-client` back to `vnet-app` (the Portal may offer to create both together).
3. Allow **virtual network access** in both directions; leave gateway transit and forwarded traffic at defaults unless directed otherwise.
4. Verify **Connected** on both peering entries.

On **VM-Client**:

```bash
curl -i --connect-timeout 5 http://<APP_PRIVATE_IP>:5000/health
curl -i -X POST http://<APP_PRIVATE_IP>:5000/api/calculate \
  -H 'Content-Type: application/json' \
  -d '{"operation":"multiply","a":12,"b":22}'
```

**Expected:** HTTP 200 if NSG and guest firewall allow the connection. **Azure's default `AllowVNetInBound` NSG rule often already allows traffic from peered VNets.** Do not assume a custom allow rule is always required.

### Step E3 — NSG troubleshooting exercise

If private curl fails, check in order:

1. Peering status = **Connected** both ways; correct private IP; Flask service running.
2. VM-App's NIC and subnet effective security rules: look for a higher-priority deny rule.
3. Guest firewall (`sudo ufw status` on VM-App, if enabled).
4. Add an explicit inbound NSG allow rule **only if necessary**: source `10.20.0.0/16`, destination `<APP_PRIVATE_IP>`, protocol TCP, port `5000`, priority `300` **if free and not superseded by a higher-priority deny**.
5. Retry private curl.

**Optional controlled experiment:** create a temporary inbound **Deny** rule on VM-App for TCP 5000 from `10.20.0.0/16` with priority `250`, confirm curl fails, then **remove that deny rule** and verify it succeeds. This illustrates NSG priority without depending on an accidental misconfiguration.

### Step E4 — Optional: SSH privately from VM-Client to VM-App

VM-App no longer has a public IP. You can administer it from VM-Client after peering, but you need an authorized SSH key on VM-App.

**Safe key setup:** On VM-Client, generate a **new** key pair; display only the **public** key:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_lab -N ''
cat ~/.ssh/id_ed25519_lab.pub
```

Copy the printed `ssh-ed25519 ...` **public key**. In **Azure Portal → VM-App → Run command → RunShellScript**, run the following after replacing `<CLIENT_VM_PUBLIC_KEY>` with the entire printed public key:

```bash
sudo mkdir -p /home/azureuser/.ssh
printf '%s\n' '<CLIENT_VM_PUBLIC_KEY>' | sudo tee -a /home/azureuser/.ssh/authorized_keys >/dev/null
sudo chown -R azureuser:azureuser /home/azureuser/.ssh
sudo chmod 700 /home/azureuser/.ssh
sudo chmod 600 /home/azureuser/.ssh/authorized_keys
```

Do **not** paste the private key into Azure Portal or copy it between machines. On VM-Client:

```bash
ssh -i ~/.ssh/id_ed25519_lab azureuser@<APP_PRIVATE_IP>
sudo systemctl status calculator-api --no-pager
```

The Portal's **Run command** feature can also help inspect VM-App if SSH is not yet working; it does not require a public IP.

---

## 8. Phase F — Test Azure Table Storage publicly with Azure CLI

The Flask API is already using **VM-App's system-assigned Managed Identity** to write to Storage. Now compare two separate **Azure CLI** identities:

| Caller | Identity | Required role | Test |
|---|---|---|---|
| Local PC | Student's Entra user via `az login` | `Storage Table Data Reader` | `az storage entity query` |
| VM-Client | VM-Client's system-assigned Managed Identity via `az login --identity` | `Storage Table Data Reader` | `az storage entity query` |
| VM-App Flask | VM-App's system-assigned Managed Identity via Python SDK | `Storage Table Data Contributor` | `create_entity()` |

### Step F1 — Assign VM-Client read role

**Storage account → Access control (IAM) → Add role assignment → Storage Table Data Reader → Managed identity → Virtual machine → `vm-client` → Review + assign**.

Ensure your **local PC's signed-in Azure user** also has `Storage Table Data Reader` if you want the local query to succeed. Wait for role assignments to propagate.

### Step F2 — Test from local PC (public endpoint)

On **local PC PowerShell**:

```powershell
az login
az account show --output table
az storage entity query `
  --account-name <STORAGE_ACCOUNT> `
  --table-name Calculations `
  --auth-mode login `
  --num-results 10 `
  --output table
```

**Expected:** query succeeds and shows previously stored rows. If multiple subscriptions exist, choose the correct one with `az account set --subscription "<SUBSCRIPTION_ID>"`.

### Step F3 — Test from VM-Client (public endpoint)

On **VM-Client**:

```bash
az logout 2>/dev/null || true
az login --identity
az account show --output table
az storage entity query \
  --account-name <STORAGE_ACCOUNT> \
  --table-name Calculations \
  --auth-mode login \
  --num-results 10 \
  --output table
```

**Expected:** query succeeds. `az logout` prevents accidentally using a previous device-code login; `az login --identity` makes the VM identity the active CLI identity. If the VM identity has access to multiple subscriptions, select the correct one.

**Check DNS before privatization**, from VM-Client:

```bash
nslookup <STORAGE_ACCOUNT>.table.core.windows.net
```

**Expected:** public Storage service address (possibly through a CNAME chain). The exact public IP is not fixed.

---

## 9. Phase G — Disable Storage public access and observe failures

1. **Storage account → Networking → Firewalls and virtual networks / Public network access**.
2. Select **Disabled** for **Public network access** and save. Do not disable the Storage account itself.
3. Allow time for the networking setting to take effect.

**Retest from local PC**:

```powershell
az storage entity query `
  --account-name <STORAGE_ACCOUNT> `
  --table-name Calculations `
  --auth-mode login `
  --num-results 10 `
  --output table
```

**Retest from VM-Client**:

```bash
az storage entity query \
  --account-name <STORAGE_ACCOUNT> \
  --table-name Calculations \
  --auth-mode login \
  --num-results 10 \
  --output table
```

**Retest the application from VM-Client**:

```bash
curl -i -X POST http://<APP_PRIVATE_IP>:5000/api/calculate \
  -H 'Content-Type: application/json' \
  -d '{"operation":"multiply","a":12,"b":22}'
```

**Expected:** both Azure CLI data queries fail due to Storage's public network restriction, and Flask's write fails (the sample API returns HTTP 502 with an error). Exact Storage error messages and HTTP status codes vary; a `403` can represent either a network restriction or insufficient RBAC, so **verify that both queries succeeded in Phase F first**. The Flask `/health` endpoint should still return 200 because it does not access Storage.

> Disabling public access does not block Azure Portal management operations; the test concerns **Table data-plane requests**. DNS may still resolve to a public address until Private DNS is configured.

---

## 10. Phase H — Create Storage Private Endpoint and Private DNS

### Step H1 — Create Private Endpoint for the **table** service

1. **Storage account → Networking → Private endpoint connections → + Private endpoint** (or **Private Link Center → Private endpoints → Create**).
2. Name `pe-storage-table`; resource group `rg-private-lab`; same region.
3. Resource: your Storage account; **Target subresource = `table`**. Do **not** select `blob` or `file`.
4. Virtual network `vnet-app`; subnet `snet-private-endpoints`.
5. On **DNS**, select **Integrate with private DNS zone = Yes** and use/create `privatelink.table.core.windows.net`.
6. Create. Verify Private Endpoint connection status is **Approved** and record `<STORAGE_PE_PRIVATE_IP>`.

### Step H2 — Link Private DNS to **both VNets**

1. **Private DNS zones → `privatelink.table.core.windows.net`**.
2. Check **Recordsets**: the Storage account name should have an **A record** pointing to `<STORAGE_PE_PRIVATE_IP>`. Azure normally creates this automatically with the Private DNS zone group.
3. **Virtual network links → + Add**: link `vnet-app`, **auto-registration disabled**.
4. Add a second link to `vnet-client`, **auto-registration disabled**.
5. Confirm both links show **Completed**.

> Linking only VNet-App is insufficient: VM-Client must resolve the Storage hostname to the Private Endpoint IP too. The Storage SDK and CLI continue to use the **normal** hostname `<STORAGE_ACCOUNT>.table.core.windows.net`; do not replace it with the `privatelink` hostname or a raw IP.

### Step H3 — Validate DNS from VM-Client and VM-App

On **VM-Client**:

```bash
nslookup <STORAGE_ACCOUNT>.table.core.windows.net
```

**Expected:** the normal Storage hostname resolves (often through a `privatelink` CNAME) to `<STORAGE_PE_PRIVATE_IP>`, an address in `10.10.2.0/24`.

If you enabled private SSH in Step E4, on **VM-App** run the same command:

```bash
nslookup <STORAGE_ACCOUNT>.table.core.windows.net
```

Alternatively use **VM-App → Run command → RunShellScript** to run the DNS test. Both VMs should resolve to the same Private Endpoint IP. DNS updates can take a little time; verify links and records before assuming a network failure.

### Step H4 — Retest Storage from VM-Client (private)

On **VM-Client**:

```bash
az logout 2>/dev/null || true
az login --identity
az storage entity query \
  --account-name <STORAGE_ACCOUNT> \
  --table-name Calculations \
  --auth-mode login \
  --num-results 10 \
  --output table
```

**Expected:** success, now via the **private** Storage IP. VM-Client can reach the endpoint because it has VNet peering to VNet-App. If this fails, verify the Private Endpoint approval, `table` subresource, both VNet links, DNS, RBAC, and effective NSG rules.

### Step H5 — Retest Storage from the **local PC** (still blocked)

On **local PC PowerShell**, repeat the query from Step F2.

**Expected:** data-plane access still fails. Your PC is **not** in either VNet and has no private route to the Private Endpoint. Its normal DNS resolution typically remains public. A VPN/private DNS setup could change this outcome; the lab assumes the PC is on the ordinary public Internet.

---

## 11. Phase I — Final end-to-end private validation

From **VM-Client**, call the API using VM-App's **private IP**:

```bash
curl -i -X POST http://<APP_PRIVATE_IP>:5000/api/calculate \
  -H 'Content-Type: application/json' \
  -d '{"operation":"multiply","a":12,"b":22}'
```

**Expected response** (IDs vary):

```json
{
  "operation": "multiply",
  "a": 12,
  "b": 22,
  "result": 264,
  "status": "stored",
  "rowKey": "<GENERATED_UUID>",
  "requestId": "<GENERATED_ID>"
}
```

Verify the new record using **VM-Client's Managed Identity**:

```bash
az storage entity query \
  --account-name <STORAGE_ACCOUNT> \
  --table-name Calculations \
  --auth-mode login \
  --num-results 20 \
  --output table
```

Compare the returned `rowKey` with the `RowKey` in the table. If you have private SSH access to VM-App, inspect its logs:

```bash
sudo journalctl -u calculator-api -n 80 --no-pager
```

**Final path to explain to the instructor:**

1. VM-Client sends HTTP to VM-App's **private IP** across VNet peering.
2. VM-App executes the Flask calculation.
3. Python obtains an identity token using **VM-App's system-assigned Managed Identity**.
4. Azure RBAC grants that identity **Storage Table Data Contributor**.
5. DNS resolves the Storage Table endpoint to the **Private Endpoint IP**.
6. VM-App writes the entity over private network connectivity.
7. VM-Client uses **its own** Managed Identity (`Storage Table Data Reader`) to verify the row over the same Private Endpoint.
8. The local PC still cannot query Storage over the public Internet.

### Expected connectivity matrix

| Test | Initial public phase | After VM-App public IP removed, before peering | After peering, before Storage lockdown | After Storage public access disabled, before PE | Final state with PE + DNS |
|---|---|---|---|---|---|
| PC → VM-App Flask public IP | Works | Fails | Fails | Fails | Fails |
| VM-Client → VM-App Flask public IP | Works | Fails | Fails | Fails | Fails |
| VM-Client → VM-App Flask private IP `/health` | Fails (no peering) | Fails | Works | Works | Works |
| PC → Storage Table data via Azure CLI | Works | Works | Works | Fails | Fails |
| VM-Client → Storage Table data via Azure CLI | Works | Works | Works | Fails | Works privately |
| VM-App → Storage Table write via Flask | Works | Works* | Works | Fails | Works privately |

\*Assumes VM-App retains the outbound connectivity needed to reach the public Storage service after its public IP is removed. If not, diagnose Azure outbound networking rather than treating it as a Managed Identity failure.

---

## 12. Troubleshooting reference

| Symptom | Likely cause | Check / action |
|---|---|---|
| `TableNotFound` | `Calculations` table does not exist or wrong capitalization/account | Storage account → **Tables**; create `Calculations` |
| Flask returns `502` | Storage write failed | Read `sudo journalctl -u calculator-api -n 80 --no-pager`; inspect underlying Azure exception |
| Storage `403` before lockdown | RBAC missing, wrong identity, or firewall rule | Check `Storage Table Data Contributor` for VM-App; `Storage Table Data Reader` for VM-Client/local user; wait for propagation |
| `az login --identity` fails | Managed Identity disabled, IMDS inaccessible, or incorrect VM context | VM-Client → **Identity**; ensure System assigned = On; run on Azure VM |
| CLI uses personal account instead of VM identity | Previous interactive login active | On VM-Client: `az logout`, then `az login --identity` and `az account show` |
| Public HTTP times out | Wrong public IP, Flask not running, source IP not allowed, NSG/firewall | Check service, `curl localhost`, NSG source `/32`, VM's public IP |
| Private HTTP times out after peering | Peering disconnected, NSG deny, wrong private IP, guest firewall | Check peering **Connected**, effective NSG rules, Flask bind `0.0.0.0:5000` |
| SSH to VM-App stops after IP removal | Expected; no direct public endpoint | SSH from VM-Client over peering, or use Portal **Run command** |
| DNS still returns public Storage IP after PE | Missing zone A record or VNet link; wrong zone | Check `privatelink.table.core.windows.net`, recordset, links to **both** VNets |
| DNS resolves to private IP but Storage query fails | Private Endpoint not approved, wrong subresource, NSG/routing, or RBAC | Verify PE `table`, approved state, port 443 reachability, and data-plane roles |
| Python `ManagedIdentityCredential` fails | VM-App identity disabled or credential unavailable | VM-App → Identity; check application logs; Python SDK uses VM identity independently of CLI login |
| Packages fail to install | VM outbound access unavailable | Install before removing VM-App public IP; inspect outbound connectivity and package mirrors |

### Optional network diagnostic commands

On VM-Client:

```bash
# Is the application reachable privately?
curl -v --connect-timeout 5 http://<APP_PRIVATE_IP>:5000/health

# Does Storage resolve privately?
nslookup <STORAGE_ACCOUNT>.table.core.windows.net

# Does HTTPS port 443 accept a connection? (not an RBAC test)
curl -v --connect-timeout 5 https://<STORAGE_ACCOUNT>.table.core.windows.net/
```

An HTTP `403` or other service response to the last command can still prove that the network connection reached Azure Storage; it does **not** prove data authorization.

---

## 13. Knowledge-check questions

1. What is the difference between a VM public IP and private IP?
2. Why does VM-Client fail to reach VM-App's private IP before VNet peering?
3. Does VNet peering automatically override a restrictive NSG rule?
4. Why can Flask `/health` succeed when `/api/calculate` fails after Storage public access is disabled?
5. What does `ManagedIdentityCredential()` replace in the application?
6. What is the difference between `Storage Table Data Reader` and `Storage Table Data Contributor`?
7. Why must the Private DNS zone be linked to both VNets?
8. Why does the local PC still fail to query Storage after the Private Endpoint is created?
9. Which resource requires the Private Endpoint: VM-App, Storage Account, or both?
10. Which tool helps distinguish an application error from a DNS/network/RBAC error?

## 14. Cleanup — mandatory

1. Save screenshots or notes required by the instructor.
2. **Azure Portal → Resource groups → `rg-private-lab` → Delete resource group**.
3. Enter the resource group name to confirm. Wait until deletion completes.
4. Verify no lab resources remain (including public IPs, managed disks, Private Endpoints, and private DNS zones). If you created anything outside the resource group, delete it separately.

**Lab complete:** You have demonstrated public access, network isolation, VNet peering, NSG control, Managed Identity and Azure RBAC, Azure Table Storage public lockdown, Private Endpoint, Private DNS and private end-to-end validation.
