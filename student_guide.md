# Student lab — Build, connect and secure an Azure serverless application

**Azure certifications:** AZ-900, AZ-104, AZ-500  
**Estimated time:** 3–4 hours guided session  
**Method:** Azure Portal for provisioning/configuration; Ubuntu VM CLI for application publishing and tests  
**Goal:** Learn Azure concepts through controlled failures and fixes.

> **Important:** Never execute an infrastructure deployment script for this lab. Every Azure resource and every networking/security rule is created by you through **portal.azure.com**. Commands appear only for installing tools, publishing code, SSH, testing, and reading stored data.

## 0. Mission, architecture and prerequisites (15 min)

Your company has a calculation API. Clients submit two numbers and an operation (`add`, `subtract`, `multiply`, `divide`); the API stores each result in Azure Table Storage. Initially, the API is accessible over the Internet. Your mission is to make it private and accessible from a management VM located in a **different** virtual network. You will establish private connectivity using VNet peering and Private DNS, then retrieve the results privately using Azure CLI.

### Target topology

```text
Your laptop / Postman (initial public API test)
   | HTTPS 443 (before lockdown)
   v
Azure Function App (same app throughout lab)
   | public ingress initially; Private Endpoint added later
   | outbound VNet Integration -> snet-func-integration
   |
VNet-App 10.10.0.0/16
   snet-func-integration 10.10.1.0/27 (delegated, outbound only)
   snet-private-endpoints  10.10.2.0/24
      - Function Private Endpoint: sites
      - Table Storage Private Endpoint: table (later)
   |
   | VNet peering (created later; NOT sufficient alone)
   |
VNet-Client 10.20.0.0/16
   snet-vm 10.20.1.0/24
      - Ubuntu VM with public IP for SSH from your own IP

Data Storage Account -> Table 'Calculations'
Runtime Storage Account -> used internally by Azure Functions
```

### Resource naming worksheet

Use one unique suffix, e.g. `ab12`, throughout. Replace names below with your own.

| Resource | Suggested name |
|---|---|
| Resource group | `rg-az-securelab-ab12` |
| VNet-App | `vnet-app-ab12` |
| Function integration subnet | `snet-func-integration` |
| Private Endpoint subnet | `snet-private-endpoints` |
| VNet-Client | `vnet-client-ab12` |
| VM subnet | `snet-vm` |
| VM subnet NSG | `nsg-vm-ab12` |
| Function App | `func-calc-ab12` (globally unique) |
| Runtime Storage Account | `stfuncrt<unique>` (globally unique) |
| Data Storage Account | `stcalc<unique>` (globally unique) |
| Ubuntu VM | `vm-client-ab12` |
| Function PE | `pe-func-ab12` |
| Table PE | `pe-table-ab12` |

**Prerequisites:** active Azure subscription; permissions to create resources and **role assignments** (`Owner` or equivalent RBAC including User Access Administrator); public IPv4 address; a computer with PowerShell or Bash, SSH and optional Postman; install Python 3.12, Azure CLI and Functions Core Tools v4 on Ubuntu. Your subscription must support the chosen Function hosting plan and VM SKU. Charges apply to VM, Private Endpoints, Storage and Function usage; delete resources at the end.

### Questions before starting

- Which components are **IaaS**, **PaaS** and **serverless**? Who patches the Ubuntu operating system? Who manages the Functions runtime?
- What is the difference between a **VNet**, **subnet**, **NSG**, **Private Endpoint** and **VNet peering**?
- Does allowing inbound SSH (22) imply outbound HTTPS (443) is blocked? **No.** Azure NSGs have default outbound allow rules. Review them, but do not create additional HTTPS rules in this lab.

---

## 1. Create Azure application resources (40 min)

### 1.1 Create the resource group

1. Azure Portal → search **Resource groups** → **+ Create**.
2. **Subscription:** select your lab subscription.
3. **Resource group:** `rg-az-securelab-ab12`.
4. **Region:** choose a region supporting Flex Consumption, Private Endpoints and your VM SKU (use the same region for all resources).
5. **Review + create → Create**.

**Concept — Resource Group:** a logical management boundary for Azure resources. It is not a network or security isolation boundary by itself.

### 1.2 Create two Storage Accounts

1. Azure Portal → **Storage accounts → + Create**.
2. Select your resource group, region, **Standard** performance and **LRS** redundancy for this training exercise.
3. Name the first account `stfuncrt<unique>` (**runtime**). Keep public networking enabled for the lab; do not harden it in the later Table Storage exercise.
4. **Review + create → Create**.
5. Repeat for `stcalc<unique>` (**application data**). Start with **Public network access: Enabled from all networks**.
6. Open **data Storage Account → Data storage → Tables → + Table**; name it exactly `Calculations`.
7. Open **data Storage Account → Networking** and confirm public network access is enabled for now.

**Concept — Storage:** a Storage Account can expose different services (`blob`, `queue`, `table`, `file`). They have distinct endpoints. A Private Endpoint for `blob` does **not** provide private access to `table`.

### 1.3 Create the application VNet and subnets

1. Azure Portal → **Virtual networks → + Create**.
2. Name: `vnet-app-ab12`; resource group: your lab RG; region: same as above.
3. **IP addresses** tab → address space `10.10.0.0/16`.
4. Add subnet `snet-func-integration` with `10.10.1.0/27`. Under **Subnet delegation**, choose **Microsoft.App/environments** if offered/required for Flex Consumption. **Do not put Private Endpoints in this delegated subnet.**
5. Add subnet `snet-private-endpoints` with `10.10.2.0/24`; no delegation.
6. Create VNet.

> If the Flex Consumption creation workflow offers to create/delegate the integration subnet automatically, use that option instead, then verify the subnet appears in `vnet-app-ab12`. The exact Portal layout can change.

### 1.4 Create the Function App (one time only)

1. Azure Portal → **Function App → + Create**.
2. Choose **Flex Consumption** (or another trainer-approved plan supporting both VNet Integration and Private Endpoint).
3. **Basics** → select subscription, resource group, unique Function App name, **Runtime stack: Python**, supported Python version (select **3.12** (tested in this lab)), **Region** as above.
4. **Storage** → select `stfuncrt<unique>` for runtime storage. Do not use the application data account for runtime storage.
5. **Networking** → keep public inbound access enabled. Configure **outbound VNet Integration** with `vnet-app-ab12` → `snet-func-integration` if the wizard exposes it. Otherwise configure it in the next step.
6. **Review + create → Create**.
7. Open **Function App → Networking → Virtual network integration**. Confirm VNet-App and the integration subnet are attached. If not, select **Add VNet / Configure**, then choose the integration subnet.
8. Open **Function App → Networking → Public network access** and confirm **Enabled** for Phase 1.

**Concept — critical distinction:** **VNet Integration controls outbound connections from the Function App**. It does not make inbound HTTP traffic private. A **Private Endpoint** will be needed for private inbound HTTP later.

### 1.5 Understand Managed Identities BEFORE configuring them

A **Managed Identity** is an identity created and managed by Microsoft Entra ID for an Azure resource. Azure manages its credentials and rotation, so your application can obtain tokens without storing a username, password or Storage Account key. A **system-assigned** identity belongs to one resource and is deleted with that resource. A **user-assigned** identity is a separate Azure resource that can be attached to multiple services.

An identity **does not grant permissions automatically**. You must give it an appropriate Azure role at the right scope. This is **authentication (who are you?)** plus **authorization (what may you do?)**. It is separate from network connectivity.

**Questions:** If a Function has a Managed Identity but no Storage data role, can it write entities? Does an Azure Resource Group `Contributor` role automatically grant Table data access?

### 1.6 Enable Function identity and assign the Table data role

1. Azure Portal → **Function App → Identity → System assigned**.
2. Set **Status: On → Save**. Confirm the identity's **Object (principal) ID** appears.
3. Azure Portal → **data Storage Account → Access control (IAM) → + Add → Add role assignment**.
4. Search for **Storage Table Data Contributor** (not generic `Contributor`).
5. **Members → Assign access to: Managed identity → + Select members**.
6. Choose **Function App**, select your `func-calc-ab12`, confirm and **Review + assign**.
7. Azure Portal → **Function App → Settings → Environment variables** (sometimes **Configuration → Application settings**) → **+ Add**.
8. **Name:** `DATA_STORAGE_ACCOUNT`; **Value:** your data Storage Account name **without** `https://` → **Apply/Save**. Accept restart if prompted.
9. Wait a few minutes for the RBAC assignment to propagate.

## 2. Create the isolated Ubuntu client VNet and VM (30 min)

### 2.1 Create the client VNet

1. Azure Portal → **Virtual networks → + Create**.
2. Name `vnet-client-ab12`; address space `10.20.0.0/16`.
3. **IP addresses → Add subnet** `snet-vm` with `10.20.1.0/24`.
4. **Review + create → Create**. **Do not configure peering yet.**

### 2.2 Create the VM subnet NSG

1. Azure Portal → **Network security groups → + Create**; name `nsg-vm-ab12`.
2. Open **nsg-vm-ab12 → Inbound security rules → + Add**.
3. Source: **IP Addresses** → your **current public IPv4 `/32`**; destination **Any**; service **SSH** or TCP/22; action **Allow**; priority **100**; name `Allow-SSH-MyIP`.
4. Verify no custom inbound allow rule opens 80/443 to the VM.
5. Azure Portal → **Virtual networks → vnet-client-ab12 → Subnets → snet-vm → Network security group** → select `nsg-vm-ab12` → **Save**.
6. Review **nsg-vm-ab12 → Outbound security rules**. Notice the default `AllowInternetOutBound` and `AllowVnetOutBound` rules. **Do not create custom outbound HTTPS rules.**

**Concept:** NSG rules are evaluated by priority (lower number first), separately for inbound and outbound directions. NSGs are stateful. An SSH-only **inbound** policy does not block **outbound** HTTPS.

### 2.3 Deploy Ubuntu

1. Azure Portal → **Virtual machines → + Create → Azure virtual machine**.
2. Resource group: lab RG; VM name: `vm-client-ab12`; image: **Ubuntu Server 24.04 LTS**; size: small trainer-approved SKU (e.g. B1s if available).
3. Authentication: **SSH public key**; username `azureuser`. Generate/download a key pair or use your existing public key. **Keep the private key private.**
4. **Networking** → VNet `vnet-client-ab12`; subnet `snet-vm`; Public IP **enabled** for SSH; **NIC network security group: None** (because NSG is already attached to the subnet). If Portal insists on a NIC NSG, make sure it does not conflict with the lab rules.
5. **Review + create → Create**.
6. Open **VM → Overview → Public IP address**, then SSH:

```bash
ssh -i ~/.ssh/<PRIVATE_KEY_FILE> azureuser@<VM_PUBLIC_IP>
```

7. On Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y curl dnsutils jq
ip -4 addr
ip route
```

8. Continue with **section 2.5** to install tools, create the source files and publish the Function App **from this Ubuntu VM**. Then call the public Function URL from Ubuntu using the same POST and key. It should work because the Function App is public and VM outbound Internet is allowed.

**Checkpoint:** SSH works; VNet-Client is isolated from VNet-App; no peering exists yet.

---

### 2.4 Ubuntu as the development and testing workstation

The application is published **from the Ubuntu VM**, not the student's Windows laptop. First complete section 2 to create VNet-Client and Ubuntu, then return here to create the three source files and publish. The VM initially uses **public Internet connectivity** to reach Azure and the Function App. No peering is configured yet.

### 2.5 Install tools, create Python files and publish

After connecting by SSH to Ubuntu, install Python **3.12**, Azure CLI, Node.js 22 and Functions Core Tools. Copy commands **one at a time**; check each version before continuing.

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3-pip curl ca-certificates gnupg dnsutils jq git
python3.12 --version
```

**Azure CLI** (Microsoft installer used during the validated lab):

```bash
curl -fsSL 'https://azurecliprod.blob.core.windows.net/$root/deb_install.sh' | sudo bash
az version
```

> This runs a downloaded installer as root. Use only in the approved lab environment; in managed production environments, review the installer and use the official signed package repository process.

**Node.js 22 and Functions Core Tools v4**:

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
node --version
npm --version
sudo npm install -g azure-functions-core-tools@4 --unsafe-perm
func --version
```

Node.js **18** is insufficient for the npm installer of Core Tools **4.15.2** (requires Node >=22). If `func --version` returns `Permission denied` and `/usr/bin/func` links to a non-executable `main.js`, verify the path and apply the following fix **only for that specific issue**:

```bash
readlink -f /usr/bin/func
ls -l "$(readlink -f /usr/bin/func)"
sudo chmod +x "$(readlink -f /usr/bin/func)"
func --version
```

**Authenticate to Azure using your user account for deployment:**

```bash
az login --use-device-code
az account show --output table
```

If you have several subscriptions, use `az account set --subscription '<SUBSCRIPTION_ID>'`.

**Create the files yourself:**

```bash
mkdir -p ~/functionapp
cd ~/functionapp
nano function_app.py
```

Paste the following entire code, then save (`Ctrl+O`, Enter) and exit (`Ctrl+X`):

```python
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
```

Create `requirements.txt`:

```bash
nano requirements.txt
```

```text
azure-functions
azure-identity
azure-data-tables
```

Create `host.json`:

```bash
nano host.json
```

```json
{
  "version": "2.0",
  "logging": {
    "applicationInsights": {
      "samplingSettings": { "isEnabled": true, "excludedTypes": "Request" }
    }
  }
}
```

Optional: create a Python 3.12 virtual environment for local checks (deployment uses the remote Python runtime):

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m py_compile function_app.py
```

**Publish to the existing Function App; do not create a second one:**

```bash
func azure functionapp publish '<EXISTING_FUNCTION_APP_NAME>' --python
```

Azure Portal → **Function App → Functions** → verify `calculate` exists. Publish **before** disabling public access. A successful deployment does not prove that the application can write to Storage; perform the HTTP and Table validation below.

**Configuration pitfall verified during testing:** `DATA_STORAGE_ACCOUNT` must be the **account name only** (for example `stcalcatb`), **not** `https://stcalcatb.table.core.windows.net`. The code constructs the full URL itself. A full URL in this setting caused HTTP 502 from the Function and Storage `InvalidResourceName` (HTTP 400) in logs.

### 2.6 Test publicly from your laptop AND Ubuntu

1. Azure Portal → **Function App → Functions → calculate → Get function URL**.
2. Copy the URL (including the `code=` query parameter), or use the key in an `x-functions-key` header. **Treat this key as a secret.**
3. PowerShell example:

```powershell
$uri = 'https://<FUNCTION_APP_NAME>.azurewebsites.net/api/calculate'
$key = '<FUNCTION_KEY>'
$body = @{ operation = 'multiply'; a = 12; b = 8 } | ConvertTo-Json
Invoke-RestMethod -Uri $uri -Method Post -Headers @{ 'x-functions-key' = $key } -ContentType 'application/json' -Body $body
```

4. Equivalent Bash / curl:

```bash
curl -i -X POST 'https://<FUNCTION_APP_NAME>.azurewebsites.net/api/calculate' \
  -H 'Content-Type: application/json' -H 'x-functions-key: <FUNCTION_KEY>' \
  -d '{"operation":"multiply","a":12,"b":8}'
```

5. Repeat the same authenticated `curl` POST **from the Ubuntu VM**, while the Function App public endpoint is still enabled. Confirm HTTP 200 and a new saved entity.
6. Postman: method **POST**, URL `https://<FUNCTION_APP_NAME>.azurewebsites.net/api/calculate`; headers `Content-Type: application/json`, `x-functions-key: <FUNCTION_KEY>`; Body → **raw → JSON** with the same payload.
7. Expect HTTP **200**, `result: 96`, `status: stored`, and `rowKey`.
8. Azure Portal → **data Storage Account → Storage browser → Tables → Calculations**; locate the new entity. If the API returns 502, verify role assignment, app setting, table name, and identity; allow time for RBAC propagation.

**Checkpoint 1:** show successful API calls from both laptop and Ubuntu and a saved Table entity.

---

## 3. Make the existing Function App private (30 min)

### 3.1 Create its inbound Private Endpoint

1. Azure Portal → **Function App → Networking → Private endpoint connections → + Add / Create** (or **Private endpoints → + Create**, depending on Portal layout).
2. Name `pe-func-ab12`.
3. **Resource:** your **existing** `func-calc-ab12`; target subresource **`sites`**.
4. **Virtual network:** `vnet-app-ab12`; **Subnet:** `snet-private-endpoints`.
5. **DNS integration:** enable integration with private DNS zone **`privatelink.azurewebsites.net`**; create the zone if requested.
6. Create; wait until the connection is **Approved**.
7. Open **Private Endpoint → Overview → Network interface**; record its private IP, e.g. `10.10.2.x`.
8. Azure Portal → **Private DNS zones → privatelink.azurewebsites.net → Virtual network links**; verify VNet-App is linked and the Function App A record exists.

### 3.2 Disable public inbound access

1. Azure Portal → **Function App → Networking → Public network access** (or **Inbound traffic configuration → Public network access**).
2. Select **Disabled → Save**.
3. From your laptop, repeat the POST. **Expected: request rejected**, often HTTP 403. The exact error may vary.
4. From the Ubuntu VM, run:

```bash
nslookup <FUNCTION_APP_NAME>.azurewebsites.net
curl -vk --connect-timeout 5 https://<FUNCTION_APP_NAME>.azurewebsites.net/api/calculate
```

**Expected:** The VM is **not** yet able to reach the Function privately. It may still resolve the public address because the client VNet is not linked to private DNS. A GET request can also produce a method-related response if it reaches the application; the actual functional test uses POST.

**Questions:** Does the Function's VNet Integration make it reachable inbound? What IP does the Function hostname resolve to from the VM? Why is a Private Endpoint not the same as peering?

---

## 4. Connect the VNets with peering and Private DNS (20 min)

### 4.1 Introduce VNet peering

**Concept — VNet peering:** Peering enables private IP routing between two Azure VNets with non-overlapping address spaces. It does not create a Private Endpoint, configure DNS, or bypass NSGs. In this lab, the default Azure NSG rules already permit traffic between the peered VNets, so **no additional inbound or outbound HTTPS rules are required**.

Before proceeding, check that the Function Private Endpoint is **Approved** and note its IP address. The Ubuntu VM is still in a separate VNet.

### 4.2 Create VNet peering using the Portal

1. Azure Portal → **Virtual networks → vnet-client-ab12 → Peerings → + Add**.
2. **This virtual network peering link name:** `client-to-app`.
3. **Remote virtual network:** select `vnet-app-ab12`.
4. If prompted, **Remote virtual network peering link name:** `app-to-client`.
5. Keep **Allow virtual network access** enabled on both sides. Gateway transit and forwarded traffic are not needed.
6. Select **Add**. Verify both directions show **Connected** under **Virtual networks → Peerings**.
7. Azure Portal → **Network security groups → nsg-vm-ab12 → Outbound security rules**. Inspect the built-in **AllowVnetOutBound** rule (priority 65000). Do not add a custom HTTPS rule.

**Question:** Why can peered VNets communicate even though we only explicitly opened inbound SSH on the Ubuntu VM?

### 4.3 Link the Private DNS zone to the client VNet

VNet peering provides a network path, but the client must also resolve the Function App hostname to the **Private Endpoint IP**, rather than the public address.

1. Azure Portal → **Private DNS zones → privatelink.azurewebsites.net**.
2. Select **Virtual network links → + Add**.
3. **Link name:** `link-client-function`.
4. **Virtual network:** `vnet-client-ab12`.
5. **Enable auto registration:** No → **Create / OK**.
6. Open **Private DNS zones → privatelink.azurewebsites.net → Recordsets** and confirm an A record for the Function App points to `10.10.2.x`.
7. Connect to Ubuntu over SSH and run:

```bash
nslookup <FUNCTION_APP_NAME>.azurewebsites.net
getent ahostsv4 <FUNCTION_APP_NAME>.azurewebsites.net
```

**Expected:** The Function hostname resolves to its Private Endpoint IP (`10.10.2.x`). If it still resolves publicly, check the Private DNS zone link and record before testing HTTPS.

### 4.4 Call the existing Function App privately

On Ubuntu, execute:

```bash
curl -v --connect-timeout 5 -X POST \
  'https://<FUNCTION_APP_NAME>.azurewebsites.net/api/calculate' \
  -H 'Content-Type: application/json' \
  -H 'x-functions-key: <FUNCTION_KEY>' \
  -d '{"operation":"add","a":10,"b":5}'
```

**Expected:** HTTP **200**, `result: 15`, `status: stored`. The same Function App now receives requests through its Private Endpoint; it has **not** been recreated. Peering, private DNS, and the default NSG rules permit this connection.

**Checkpoint 3:** Show both peerings in **Connected** state, private DNS resolving to `10.10.2.x`, and a successful API call from Ubuntu. Verify that the request still fails from the local computer after public network access has been disabled.

**If the request fails:**

- **Public IP in DNS response:** inspect `privatelink.azurewebsites.net`, its A record, and its link to VNet-Client.
- **Private IP but TCP timeout:** verify peering, actual Private Endpoint IP, any unexpected custom NSG rules, and routes.
- **HTTP 401/403:** verify Function key and Function App access settings; this is different from a routing failure.
- **HTTP 5xx:** investigate Function App logs, Storage connectivity, and RBAC.

**Knowledge check:** What is the difference between a route, a DNS record, an NSG rule, and an application authorization check?

---

## 5. Secure Table Storage: public → blocked → private (40–50 min)

**Learning sequence:** first query the data account from both laptop and Ubuntu **while public access is enabled**, then disable public access **before** creating the Storage Private Endpoint, observe the failures, configure the Private Endpoint/DNS, and finally retest. Do not skip the failed-access checkpoint.

### 5.1 Confirm the Managed Identity model

You have already configured a **system-assigned Managed Identity** on the Function App and assigned it **Storage Table Data Contributor**. This allows **writing** Table entities, subject to network connectivity. Now you will create a separate system-assigned identity for the VM with **read-only** access.

**Concept:** Managed Identity handles token acquisition; **Azure RBAC** grants actions; **Private Endpoint + DNS + NSG + routing** provide the network path. All are required independently.

### 5.2 Enable the Ubuntu VM's identity and grant data-plane read access

1. Azure Portal → **Virtual machines → vm-client-ab12 → Identity → System assigned**.
2. Set **Status: On → Save**.
3. Azure Portal → **data Storage Account → Access control (IAM) → + Add → Add role assignment**.
4. Role: **Storage Table Data Reader**.
5. **Members → Managed identity → + Select members → Virtual machine → vm-client-ab12**.
6. **Review + assign**; wait a few minutes for role propagation.

**Question:** Why should the VM have **Reader** while the Function App has **Contributor**? What is least privilege?

### 5.3 Authenticate as the VM identity and test public Storage access

Earlier, `az login --use-device-code` authenticated **your personal user account** to publish the Function App. Enabling the VM's managed identity and granting RBAC does **not** switch the Azure CLI session automatically.

On Ubuntu:

```bash
az account show --query user -o json
az logout
az login --identity
az account show --query user -o json
az storage entity query \
  --account-name '<DATA_STORAGE_ACCOUNT>' \
  --table-name Calculations \
  --auth-mode login \
  --num-results 10 \
  --output table
```

**Expected:** rows returned. If you see `You do not have the required permissions`, verify that you used `az login --identity` (not the earlier user login), and check the VM's **Storage Table Data Reader** assignment. This exact error occurred during the live lab and was fixed by switching identity.

**Now test from your laptop:** sign in with a user account that has **Storage Table Data Reader** on the data Storage Account, and run the same `az storage entity query` command using `--auth-mode login`. If you don't have that role, ask the instructor to grant it temporarily. This distinguishes authorization from public network access. Record that both machines can query while public access is enabled.

### 5.4 Disable Storage public access FIRST and retest

1. Azure Portal → **Storage accounts → <DATA_STORAGE_ACCOUNT> → Networking → Public network access → Disabled → Save**.
2. **Do not** change the separate Functions runtime Storage Account.
3. From **your laptop**, repeat the same Azure CLI Table query. Expect a network-access denial.
4. From **Ubuntu**, repeat the query with the managed identity. Expect a network-access denial because there is not yet a Table Private Endpoint.
5. Record both failures. The identities and roles have not changed: **network access** is now the missing layer.

> A Storage network restriction commonly produces an HTTP 403, but the CLI may show a generic permissions message. Compare the account's network settings, DNS resolution and authenticated identity rather than relying on the wording alone.

### 5.5 Create a Private Endpoint for the `table` service

1. Azure Portal → **data Storage Account → Networking → Private endpoint connections → + Private endpoint**.
2. Name `pe-table-ab12`; select the **existing data Storage Account**.
3. Target subresource **`table`** (not `blob`, `file`, or `queue`).
4. Virtual network `vnet-app-ab12`; subnet `snet-private-endpoints`.
5. DNS integration: **Yes**, private DNS zone **`privatelink.table.core.windows.net`**.
6. **Review + create → Create**; verify **Approved** and record the Table Private Endpoint IP.
7. Azure Portal → **Private DNS zones → privatelink.table.core.windows.net → Virtual network links → + Add**; link **vnet-client-ab12** with auto registration disabled. Confirm VNet-App is also linked.
8. No custom HTTPS NSG rule is needed in this lab. Default NSG rules allow communication between the peered VNets unless other restrictions have been introduced.

### 5.6 Verify Private DNS and retry Ubuntu access

From Ubuntu:

```bash
nslookup <DATA_STORAGE_ACCOUNT>.table.core.windows.net
getent ahostsv4 <DATA_STORAGE_ACCOUNT>.table.core.windows.net
```

**Expected:** Private IP `10.10.2.x` for the Table endpoint. The Function App's VNet-App must also resolve the Table endpoint privately after the Storage lockdown.

### 5.7 Final end-to-end Function and Storage validation

1. From Ubuntu, confirm `nslookup <DATA_STORAGE_ACCOUNT>.table.core.windows.net` returns the Table Private Endpoint IP (not a public IP). **Do not include `https://` in `nslookup`; it accepts hostnames, not URLs.**
2. Run `az storage entity query --account-name '<DATA_STORAGE_ACCOUNT>' --table-name Calculations --auth-mode login --num-results 10 -o table`. Expect rows returned via the private endpoint.
3. From your laptop, repeat the query. Expect blocked public access (unless the laptop is connected to an authorized private network).
4. From Ubuntu, invoke the **same existing Function App** again with a new calculation (for example `12 × 22 = 264`) and expect HTTP 200 with `status: stored` and a new `rowKey`.
5. From Ubuntu, query Table Storage again and locate the new entity by `RowKey`/result. This confirms the Function App can **write** to Storage after lockdown and Ubuntu can **read** it privately.

If the Function returns 502 after Storage lockdown, verify **Function App → Networking → Virtual network integration** is configured to VNet-App, that VNet-App is linked to `privatelink.table.core.windows.net`, and that the Function's managed identity still has **Storage Table Data Contributor**. The Function's **inbound** Private Endpoint does not replace its **outbound** VNet Integration.

**Checkpoint 4:** show public queries working before lockdown, both queries failing after lockdown, Ubuntu querying privately after Table Private Endpoint/DNS, and a newly inserted row after the final Function execution.

---

## 6. Final validation and cleanup (20 min)

### Expected results

| Test | Expected result | What it demonstrates |
|---|---|---|
| Laptop → Function, before lockdown | HTTP 200 | Public HTTPS access |
| Laptop → Function, after lockdown | Blocked | Public network access restriction |
| Ubuntu → Function, before peering | Fails | No private network path |
| Ubuntu → Function, peering + private DNS | HTTP 200 | End-to-end private HTTPS using default NSG rules |
| Laptop and Ubuntu → Table, before lockdown | Entities returned with valid RBAC | Public data-plane access |
| Laptop and Ubuntu → Table, public disabled before PE | Blocked | Network restrictions independent of RBAC |
| Ubuntu → Table, after PE + DNS + RBAC | Entities returned | Private data access + authorization |
| Ubuntu → Function after Storage lockdown; Ubuntu → Table | New result stored and visible | End-to-end private application/data path |
| Laptop → Table, public disabled | Blocked | Storage public network restriction |

### Final knowledge questions

1. Why is VNet Integration **not** sufficient for private inbound Function access?
2. What does peering provide, and what does it **not** provide?
3. Why does allowing inbound SSH to the VM not prevent its outbound HTTPS requests?
4. Why do default NSG rules usually permit traffic between peered VNets?
5. Why must the Function App hostname resolve to the Private Endpoint IP?
6. Why must a Table Storage Private Endpoint target the **`table`** subresource?
7. What is the difference between Managed Identity, Azure RBAC and network access?
8. Why can a valid identity still fail to access Storage when public access is disabled?
9. How do HTTP 403, TCP timeout and DNS resolution failures differ?
10. Why should application data and runtime Storage be separated in this teaching exercise?

### Cleanup — Azure Portal only

1. Azure Portal → **Resource groups → rg-az-securelab-ab12**.
2. Review all resources and ensure no unrelated resources are in the group.
3. Select **Delete resource group**, type the exact name, confirm.
4. Wait for deletion to complete; verify no VM, Public IP, Private Endpoint or other billable resource remains.

---

## Optional troubleshooting quick reference

| Symptom | Likely layer | Check |
|---|---|---|
| DNS resolves public IP | DNS | Private DNS zone A record and VNet link |
| DNS private, TCP timeout | Network | Peering, effective NSG rules, routing, Private Endpoint IP/443 |
| HTTP 401/403 | HTTP/security | Function key, public access settings, app restrictions |
| HTTP 400 | Application | Request JSON, operation, divide-by-zero |
| HTTP 502 from API | Application → Storage | Function identity/RBAC, Table endpoint, DNS, outbound integration |
| Storage 403 | Storage auth or firewall | Role scope, token, public access, private endpoint |
| `az login --identity` fails | Identity | VM system-assigned identity enabled; IMDS availability |

**Official documentation:**

- Azure Functions networking: https://learn.microsoft.com/en-us/azure/azure-functions/functions-networking-options
- Azure Private Endpoint network policies: https://learn.microsoft.com/en-us/azure/private-link/disable-private-endpoint-network-policy
- Azure Functions Core Tools: https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local
- Azure CLI installation: https://learn.microsoft.com/en-us/cli/azure/install-azure-cli
- Table Storage authorization with Microsoft Entra ID: https://learn.microsoft.com/en-us/azure/storage/tables/authorize-access-azure-active-directory

**Trainer validation note:** This guide reflects a live walkthrough. Exact Portal labels and supported hosting features can vary by subscription, region, and release.
