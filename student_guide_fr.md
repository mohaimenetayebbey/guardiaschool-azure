# TP étudiant — Déployer, connecter et sécuriser une application serverless Azure

**Certifications abordées :** AZ-900, AZ-104 et AZ-500  
**Durée indicative :** 3 à 4 heures en séance guidée  
**Méthode :** **Azure Portal** pour la création et la configuration des ressources ; **Ubuntu VM** pour le développement, le déploiement et les tests CLI.  
**Objectif pédagogique :** apprendre les concepts Azure par la pratique, en provoquant puis en résolvant des problèmes de connectivité et d'accès.

> **Consigne importante :** aucun script de déploiement d'infrastructure n'est utilisé. Créez toutes les ressources, les règles réseau et les paramètres de sécurité depuis **https://portal.azure.com**. Les commandes servent uniquement à installer les outils, créer et publier le code, se connecter en SSH, tester les services et consulter les données.

## 0. Scénario, architecture et prérequis (15 min)

Votre entreprise dispose d'une API de calcul. Les clients transmettent deux nombres et une opération (`add`, `subtract`, `multiply`, `divide`). L'API calcule le résultat et l'enregistre dans **Azure Table Storage**. Initialement, l'API est accessible depuis Internet. Votre mission consiste à rendre cette API privée, puis à y accéder depuis une VM d'administration Ubuntu située dans **un autre Virtual Network**. Vous mettrez en place le **VNet peering**, les **Private Endpoints** et le **Private DNS**, avant de consulter les résultats via **Azure CLI**.

### Architecture cible

```text
PC local / Postman (test public initial)
   | HTTPS 443 (avant restriction)
   v
Azure Function App (la MÊME application durant tout le TP)
   | accès entrant public au départ, puis Private Endpoint
   | VNet Integration sortante -> snet-func-integration
   |
VNet-App 10.10.0.0/16
   snet-func-integration 10.10.1.0/27 (délégué, trafic sortant)
   snet-private-endpoints  10.10.2.0/24
      - Private Endpoint Function : sites
      - Private Endpoint Table Storage : table (plus tard)
   |
   | VNet peering (créé plus tard ; insuffisant à lui seul)
   |
VNet-Client 10.20.0.0/16
   snet-vm 10.20.1.0/24
      - VM Ubuntu avec IP publique pour SSH depuis votre IP

Storage Account de données -> Table « Calculations »
Storage Account runtime -> utilisé en interne par Azure Functions
```

### Nommage des ressources

Choisissez un suffixe unique, par exemple `ab12`, et réutilisez-le. Remplacez les noms ci-dessous par les vôtres.

| Ressource | Nom proposé |
|---|---|
| Resource Group | `rg-az-securelab-ab12` |
| VNet-App | `vnet-app-ab12` |
| Subnet d'intégration Function | `snet-func-integration` |
| Subnet des Private Endpoints | `snet-private-endpoints` |
| VNet-Client | `vnet-client-ab12` |
| Subnet VM | `snet-vm` |
| NSG du subnet VM | `nsg-vm-ab12` |
| Function App | `func-calc-ab12` (nom globalement unique) |
| Storage Account runtime | `stfuncrt<unique>` (nom globalement unique) |
| Storage Account de données | `stcalc<unique>` (nom globalement unique) |
| Ubuntu VM | `vm-client-ab12` |
| Private Endpoint Function | `pe-func-ab12` |
| Private Endpoint Table | `pe-table-ab12` |

**Prérequis :** abonnement Azure actif ; autorisations de créer des ressources **et d'attribuer des rôles** (par exemple `Owner`, ou permissions RBAC équivalentes incluant la gestion des rôles) ; adresse IPv4 publique du poste ; SSH et éventuellement PowerShell/Postman ; outils Python 3.12, Azure CLI et Functions Core Tools v4 installés ensuite sur Ubuntu. Le plan d'hébergement Function et la taille de VM doivent être disponibles dans votre région et votre abonnement.

**Coûts :** VM, IP publique, Private Endpoints, Storage et Function App peuvent être facturés. Supprimez les ressources à la fin du TP.

### Questions de départ

- Quels services relèvent de **IaaS**, **PaaS** ou **serverless** ? Qui maintient l'OS Ubuntu ? Qui gère le runtime Azure Functions ?
- Quelles différences entre **VNet**, **subnet**, **NSG**, **Private Endpoint** et **VNet peering** ?
- Autoriser uniquement le port SSH 22 **en inbound** bloque-t-il le HTTPS 443 **en outbound** ? **Non** : les NSG Azure possèdent des règles outbound autorisant le trafic par défaut. Nous les examinerons sans ajouter de règle HTTPS personnalisée.

---

## 1. Créer les ressources applicatives Azure (40 min)

### 1.1 Créer le Resource Group

1. **Azure Portal → Resource groups → + Create**.
2. **Subscription** : sélectionnez l'abonnement du TP.
3. **Resource group** : `rg-az-securelab-ab12`.
4. **Region** : choisissez une région prenant en charge **Flex Consumption**, **Private Endpoints** et la taille de VM retenue ; conservez la même région pour les ressources.
5. **Review + create → Create**.

**Concept — Resource Group :** conteneur logique de gestion des ressources Azure. Ce n'est pas, à lui seul, une frontière d'isolation réseau ou de sécurité.

### 1.2 Créer deux Storage Accounts

1. **Azure Portal → Storage accounts → + Create**.
2. Choisissez votre Resource Group, la région, la performance **Standard** et la redondance **LRS** pour ce TP.
3. Créez `stfuncrt<unique>` : Storage Account du **runtime** Azure Functions. Conservez son accès réseau public pendant le TP ; ce n'est pas le compte que nous sécuriserons dans l'exercice Table Storage.
4. **Review + create → Create**.
5. Recommencez avec `stcalc<unique>` : Storage Account des **données applicatives**. Sélectionnez initialement **Public network access: Enabled from all networks**.
6. Ouvrez **Storage Account de données → Data storage → Tables → + Table** ; créez `Calculations` (respectez la casse).
7. Dans **Storage Account de données → Networking**, vérifiez que l'accès public est encore autorisé.

**Concept — Storage :** un Storage Account peut proposer plusieurs services (`blob`, `queue`, `table`, `file`), chacun ayant son endpoint. Un Private Endpoint `blob` ne donne pas accès au service `table`.

### 1.3 Créer VNet-App et ses subnets

1. **Azure Portal → Virtual networks → + Create**.
2. **Name** : `vnet-app-ab12` ; Resource Group du TP ; région identique.
3. Onglet **IP addresses** → Address space `10.10.0.0/16`.
4. Ajoutez `snet-func-integration` : `10.10.1.0/27`. Pour **Subnet delegation**, sélectionnez **Microsoft.App/environments** si cette délégation est proposée/requise par **Flex Consumption**. **Ne placez pas les Private Endpoints dans ce subnet délégué.**
5. Ajoutez `snet-private-endpoints` : `10.10.2.0/24`, sans délégation.
6. Créez le VNet.

> Si l'assistant Flex Consumption propose de créer ou de déléguer automatiquement le subnet d'intégration, vous pouvez utiliser cette option, puis vérifier sa présence dans `vnet-app-ab12`. Les écrans du Portal évoluent.

### 1.4 Créer la Function App (une seule fois)

1. **Azure Portal → Function App → + Create**.
2. Choisissez **Flex Consumption** (ou un autre plan validé par le formateur qui prend en charge **VNet Integration** et **Private Endpoint**).
3. **Basics** : Subscription, Resource Group, nom unique, **Runtime stack: Python**, **Python version: 3.12** (version utilisée et validée pendant le TP), Region.
4. **Storage** : sélectionnez `stfuncrt<unique>` comme Storage Account du runtime ; ne sélectionnez pas le compte de données applicatives.
5. **Networking** : laissez l'accès public entrant activé. Configurez la **VNet Integration sortante** vers `vnet-app-ab12` / `snet-func-integration` si l'assistant le propose. Sinon, faites-le après la création.
6. **Review + create → Create**.
7. **Function App → Networking → Virtual network integration** : vérifiez l'association à VNet-App et à son subnet d'intégration ; sinon, utilisez **Add VNet / Configure**.
8. **Function App → Networking → Public network access** : vérifiez **Enabled**.

**Concept essentiel :** **VNet Integration** concerne principalement le trafic **sortant** de la Function App. Elle ne rend pas l'API privée en entrée. Nous utiliserons ensuite un **Private Endpoint** pour les appels HTTP entrants.

### 1.5 Comprendre les Managed Identities AVANT leur configuration

Une **Managed Identity** est une identité Microsoft Entra ID associée à une ressource Azure. Azure gère les identifiants et leur rotation : l'application obtient des tokens sans stocker de mot de passe ni de clé Storage.

- **System-assigned Managed Identity** : liée à une seule ressource, supprimée avec elle.
- **User-assigned Managed Identity** : ressource Azure indépendante, réutilisable par plusieurs services.

Créer une identité **n'accorde aucune permission automatiquement**. Il faut attribuer un rôle **Azure RBAC** adapté au bon périmètre (**scope**). Distinguez **authentication** (qui êtes-vous ?), **authorization** (que pouvez-vous faire ?) et **network connectivity** (pouvez-vous joindre le service ?).

**Questions :** une Function avec Managed Identity, mais sans rôle Storage data-plane, peut-elle écrire une entité ? Le rôle générique `Contributor` sur un Resource Group suffit-il à accéder aux données Table ?

### 1.6 Activer l'identité de la Function et attribuer le rôle Table

1. **Function App → Identity → System assigned**.
2. **Status: On → Save**. Vérifiez l'apparition de l'**Object (principal) ID**.
3. **Storage Account de données → Access control (IAM) → + Add → Add role assignment**.
4. Sélectionnez **Storage Table Data Contributor** (et non le rôle générique `Contributor`).
5. **Members → Assign access to: Managed identity → + Select members**.
6. Type **Function App** ; choisissez votre Function App → **Review + assign**.
7. **Function App → Settings → Environment variables → App settings → + Add** (ou **Configuration → Application settings**, selon l'interface).
8. **Name** : `DATA_STORAGE_ACCOUNT` ; **Value** : le **nom seul** du Storage Account de données, **sans `https://` et sans nom de domaine**. Appliquez et enregistrez ; acceptez le redémarrage si demandé.
9. Attendez quelques minutes pour la propagation des permissions RBAC.

---

## 2. Créer VNet-Client et la VM Ubuntu (30 min)

### 2.1 Créer le VNet client

1. **Azure Portal → Virtual networks → + Create**.
2. **Name** : `vnet-client-ab12` ; Address space `10.20.0.0/16`.
3. **IP addresses → Add subnet** : `snet-vm`, `10.20.1.0/24`.
4. **Review + create → Create**. **Ne créez pas encore le peering.**

### 2.2 Créer le NSG du subnet VM

1. **Azure Portal → Network security groups → + Create** ; nom `nsg-vm-ab12`.
2. **nsg-vm-ab12 → Inbound security rules → + Add**.
3. **Source: IP Addresses** → votre IPv4 publique actuelle en `/32` ; **Destination: Any** ; **Service: SSH** ou TCP/22 ; **Action: Allow** ; **Priority: 100** ; **Name: Allow-SSH-MyIP**.
4. Vérifiez qu'aucune règle inbound personnalisée n'ouvre les ports 80/443 vers la VM.
5. **Virtual networks → vnet-client-ab12 → Subnets → snet-vm → Network security group** → sélectionnez `nsg-vm-ab12` → **Save**.
6. Consultez **nsg-vm-ab12 → Outbound security rules** : repérez `AllowInternetOutBound` et `AllowVnetOutBound`. **N'ajoutez aucune règle HTTPS outbound personnalisée.**

**Concept — NSG :** les règles sont évaluées par ordre de priorité (nombre le plus faible d'abord), séparément en **inbound** et **outbound**. Les NSG sont **stateful**. Autoriser uniquement SSH en entrée ne bloque pas HTTPS en sortie.

### 2.3 Déployer Ubuntu

1. **Azure Portal → Virtual machines → + Create → Azure virtual machine**.
2. Resource Group du TP ; **VM name** : `vm-client-ab12` ; **Image: Ubuntu Server 24.04 LTS** ; **Size** : petit SKU validé par le formateur (par exemple B1s si disponible).
3. **Authentication: SSH public key** ; **Username: azureuser**. Générez/téléchargez une paire de clés ou utilisez votre clé publique existante. **Ne partagez jamais la clé privée.**
4. Onglet **Networking** : **Virtual network** `vnet-client-ab12` ; **Subnet** `snet-vm` ; **Public IP: Enabled** pour SSH ; **NIC network security group: None**, car le NSG est déjà associé au subnet. Si un NSG NIC est imposé, vérifiez qu'il ne contredit pas les règles du TP.
5. **Review + create → Create**.
6. **VM → Overview → Public IP address** ; depuis votre PC, ouvrez une connexion SSH :

```bash
ssh -i ~/.ssh/<PRIVATE_KEY_FILE> azureuser@<VM_PUBLIC_IP>
```

7. Dans Ubuntu :

```bash
sudo apt-get update
sudo apt-get install -y curl dnsutils jq
ip -4 addr
ip route
```

**Point de contrôle :** SSH fonctionne ; la VM est dans VNet-Client ; aucun peering n'existe encore. La VM pourra toutefois contacter l'API **publique** via Internet. Ce n'est pas contradictoire avec l'absence de peering.

### 2.4 Ubuntu : poste de développement et de test

Nous allons créer les fichiers et publier le code **directement depuis la VM Ubuntu**, et non depuis Windows. Au début, Ubuntu utilise son accès Internet public pour joindre Azure et la Function App. Le peering n'est pas encore configuré.

### 2.5 Installer les outils, créer les fichiers Python et publier

Exécutez les commandes **une par une**. Conservez **Python 3.12**.

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3-pip curl ca-certificates gnupg dnsutils jq git
python3.12 --version
```

**Installer Azure CLI** (méthode utilisée pendant le TP) :

```bash
curl -fsSL 'https://azurecliprod.blob.core.windows.net/$root/deb_install.sh' | sudo bash
az version
```

> Cette commande exécute un script téléchargé avec les droits root. Utilisez-la uniquement dans un environnement de TP approuvé ; en production, privilégiez la procédure officielle avec vérification du dépôt et des packages.

**Installer Node.js 22, npm et Azure Functions Core Tools v4** :

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
node --version
npm --version
sudo npm install -g azure-functions-core-tools@4 --unsafe-perm
func --version
```

**Dépannage validé :** Node.js **18** ne satisfait pas les prérequis de l'installateur npm de Core Tools **4.15.2** (Node >=22). Si `func --version` affiche `Permission denied` et que `/usr/bin/func` pointe vers un `main.js` non exécutable, vérifiez le chemin avant de corriger **uniquement ce problème** :

```bash
readlink -f /usr/bin/func
ls -l "$(readlink -f /usr/bin/func)"
sudo chmod +x "$(readlink -f /usr/bin/func)"
func --version
```

**Se connecter à Azure avec son compte utilisateur pour publier l'application :**

```bash
az login --use-device-code
az account show --output table
```

Si plusieurs abonnements sont disponibles : `az account set --subscription '<SUBSCRIPTION_ID>'`.

**Créer manuellement les fichiers dans Ubuntu** (aucun transfert depuis le PC nécessaire) :

```bash
mkdir -p ~/functionapp
cd ~/functionapp
nano function_app.py
```

Collez **l'intégralité du code ci-dessous** ; enregistrez avec `Ctrl+O`, Entrée, puis quittez avec `Ctrl+X`.

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

Créer `requirements.txt` :

```bash
nano requirements.txt
```

```text
azure-functions
azure-identity
azure-data-tables
```

Créer `host.json` :

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

**Facultatif :** créer un environnement virtuel Python 3.12 pour vérifier le code localement (le déploiement utilise le runtime distant) :

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m py_compile function_app.py
```

**Publier vers la Function App EXISTANTE, sans en créer une nouvelle :**

```bash
func azure functionapp publish '<EXISTING_FUNCTION_APP_NAME>' --python
```

**Azure Portal → Function App → Functions** : vérifiez que `calculate` apparaît. Publiez le code **avant de désactiver l'accès public**. Un déploiement réussi ne garantit pas que la Function peut écrire dans Storage : il faut tester l'API et la table.

> **Erreur réellement rencontrée :** `DATA_STORAGE_ACCOUNT` doit contenir **uniquement le nom du compte** (ex. `stcalcatb`), **pas** `https://stcalcatb.table.core.windows.net`. Le code construit lui-même l'URL complète. Une URL dans cette variable a provoqué HTTP 502 côté Function et `InvalidResourceName` / HTTP 400 côté Storage.

### 2.6 Tester l'API publique depuis le PC ET depuis Ubuntu

1. **Azure Portal → Function App → Functions → calculate → Get function URL**.
2. Récupérez l'URL et sa **Function key** (paramètre `code=` ou header `x-functions-key`). **Traitez cette clé comme un secret ; ne la partagez pas.**
3. Sur Windows PowerShell :

```powershell
$uri = 'https://<FUNCTION_APP_NAME>.azurewebsites.net/api/calculate'
$key = '<FUNCTION_KEY>'
$body = @{ operation = 'multiply'; a = 12; b = 8 } | ConvertTo-Json
Invoke-RestMethod -Uri $uri -Method Post -Headers @{ 'x-functions-key' = $key } -ContentType 'application/json' -Body $body
```

4. Depuis Ubuntu avec `curl` :

```bash
curl -i -X POST 'https://<FUNCTION_APP_NAME>.azurewebsites.net/api/calculate' \
  -H 'Content-Type: application/json' -H 'x-functions-key: <FUNCTION_KEY>' \
  -d '{"operation":"multiply","a":12,"b":8}'
```

5. Répétez le test depuis **Ubuntu** tant que la Function App est publique. Il doit réussir **sans peering**, grâce à l'accès Internet sortant de la VM.
6. Avec **Postman** : méthode **POST** ; URL `https://<FUNCTION_APP_NAME>.azurewebsites.net/api/calculate` ; headers `Content-Type: application/json` et `x-functions-key: <FUNCTION_KEY>` ; **Body → raw → JSON** avec la même charge utile.
7. **Résultat attendu :** HTTP **200**, `result: 96`, `status: stored` et un `rowKey`.
8. **Storage Account de données → Storage browser → Tables → Calculations** : retrouvez la nouvelle entité. Si HTTP 502 apparaît, contrôlez la Managed Identity, le rôle RBAC, `DATA_STORAGE_ACCOUNT`, le nom de la table et les logs.

**Point de contrôle 1 :** démontrer un appel API réussi depuis le PC et depuis Ubuntu, puis retrouver l'entité dans Table Storage.

---

## 3. Rendre privée la Function App existante (30 min)

### 3.1 Créer le Private Endpoint entrant

1. **Function App → Networking → Private endpoint connections → + Add / Create** (ou **Private endpoints → + Create**, selon l'interface).
2. **Name** : `pe-func-ab12`.
3. **Resource** : votre Function App **existante** ; **Target subresource: `sites`**.
4. **Virtual network** : `vnet-app-ab12` ; **Subnet** : `snet-private-endpoints`.
5. **DNS integration** : **Yes** ; zone **`privatelink.azurewebsites.net`** ; créez-la si nécessaire.
6. Validez ; attendez le statut **Approved**.
7. **Private Endpoint → Overview → Network interface** : relevez l'IP privée, par exemple `10.10.2.x`.
8. **Private DNS zones → privatelink.azurewebsites.net → Virtual network links** : vérifiez le lien vers VNet-App et la présence de l'enregistrement **A**.

### 3.2 Désactiver l'accès public entrant

1. **Function App → Networking → Public network access** (ou **Inbound traffic configuration → Public network access**).
2. Sélectionnez **Disabled → Save**.
3. Depuis votre **PC**, répétez le POST : **échec attendu** (souvent HTTP 403, selon le contexte).
4. Depuis **Ubuntu**, lancez :

```bash
nslookup <FUNCTION_APP_NAME>.azurewebsites.net
curl -v --connect-timeout 5 https://<FUNCTION_APP_NAME>.azurewebsites.net/api/calculate
```

**Résultat attendu :** Ubuntu n'a pas encore de chemin privé vers la Function. Le nom peut toujours résoudre vers une IP publique, car VNet-Client n'est pas encore lié à la zone Private DNS. Le `GET` utilisé ici sert à observer la connexion ; le test applicatif complet utilisera `POST`.

**Questions :** VNet Integration suffit-elle pour l'accès HTTP entrant ? Quelle IP est retournée par DNS ? Pourquoi un Private Endpoint n'est-il pas équivalent à un VNet peering ?

---

## 4. Connecter les VNets avec VNet peering et Private DNS (20 min)

### 4.1 Comprendre VNet peering

Le **VNet peering** permet le routage IP privé entre deux VNets Azure dont les espaces d'adressage ne se chevauchent pas. Il ne crée ni Private Endpoint ni configuration DNS et ne contourne pas les NSG.

Dans notre TP, les **règles NSG par défaut** autorisent normalement le trafic entre VNets peered. **Aucune règle HTTPS inbound/outbound supplémentaire n'est nécessaire.**

Avant de poursuivre, vérifiez que le Private Endpoint de la Function est **Approved** et relevez son IP.

### 4.2 Créer le VNet peering depuis le Portal

1. **Virtual networks → vnet-client-ab12 → Peerings → + Add**.
2. **This virtual network peering link name** : `client-to-app`.
3. **Remote virtual network** : `vnet-app-ab12`.
4. Si demandé, **Remote virtual network peering link name** : `app-to-client`.
5. Conservez **Allow virtual network access** activé dans les deux sens. Ni **Gateway transit** ni **Forwarded traffic** ne sont nécessaires.
6. **Add**. Vérifiez l'état **Connected** des deux côtés, dans **Virtual networks → Peerings**.
7. **Network security groups → nsg-vm-ab12 → Outbound security rules** : repérez **AllowVnetOutBound**, priorité **65000**. **N'ajoutez pas de règle HTTPS personnalisée.**

**Question :** pourquoi la VM peut-elle communiquer avec VNet-App alors que nous avons uniquement ouvert SSH en inbound ?

### 4.3 Associer la zone Private DNS au VNet client

Le peering fournit le **chemin réseau**, mais Ubuntu doit aussi résoudre le nom de la Function App vers l'**IP du Private Endpoint**, et non vers une IP publique.

1. **Private DNS zones → privatelink.azurewebsites.net**.
2. **Virtual network links → + Add**.
3. **Link name** : `link-client-function`.
4. **Virtual network** : `vnet-client-ab12`.
5. **Enable auto registration: No → Create / OK**.
6. **Private DNS zones → privatelink.azurewebsites.net → Recordsets** : vérifiez l'enregistrement **A** de la Function App pointant vers `10.10.2.x`.
7. Depuis Ubuntu :

```bash
nslookup <FUNCTION_APP_NAME>.azurewebsites.net
getent ahostsv4 <FUNCTION_APP_NAME>.azurewebsites.net
```

**Résultat attendu :** le nom public de la Function App passe par un **CNAME** vers `*.privatelink.azurewebsites.net`, puis se résout vers l'IP privée `10.10.2.x`.

> **Erreur rencontrée pendant le TP :** `nslookup` et `getent` prennent un **hostname**, pas une URL. Utilisez `nslookup monapp.azurewebsites.net`, **sans `https://`**. En revanche, `curl` utilise une URL avec `https://`.

### 4.4 Appeler la même Function App en privé

Depuis Ubuntu :

```bash
curl -v --connect-timeout 5 -X POST \
  'https://<FUNCTION_APP_NAME>.azurewebsites.net/api/calculate' \
  -H 'Content-Type: application/json' \
  -H 'x-functions-key: <FUNCTION_KEY>' \
  -d '{"operation":"add","a":10,"b":5}'
```

**Résultat attendu :** HTTP **200**, `result: 15`, `status: stored`. C'est **la même Function App** ; seul son chemin d'accès a changé. Peering, Private DNS et règles NSG par défaut rendent la communication possible.

**Point de contrôle 2 :** les deux peerings sont **Connected** ; le DNS renvoie `10.10.2.x` ; le POST Ubuntu réussit ; le POST depuis le PC, hors réseau privé, reste bloqué.

**Si le test échoue :**

- **IP publique dans la réponse DNS :** vérifiez la zone `privatelink.azurewebsites.net`, son enregistrement A et son lien avec VNet-Client.
- **IP privée, mais timeout TCP :** vérifiez peering, IP du Private Endpoint, routes et éventuelles règles NSG personnalisées.
- **HTTP 401/403 :** contrôlez la Function key et les restrictions d'accès ; ce n'est pas nécessairement un problème de routage.
- **HTTP 5xx :** examinez les logs, l'accès Storage et les rôles RBAC.

**Question :** quelles différences entre une route, un enregistrement DNS, une règle NSG et une vérification d'autorisation applicative ?

---

## 5. Sécuriser Table Storage : public → bloqué → privé (40–50 min)

**Progression volontaire :** lire d'abord les données depuis **le PC et Ubuntu** avec accès public activé ; désactiver ensuite cet accès **avant** de créer le Private Endpoint ; constater les échecs ; configurer Private Endpoint et DNS ; refaire les tests. **Ne sautez pas les tests d'échec.**

### 5.1 Rappel : Managed Identity, RBAC et réseau

La Function App possède déjà une **System-assigned Managed Identity** avec le rôle **Storage Table Data Contributor**, qui lui permet d'écrire des entités, sous réserve de connectivité réseau. Nous allons maintenant créer une identité **distincte** pour la VM, avec un accès **lecture seule**.

- **Managed Identity** : obtention d'un token d'authentification.
- **Azure RBAC** : autorisation de lire ou écrire les données.
- **Private Endpoint + DNS + routage + NSG** : chemin réseau vers le service.

Ces contrôles sont **indépendants et complémentaires**.

### 5.2 Activer l'identité de la VM et attribuer le rôle data-plane

1. **Virtual machines → vm-client-ab12 → Identity → System assigned**.
2. **Status: On → Save**.
3. **Storage Account de données → Access control (IAM) → + Add → Add role assignment**.
4. **Role: Storage Table Data Reader**.
5. **Members → Managed identity → + Select members → Virtual machine → vm-client-ab12**.
6. **Review + assign** ; attendez la propagation RBAC.

**Question :** pourquoi le rôle **Reader** pour la VM et **Contributor** pour la Function ? Expliquez le principe de **least privilege**.

### 5.3 Se connecter AVEC l'identité de la VM et tester Storage public

Vous avez utilisé précédemment `az login --use-device-code` pour vous connecter à Azure avec **votre compte utilisateur** et publier la Function. **Activer la Managed Identity sur la VM ne change pas automatiquement la session Azure CLI !**

Dans Ubuntu :

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

**Résultat attendu :** les entités sont affichées. Si vous obtenez `You do not have the required permissions`, vérifiez **d'abord** `az login --identity` et l'attribution du rôle **Storage Table Data Reader** à la VM. Cette erreur a été rencontrée pendant le TP et corrigée en changeant d'identité Azure CLI.

**Tester ensuite depuis le PC :** connectez Azure CLI avec un **compte utilisateur** disposant également du rôle **Storage Table Data Reader** sur le compte de données. Exécutez la même commande `az storage entity query --auth-mode login`. Demandez temporairement ce rôle au formateur si nécessaire. Notez que **les deux machines peuvent lire les données** tant que l'accès réseau public est autorisé et que les identités ont les permissions nécessaires.

### 5.4 Désactiver l'accès public Storage AVANT le Private Endpoint

1. **Storage accounts → <DATA_STORAGE_ACCOUNT> → Networking → Public network access → Disabled → Save**.
2. **Ne modifiez pas** le Storage Account séparé utilisé par le runtime Azure Functions.
3. Depuis le **PC**, relancez la commande de lecture Table : **échec attendu**.
4. Depuis **Ubuntu**, relancez la commande avec la Managed Identity : **échec attendu**, car aucun Private Endpoint `table` n'existe encore.
5. Notez les deux échecs. Les identités et rôles RBAC n'ont pas changé : c'est maintenant **le réseau** qui bloque l'accès.

> Un blocage réseau Storage renvoie souvent HTTP 403, mais Azure CLI peut afficher un message générique de permissions. Analysez ensemble le réseau, le DNS et l'identité active ; ne concluez pas sur la seule formulation de l'erreur.

### 5.5 Créer le Private Endpoint du service `table`

1. **Storage Account de données → Networking → Private endpoint connections → + Private endpoint**.
2. **Name** : `pe-table-ab12` ; cible : le **Storage Account de données existant**.
3. **Target subresource: `table`** (pas `blob`, `file` ou `queue`).
4. **Virtual network** : `vnet-app-ab12` ; **Subnet** : `snet-private-endpoints`.
5. **DNS integration: Yes** ; zone **`privatelink.table.core.windows.net`**.
6. **Review + create → Create** ; vérifiez **Approved** et relevez l'IP privée du Private Endpoint Table.
7. **Private DNS zones → privatelink.table.core.windows.net → Virtual network links → + Add** : associez **vnet-client-ab12**, avec **auto registration disabled**. Vérifiez également le lien avec VNet-App.
8. N'ajoutez aucune règle HTTPS NSG personnalisée : les règles par défaut autorisent normalement les échanges entre les VNets peered, en l'absence d'autres restrictions.

### 5.6 Vérifier Private DNS et relire les données depuis Ubuntu

```bash
nslookup <DATA_STORAGE_ACCOUNT>.table.core.windows.net
getent ahostsv4 <DATA_STORAGE_ACCOUNT>.table.core.windows.net
```

**Résultat attendu :** une IP privée du subnet des Private Endpoints, par exemple `10.10.2.x`. La Function App doit elle aussi pouvoir résoudre et joindre l'endpoint Table en privé via sa **VNet Integration sortante**.

Relancez :

```bash
az storage entity query \
  --account-name '<DATA_STORAGE_ACCOUNT>' \
  --table-name Calculations \
  --auth-mode login \
  --num-results 10 \
  --output table
```

**Résultat attendu :** les entités réapparaissent, cette fois via le Private Endpoint. Depuis le PC non connecté au réseau privé, la lecture doit toujours échouer.

### 5.7 Validation finale de bout en bout

1. Depuis Ubuntu, vérifiez que `nslookup <DATA_STORAGE_ACCOUNT>.table.core.windows.net` renvoie **l'IP privée** (sans `https://` dans la commande).
2. Vérifiez que `az storage entity query` retourne les lignes via la **Managed Identity de la VM**.
3. Depuis le PC, retentez la même lecture : l'accès public doit rester bloqué, sauf si le PC utilise déjà un accès réseau privé autorisé.
4. Depuis Ubuntu, appelez **la même Function App existante** avec une nouvelle opération, par exemple `12 × 22 = 264` :

```bash
curl -i -X POST 'https://<FUNCTION_APP_NAME>.azurewebsites.net/api/calculate' \
  -H 'Content-Type: application/json' \
  -H 'x-functions-key: <FUNCTION_KEY>' \
  -d '{"operation":"multiply","a":12,"b":22}'
```

5. Vérifiez HTTP **200**, `result: 264`, `status: stored` et un nouveau `rowKey`.
6. Relancez `az storage entity query` depuis Ubuntu et retrouvez la **nouvelle entité**. Cela prouve que la Function peut **écrire** après le verrouillage du Storage et que la VM peut **lire** en privé.

Si la Function retourne HTTP 502 après la désactivation de l'accès public Storage, vérifiez **Function App → Networking → Virtual network integration**, le lien VNet-App vers `privatelink.table.core.windows.net` et le rôle **Storage Table Data Contributor** de son identité. Le Private Endpoint **entrant** de la Function ne remplace pas sa VNet Integration **sortante**.

**Point de contrôle 3 :** montrer la lecture publique avant restriction, les deux échecs après restriction, la lecture privée Ubuntu après Private Endpoint/DNS, puis l'insertion d'une nouvelle ligne par la Function App.

---

## 6. Bilan, questions et nettoyage (20 min)

### Résultats attendus

| Test | Résultat attendu | Concept illustré |
|---|---|---|
| PC → Function avant restriction | HTTP 200 | Endpoint HTTPS public |
| PC → Function après restriction | Bloqué | Public network access |
| Ubuntu → Function avant peering | Échec d'accès privé | Absence de chemin réseau privé |
| Ubuntu → Function après peering + Private DNS | HTTP 200 | HTTPS privé et règles NSG par défaut |
| PC et Ubuntu → Table avant restriction | Entités accessibles avec RBAC valide | Accès data-plane public |
| PC et Ubuntu → Table après restriction, sans PE | Bloqué | Sécurité réseau distincte de RBAC |
| Ubuntu → Table après PE + DNS + RBAC | Entités accessibles | Private Link + autorisation |
| Ubuntu → Function, puis Ubuntu → Table | Nouvelle ligne créée et visible | Chaîne applicative privée complète |
| PC → Table après désactivation du public | Bloqué | Restriction réseau Storage |

### Questions de compréhension

1. Pourquoi **VNet Integration** ne suffit-elle pas à rendre l'accès entrant de la Function privé ?
2. Que permet **VNet peering**, et que ne permet-il **pas** ?
3. Pourquoi ouvrir SSH 22 en inbound ne bloque-t-il pas HTTPS 443 en outbound ?
4. Pourquoi les règles NSG par défaut autorisent-elles normalement les échanges entre VNets peered ?
5. Pourquoi le hostname public de la Function doit-il résoudre vers l'IP du Private Endpoint ?
6. Pourquoi choisir précisément le subresource **`table`** pour le Private Endpoint Storage ?
7. Quelles différences entre **Managed Identity**, **Azure RBAC** et **network access** ?
8. Pourquoi une identité correctement autorisée peut-elle échouer après désactivation de l'accès public Storage ?
9. Comment distinguer HTTP 403, timeout TCP et échec DNS ?
10. Pourquoi avons-nous séparé le Storage Account de données et celui du runtime Azure Functions ?

### Nettoyage — uniquement via Azure Portal

1. **Resource groups → rg-az-securelab-ab12**.
2. Vérifiez que le groupe ne contient **aucune ressource étrangère au TP**.
3. **Delete resource group** ; saisissez le nom exact puis confirmez.
4. Attendez la suppression et vérifiez qu'aucune VM, IP publique, Private Endpoint ou autre ressource facturable ne subsiste.

---

## 7. Aide au dépannage

| Symptôme | Couche probable | Vérifications |
|---|---|---|
| DNS renvoie une IP publique | DNS | Recordset A, zone Private DNS et VNet link |
| DNS privé, mais timeout TCP | Réseau | Peering, règles NSG effectives, routes, IP Private Endpoint / 443 |
| HTTP 401/403 sur la Function | HTTP / sécurité | Function key, accès public, restrictions applicatives |
| HTTP 400 | Application | JSON, opération, division par zéro |
| HTTP 502 sur l'API | Application → Storage | Managed Identity, RBAC, endpoint Table, DNS, VNet Integration |
| Storage HTTP 403 | RBAC ou firewall Storage | Rôle et scope, token, Public network access, Private Endpoint |
| `az login --identity` échoue | Identité | System-assigned identity de la VM, disponibilité IMDS |
| `You do not have the required permissions` | Identité ou RBAC | `az login --identity` puis rôle `Storage Table Data Reader` |
| `InvalidResourceName` dans les logs | Configuration applicative | `DATA_STORAGE_ACCOUNT` doit contenir le **nom seul** du compte |
| `nslookup https://...` → NXDOMAIN | Syntaxe DNS | Retirer `https://` ; utiliser uniquement le hostname |
| Core Tools : Node.js 18 / `ERR_REQUIRE_ESM` | Outils Ubuntu | Installer Node.js 22 avant Core Tools v4 |
| `func: Permission denied` | Outils Ubuntu | Vérifier la cible du lien symbolique et ses permissions |

### Documentation officielle

- [Azure Functions — Networking options](https://learn.microsoft.com/en-us/azure/azure-functions/functions-networking-options)
- [Azure Private Endpoint — Network policies](https://learn.microsoft.com/en-us/azure/private-link/disable-private-endpoint-network-policy)
- [Azure Functions Core Tools](https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local)
- [Installer Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli)
- [Autoriser l'accès à Table Storage avec Microsoft Entra ID](https://learn.microsoft.com/en-us/azure/storage/tables/authorize-access-azure-active-directory)

> **Note pédagogique :** ce document reprend les étapes et les problèmes effectivement rencontrés pendant une exécution du TP. Les intitulés du Portal et les fonctionnalités disponibles peuvent varier selon la région, l'abonnement et les évolutions d'Azure.
