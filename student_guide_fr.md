# TP Azure — Du réseau public au réseau privé avec des VM, Flask, les identités managées et Table Storage

**Certifications :** AZ-900 · AZ-104 · AZ-500  
**Durée estimée :** 3 à 4 heures (prévoir du temps supplémentaire pour le provisionnement Azure et la propagation des rôles RBAC)  
**Environnement :** Portail Azure en priorité ; Ubuntu 24.04 LTS ; Python 3.12 ; Azure CLI ; PowerShell sur Windows  
**Objectif :** Déployer une API Python de calcul, la rendre accessible publiquement, l'isoler, rétablir son accès par peering entre VNets, puis rendre Azure Table Storage privé, sans secrets applicatifs.

> **Abonnement :** Ce TP utilise deux petites VM au lieu d'une Azure Function App, car certains abonnements d'essai ne permettent pas de déployer les plans d'hébergement requis. Les VM, disques, IP publiques et Private Endpoints peuvent être facturés ou consommer des crédits. Vérifiez les quotas et tailles de VM disponibles. Supprimez les ressources à la fin.
>
> **Sécurité :** Le serveur de développement intégré à Flask est réservé à ce TP temporaire, pas à la production. Limitez le port public 5000 aux adresses IP des étudiants concernés et supprimez l'exposition publique pendant l'exercice. HTTP n'est pas chiffré : ne transmettez aucun secret.

## 1. Objectifs pédagogiques

À la fin du TP, vous saurez :

- Expliquer les groupes de ressources, VNets, sous-réseaux, adresses IP publiques/privées, NSG et VNet Peering.
- Déployer des VM Ubuntu et tester la connectivité HTTP et SSH depuis différents réseaux.
- Différencier un problème de routage, un blocage NSG, un problème DNS et une erreur RBAC.
- Utiliser l'**identité managée affectée par le système** d'une VM dans Python pour écrire dans Azure Table Storage **sans clé de compte ni chaîne de connexion**.
- Interroger Table Storage avec Azure CLI depuis le **PC local** et **VM-Client**, avec des identités différentes.
- Désactiver l'accès réseau public au stockage et rétablir l'accès privé via **Private Endpoint** et **Private DNS**.
- Valider le flux de bout en bout et nettoyer les ressources.

## 2. Architecture et conventions de nommage

Utilisez des noms uniques lorsque Azure l'exige. Notez vos valeurs réelles ; les commandes utilisent des paramètres tels que `<STORAGE_ACCOUNT>`.

| Ressource | Nom proposé | Adresse / rôle |
|---|---|---|
| Groupe de ressources | `rg-private-lab` | Même région Azure pour toutes les ressources |
| VNet-App | `vnet-app` | `10.10.0.0/16` |
| Sous-réseau applicatif | `snet-app` | `10.10.1.0/24` |
| Sous-réseau Private Endpoint | `snet-private-endpoints` | `10.10.2.0/24` |
| VNet-Client | `vnet-client` | `10.20.0.0/16` |
| Sous-réseau client | `snet-client` | `10.20.1.0/24` |
| VM applicative | `vm-app` | Ubuntu 24.04, IP privée dans `10.10.1.0/24` |
| VM cliente | `vm-client` | Ubuntu 24.04, IP privée dans `10.20.1.0/24` |
| NSG applicatif | `nsg-app` | SSH 22 et HTTP 5000 temporaire |
| NSG client | `nsg-client` | SSH 22 depuis l'IP publique de l'étudiant |
| Compte de stockage | `st<uniquelettersnumbers>` | Standard GPv2 ; nom globalement unique, 3 à 24 lettres minuscules/chiffres |
| Table | `Calculations` | Données Azure Table Storage |
| Private Endpoint Storage | `pe-storage-table` | Sous-ressource Storage **table** |
| Zone DNS privée | `privatelink.table.core.windows.net` | Liée aux deux VNets |

**Chemin réseau final :**

```text
PC étudiant --SSH--> VM-Client (IP publique)
                          |
                          | curl / SSH via VNet Peering (IP privées)
                          v
                       VM-App ---- API Python Flask :5000
                          |
                          | ManagedIdentityCredential (authentification)
                          | Azure RBAC : Storage Table Data Contributor (autorisation)
                          | HTTPS vers <STORAGE_ACCOUNT>.table.core.windows.net
                          v
                  Private Endpoint Storage (IP privée)
                          |
                          v
                  Azure Table Storage / Calculations

PC étudiant -- Azure CLI --> Point d'accès public Storage : BLOQUÉ à la fin
VM-Client -- Azure CLI / Identité managée --> Private Endpoint Storage : AUTORISÉ
```

**À retenir :** l'identité managée est un mécanisme d'**authentification**, pas un saut réseau. Le Private Endpoint et le DNS privé assurent la connectivité privée. **VM-App n'a pas besoin de Private Endpoint** : son IP privée est directement accessible depuis le VNet appairé.

### Prérequis

- Un abonnement Azure autorisant la création de VM, VNets, NSG, Storage, Private Endpoints, DNS privé et attributions de rôles. Pour attribuer des rôles, il faut `Owner`, `User Access Administrator` ou une permission équivalente ; `Contributor` seul ne suffit pas.
- Une région disposant d'un quota pour deux petites VM Ubuntu. Les VM et le Private Endpoint ne sont pas nécessairement gratuits.
- Un PC Windows avec **PowerShell**, un **client SSH** (`ssh -V`), **Azure CLI** (`az version`) et Internet. Si Azure CLI manque : [guide officiel Microsoft](https://learn.microsoft.com/fr-fr/cli/azure/install-azure-cli-windows).
- Votre adresse IPv4 publique, consultable sur `https://api.ipify.org`. Notez-la sous `<MY_PUBLIC_IP>`.
- Un terminal et le portail `https://portal.azure.com`.
- Notez les **vraies** IP, le nom d'utilisateur des VM et le nom du compte Storage. Remplacez les paramètres entre chevrons : ne saisissez pas littéralement `<` et `>`.

---

## 3. Phase A — Déployer VNet-App et VM-App

### Étape A1 — Créer le groupe de ressources et VNet-App

1. **Portail Azure → Groupes de ressources → Créer**. Nom : `rg-private-lab`. Choisissez votre région, puis **Vérifier + créer → Créer**.
2. **Réseaux virtuels → Créer**. Nom : `vnet-app`, groupe `rg-private-lab`, même région.
3. Dans **Adresses IP**, définissez `10.10.0.0/16`, puis créez `snet-app` (`10.10.1.0/24`) et `snet-private-endpoints` (`10.10.2.0/24`). Supprimez ou adaptez le sous-réseau par défaut si nécessaire.
4. **Vérifier + créer → Créer**.

### Étape A2 — Créer VM-App

1. **Machines virtuelles → Créer → Machine virtuelle Azure**.
2. Groupe `rg-private-lab` ; nom `vm-app` ; image **Ubuntu Server 24.04 LTS** ; plus petite taille autorisée avec suffisamment de mémoire (par exemple série B si disponible).
3. Authentification : **Clé publique SSH** ; utilisateur `azureuser`. Générez/téléchargez une paire de clés ou utilisez une clé publique existante. **Conservez la clé privée en sécurité sur votre PC**.
4. Onglet **Mise en réseau** : VNet `vnet-app`, sous-réseau `snet-app`, **IP publique : créer** ; NSG de carte réseau **De base ou Avancé** (`nsg-app` en mode avancé). Autorisez **SSH (22)** uniquement depuis `<MY_PUBLIC_IP>/32`, et non depuis tout Internet. Si l'assistant crée déjà un NSG, réutilisez-le au lieu d'en multiplier inutilement.
5. Dans **Gestion → Identité** (ou après déploiement : **VM-App → Identité → Affectée par le système → Activé → Enregistrer**), activez l'**identité managée affectée par le système**.
6. Créez la VM. Notez `<APP_PUBLIC_IP>` et `<APP_PRIVATE_IP>` depuis **VM-App → Vue d'ensemble / Mise en réseau**.
7. Vérifiez que la source de la règle SSH entrante est bien votre IPv4 publique `/32`. Si l'assistant a créé `AllowSSH` depuis `Any`, restreignez-la.

### Étape A3 — Se connecter en SSH et installer les prérequis de VM-App

Sur le **PC local** (PowerShell), remplacez le chemin de clé et l'IP :

```powershell
ssh -i "$HOME\.ssh\<YOUR_PRIVATE_KEY>" azureuser@<APP_PUBLIC_IP>
```

Sur **VM-App** :

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

**N'utilisez pas** `sudo pip install` dans le Python système d'Ubuntu. Utilisez l'environnement virtuel.

---

## 4. Phase B — Créer un Storage public et configurer l'identité managée

### Étape B1 — Créer le compte Storage et la table

1. **Comptes de stockage → Créer**. Groupe `rg-private-lab`, même région, nom globalement unique `<STORAGE_ACCOUNT>`, performance **Standard**, type **StorageV2 (usage général v2)**, redondance **LRS**.
2. **Mise en réseau : activer l'accès réseau public depuis tous les réseaux** pour les premiers tests. Conservez **Transfert sécurisé requis**.
3. Créez le compte de stockage.
4. **Compte de stockage → Stockage des données → Tables → + Table**. Nom exact : **`Calculations`** (C majuscule). Créez la table. Sans elle, vous obtiendrez `TableNotFound`.
5. Notez le **nom** du compte, pas son URL complète.

### Étape B2 — Autoriser VM-App à écrire

1. **Compte Storage → Contrôle d'accès (IAM) → Ajouter → Ajouter une attribution de rôle**.
2. Sélectionnez **Storage Table Data Contributor** (et non le rôle générique `Contributor`).
3. **Membres → Attribuer l'accès à : Identité managée → Sélectionner les membres → Machine virtuelle → `vm-app`**.
4. **Vérifier + attribuer**. Attendez quelques minutes pour la propagation RBAC.
5. Sur VM-App, Flask utilise directement `ManagedIdentityCredential()`. **Aucun `az login` n'est nécessaire pour l'application Python**.

> Pour les futurs tests Azure CLI depuis le PC, attribuez aussi **Storage Table Data Reader** à votre utilisateur Azure. Les permissions de gestion du portail ne donnent pas automatiquement accès aux données des tables. Vous devez disposer des droits nécessaires pour attribuer ce rôle.

### Étape B3 — Créer l'application Flask complète sur VM-App

Sur **VM-App**, créez le fichier :

```bash
cd ~/calculator-api
nano app.py
```

Collez le code Python 3.12 complet ci-dessous. **Les noms des champs JSON, les routes et les messages techniques restent en anglais pour conserver les mêmes commandes de test que la version anglaise.**

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

Définissez la variable avec le **nom réel** du compte Storage, pas son URL :

```bash
export STORAGE_ACCOUNT_NAME="<STORAGE_ACCOUNT>"
export TABLE_NAME="Calculations"
source ~/calculator-api/.venv/bin/activate
python ~/calculator-api/app.py
```

Gardez ce terminal ouvert pour les premiers tests. Dans une **deuxième session SSH** vers VM-App :

```bash
curl -i http://127.0.0.1:5000/health
curl -i -X POST http://127.0.0.1:5000/api/calculate \
  -H 'Content-Type: application/json' \
  -d '{"operation":"multiply","a":12,"b":22}'
```

**Résultat attendu :** `/health` renvoie HTTP `200` ; `/api/calculate` renvoie HTTP `200`, `result: 264`, `status: stored` et un `rowKey` généré. Si `TableNotFound` apparaît, créez `Calculations`. En cas de `403`, vérifiez **Storage Table Data Contributor** pour l'identité managée de VM-App et patientez le temps de la propagation RBAC.

### Étape B4 — Rendre Flask persistant **avant de supprimer l'IP publique**

Arrêtez le processus Flask au premier plan avec `Ctrl+C`. Sur VM-App, créez un service `systemd` :

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

**Remplacez `<STORAGE_ACCOUNT>` dans la définition du service avant d'exécuter les commandes.** Consultez les journaux :

```bash
sudo journalctl -u calculator-api -n 60 --no-pager
```

---

## 5. Phase C — Tester HTTP publiquement depuis le PC local

### Étape C1 — Autoriser temporairement HTTP public

1. **VM-App → Mise en réseau → Paramètres réseau / Règles de ports entrants**, ou ouvrez directement `nsg-app`.
2. Ajoutez une règle entrante : **Adresses IP source** = `<MY_PUBLIC_IP>/32` ; **Port de destination** `5000` ; **Protocole TCP** ; **Action Autoriser** ; **Priorité 300** ; nom `Allow-HTTP-Student-PC`.
3. Si un NSG est associé au sous-réseau **et** un autre à la carte réseau, **les deux doivent autoriser** le trafic.

Sur le **PC local**, dans PowerShell :

```powershell
Invoke-RestMethod -Uri "http://<APP_PUBLIC_IP>:5000/health" -Method GET

Invoke-RestMethod `
  -Uri "http://<APP_PUBLIC_IP>:5000/api/calculate" `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"operation":"multiply","a":12,"b":22}'
```

**Résultat attendu :** `status: ok`, puis `result: 264` et `status: stored`.

> N'ouvrez HTTP qu'aux IP nécessaires. Si VM-Client sort avec une autre IP publique, ajoutez une deuxième règle temporaire à la phase suivante. Évitez `Any`, sauf démonstration très courte et strictement supervisée par le formateur.

---

## 6. Phase D — Déployer VNet-Client et tester l'IP publique de VM-App

### Étape D1 — Créer VNet-Client et VM-Client

1. **Réseaux virtuels → Créer** `vnet-client`, espace d'adressage `10.20.0.0/16`, sous-réseau `snet-client` `10.20.1.0/24`.
2. **Machines virtuelles → Créer** `vm-client` : Ubuntu 24.04 LTS, même région, utilisateur `azureuser`, clé publique SSH, VNet `vnet-client`, sous-réseau `snet-client`, **IP publique activée**.
3. NSG `nsg-client` : SSH entrant (22) uniquement depuis `<MY_PUBLIC_IP>/32`. VM-Client n'a **pas** besoin d'un port 5000 entrant public.
4. Activez l'**identité managée affectée par le système** via **VM-Client → Identité → Affectée par le système → Activé → Enregistrer**.
5. Notez `<CLIENT_PUBLIC_IP>` et `<CLIENT_PRIVATE_IP>`.

Sur le **PC local** :

```powershell
ssh -i "$HOME\.ssh\<YOUR_PRIVATE_KEY>" azureuser@<CLIENT_PUBLIC_IP>
```

Sur **VM-Client**, installez les outils de diagnostic et Azure CLI :

```bash
sudo apt update
sudo apt install -y curl dnsutils iputils-ping openssh-client ca-certificates
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
az version
```

### Étape D2 — Tester VM-App via son **IP publique** depuis VM-Client

Sur VM-Client :

```bash
curl -i --connect-timeout 5 http://<APP_PUBLIC_IP>:5000/health
```

Si la requête expire, le NSG de VM-App n'autorise peut-être que l'IP publique de votre PC. **Identifiez l'IP publique réelle de sortie de VM-Client** :

```bash
curl -4 https://api.ipify.org
```

Dans `nsg-app`, ajoutez une règle **temporaire** autorisant TCP 5000 depuis `<CLIENT_EGRESS_PUBLIC_IP>/32`, par exemple priorité `310`, nom `Allow-HTTP-Client-Public`. Réessayez. **Ne supposez pas** que l'IP publique de sortie est identique à l'IP publique entrante de VM-Client : vérifiez-la.

**Résultat attendu :** HTTP 200. À ce stade, les VNets ne sont pas appairés : le trafic utilise le point d'accès **public** de VM-App.

### Étape D3 — Observer l'isolation avant le peering

Depuis VM-Client :

```bash
curl -v --connect-timeout 5 http://<APP_PRIVATE_IP>:5000/health
```

**Résultat attendu :** échec ou expiration, car VNet-Client n'a pas de chemin réseau privé vers VNet-App. Ne vous fiez pas uniquement à `ping` : ICMP peut être bloqué indépendamment de TCP. **`curl` est le test applicatif de référence**.

---

## 7. Phase E — Supprimer l'IP publique de VM-App, puis appairer les VNets

### Étape E1 — Supprimer l'IP publique de VM-App

**Avant de continuer :** vérifiez que `calculator-api` tourne comme service `systemd` et que vous avez noté l'IP privée de VM-App.

1. **VM-App → Mise en réseau → Interface réseau** (ouvrez la ressource NIC).
2. **Configurations IP → `ipconfig1` → Adresse IP publique → Dissocier / Aucune → Enregistrer**. Les libellés du portail peuvent varier.
3. Vérifiez que VM-App conserve `<APP_PRIVATE_IP>` mais **n'a plus d'IP publique**.

Effectuez les tests suivants :

- PC local → SSH vers l'**ancienne** `<APP_PUBLIC_IP>` : **échec attendu**.
- PC local → HTTP vers l'**ancienne** `<APP_PUBLIC_IP>:5000` : **échec attendu**.
- VM-Client → HTTP vers l'**ancienne** `<APP_PUBLIC_IP>:5000` : **échec attendu**.
- VM-Client → HTTP vers `<APP_PRIVATE_IP>:5000` : **toujours en échec** avant le peering.

Utilisez un délai maximal de 5 secondes pour `curl`. L'ancienne IP publique peut être réattribuée plus tard : une réponse inattendue ne prouve pas que votre VM est encore accessible.

> La suppression de l'IP publique **ne supprime pas automatiquement** tout accès Internet sortant : cela dépend de la configuration réseau Azure. Vérifiez que le service fonctionne avant de supprimer l'IP, et évitez de dépendre de nouvelles installations de paquets ensuite.

### Étape E2 — Créer un **VNet Peering bidirectionnel**

1. **VNet-App → Peerings → Ajouter**.
2. Appairez avec `vnet-client` et configurez le peering inverse de `vnet-client` vers `vnet-app` (le portail peut créer les deux en une seule opération).
3. Autorisez **l'accès au réseau virtuel** dans les deux sens. Conservez les paramètres par défaut pour le transit de passerelle et le trafic transféré, sauf instruction contraire.
4. Vérifiez l'état **Connected / Connecté** sur les deux peerings.

Sur **VM-Client** :

```bash
curl -i --connect-timeout 5 http://<APP_PRIVATE_IP>:5000/health
curl -i -X POST http://<APP_PRIVATE_IP>:5000/api/calculate \
  -H 'Content-Type: application/json' \
  -d '{"operation":"multiply","a":12,"b":22}'
```

**Résultat attendu :** HTTP 200 si le NSG et le pare-feu invité autorisent la connexion. **La règle NSG par défaut `AllowVNetInBound` autorise souvent déjà les VNets appairés.** Une règle personnalisée n'est pas systématiquement nécessaire.

### Étape E3 — Exercice de dépannage NSG

Si `curl` privé échoue, vérifiez dans cet ordre :

1. Peerings **Connectés** dans les deux sens ; IP privée correcte ; service Flask actif.
2. Règles de sécurité effectives sur la NIC et le sous-réseau de VM-App ; recherchez un refus de priorité supérieure.
3. Pare-feu de l'OS (`sudo ufw status` sur VM-App, s'il est activé).
4. Si nécessaire seulement, ajoutez une règle NSG entrante **Allow** : source `10.20.0.0/16`, destination `<APP_PRIVATE_IP>`, TCP, port `5000`, priorité `300` **si libre et non annulée par un refus plus prioritaire**.
5. Relancez `curl` vers l'IP privée.

**Expérience facultative :** créez temporairement une règle entrante **Deny** sur VM-App pour TCP 5000 depuis `10.20.0.0/16`, priorité `250`. Vérifiez que `curl` échoue, puis **supprimez cette règle** et vérifiez le retour du succès. Cela démontre les priorités NSG sans dépendre d'une mauvaise configuration accidentelle.

### Étape E4 — Facultatif : se connecter en SSH privé de VM-Client vers VM-App

VM-App n'a plus d'IP publique. Après le peering, vous pouvez l'administrer depuis VM-Client si une clé SSH autorisée est configurée.

**Configuration sûre :** sur VM-Client, générez une **nouvelle** paire de clés ; affichez uniquement la **clé publique** :

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_lab -N ''
cat ~/.ssh/id_ed25519_lab.pub
```

Copiez la ligne publique `ssh-ed25519 ...`. Dans **Portail Azure → VM-App → Exécuter une commande → RunShellScript**, exécutez le script suivant en remplaçant `<CLIENT_VM_PUBLIC_KEY>` par la clé publique complète :

```bash
sudo mkdir -p /home/azureuser/.ssh
printf '%s\n' '<CLIENT_VM_PUBLIC_KEY>' | sudo tee -a /home/azureuser/.ssh/authorized_keys >/dev/null
sudo chown -R azureuser:azureuser /home/azureuser/.ssh
sudo chmod 700 /home/azureuser/.ssh
sudo chmod 600 /home/azureuser/.ssh/authorized_keys
```

**Ne copiez jamais la clé privée** dans le portail ni entre les VM. Sur VM-Client :

```bash
ssh -i ~/.ssh/id_ed25519_lab azureuser@<APP_PRIVATE_IP>
sudo systemctl status calculator-api --no-pager
```

La fonctionnalité **Exécuter une commande** du portail permet aussi d'inspecter VM-App sans IP publique, même si SSH ne fonctionne pas encore.

---

## 8. Phase F — Tester publiquement Azure Table Storage avec Azure CLI

Flask utilise déjà **l'identité managée affectée par le système de VM-App** pour écrire dans Storage. Comparez maintenant deux identités **Azure CLI** distinctes :

| Client | Identité | Rôle nécessaire | Test |
|---|---|---|---|
| PC local | Utilisateur Entra de l'étudiant via `az login` | `Storage Table Data Reader` | `az storage entity query` |
| VM-Client | Identité managée de VM-Client via `az login --identity` | `Storage Table Data Reader` | `az storage entity query` |
| Flask sur VM-App | Identité managée de VM-App via le SDK Python | `Storage Table Data Contributor` | `create_entity()` |

### Étape F1 — Attribuer le rôle de lecture à VM-Client

**Compte Storage → Contrôle d'accès (IAM) → Ajouter une attribution de rôle → Storage Table Data Reader → Identité managée → Machine virtuelle → `vm-client` → Vérifier + attribuer**.

Vérifiez également que **l'utilisateur Azure connecté sur votre PC** dispose de `Storage Table Data Reader`. Attendez la propagation des attributions.

### Étape F2 — Tester depuis le PC local (point d'accès public)

Sur le **PC local**, dans PowerShell :

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

**Résultat attendu :** la requête réussit et affiche les lignes déjà enregistrées. En présence de plusieurs abonnements, sélectionnez le bon avec `az account set --subscription "<SUBSCRIPTION_ID>"`.

### Étape F3 — Tester depuis VM-Client (point d'accès public)

Sur **VM-Client** :

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

**Résultat attendu :** la requête réussit. `az logout` évite de réutiliser une ancienne session interactive ; `az login --identity` sélectionne l'identité managée de la VM. Si cette identité voit plusieurs abonnements, choisissez le bon.

**Vérifiez le DNS avant la privatisation**, depuis VM-Client :

```bash
nslookup <STORAGE_ACCOUNT>.table.core.windows.net
```

**Résultat attendu :** adresse publique du service Storage, éventuellement après une chaîne CNAME. L'IP publique exacte peut varier.

---

## 9. Phase G — Désactiver l'accès public de Storage et observer les échecs

1. **Compte Storage → Mise en réseau → Pare-feu et réseaux virtuels / Accès réseau public**.
2. Choisissez **Désactivé** pour **Accès réseau public**, puis enregistrez. Ne désactivez pas le compte Storage lui-même.
3. Attendez l'application du paramètre réseau.

**Retestez depuis le PC local** :

```powershell
az storage entity query `
  --account-name <STORAGE_ACCOUNT> `
  --table-name Calculations `
  --auth-mode login `
  --num-results 10 `
  --output table
```

**Retestez depuis VM-Client** :

```bash
az storage entity query \
  --account-name <STORAGE_ACCOUNT> \
  --table-name Calculations \
  --auth-mode login \
  --num-results 10 \
  --output table
```

**Retestez l'application depuis VM-Client** :

```bash
curl -i -X POST http://<APP_PRIVATE_IP>:5000/api/calculate \
  -H 'Content-Type: application/json' \
  -d '{"operation":"multiply","a":12,"b":22}'
```

**Résultat attendu :** les deux requêtes Azure CLI vers les données échouent à cause de la restriction réseau publique de Storage, et l'écriture Flask échoue (l'API d'exemple renvoie HTTP 502). Les messages et codes HTTP exacts peuvent varier ; un `403` peut venir d'une restriction réseau **ou** d'un manque de droits RBAC. **Vérifiez donc que les deux requêtes fonctionnaient en phase F.** La route Flask `/health` doit continuer à renvoyer 200, car elle n'accède pas à Storage.

> Désactiver l'accès public ne bloque pas les opérations de gestion du portail Azure. Le test concerne les **requêtes de données Table Storage**. Le DNS peut continuer à résoudre une IP publique tant que le DNS privé n'est pas configuré.

---

## 10. Phase H — Créer le Private Endpoint Storage et le DNS privé

### Étape H1 — Créer le Private Endpoint pour le service **table**

1. **Compte Storage → Mise en réseau → Connexions de point de terminaison privé → + Point de terminaison privé** (ou **Centre Private Link → Points de terminaison privés → Créer**).
2. Nom `pe-storage-table` ; groupe `rg-private-lab` ; même région.
3. Ressource cible : votre compte Storage ; **Sous-ressource cible = `table`**. **Ne choisissez pas** `blob` ou `file`.
4. Réseau virtuel `vnet-app` ; sous-réseau `snet-private-endpoints`.
5. Dans **DNS**, choisissez **Intégrer à une zone DNS privée = Oui** et utilisez/créez `privatelink.table.core.windows.net`.
6. Créez la ressource. Vérifiez que la connexion Private Endpoint est **Approved / Approuvée** et notez `<STORAGE_PE_PRIVATE_IP>`.

### Étape H2 — Lier la zone DNS privée aux **deux VNets**

1. **Zones DNS privées → `privatelink.table.core.windows.net`**.
2. Dans **Jeux d'enregistrements**, vérifiez qu'un enregistrement **A** portant le nom du compte Storage pointe vers `<STORAGE_PE_PRIVATE_IP>`. Azure le crée généralement automatiquement via le groupe de zones DNS privées.
3. **Liens de réseau virtuel → + Ajouter** : liez `vnet-app`, **inscription automatique désactivée**.
4. Ajoutez un second lien vers `vnet-client`, **inscription automatique désactivée**.
5. Vérifiez que les deux liens sont **Completed / Terminés**.

> Lier uniquement VNet-App ne suffit pas : VM-Client doit aussi résoudre le nom Storage vers l'IP du Private Endpoint. Le SDK et Azure CLI utilisent toujours le **nom DNS normal** `<STORAGE_ACCOUNT>.table.core.windows.net`, jamais directement le nom `privatelink` ni l'IP brute.

### Étape H3 — Valider le DNS depuis VM-Client et VM-App

Sur **VM-Client** :

```bash
nslookup <STORAGE_ACCOUNT>.table.core.windows.net
```

**Résultat attendu :** le nom Storage habituel résout, souvent via un CNAME `privatelink`, vers `<STORAGE_PE_PRIVATE_IP>`, une adresse du réseau `10.10.2.0/24`.

Si SSH privé a été configuré à l'étape E4, exécutez la même commande sur **VM-App** :

```bash
nslookup <STORAGE_ACCOUNT>.table.core.windows.net
```

Vous pouvez aussi passer par **VM-App → Exécuter une commande → RunShellScript**. Les deux VM doivent résoudre vers la même IP privée du Private Endpoint. La propagation DNS peut prendre un peu de temps : vérifiez d'abord les liens et les enregistrements.

### Étape H4 — Retester Storage depuis VM-Client (accès privé)

Sur **VM-Client** :

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

**Résultat attendu :** succès, cette fois via l'**IP privée** de Storage. VM-Client atteint le Private Endpoint grâce au VNet Peering vers VNet-App. En cas d'échec, vérifiez l'approbation du Private Endpoint, la sous-ressource `table`, les liens DNS des deux VNets, la résolution DNS, RBAC et les règles NSG effectives.

### Étape H5 — Retester Storage depuis le **PC local** (toujours bloqué)

Sur le **PC local**, répétez la requête de l'étape F2.

**Résultat attendu :** l'accès aux données reste bloqué. Le PC n'appartient à aucun des deux VNets et n'a pas de route privée vers le Private Endpoint. Sa résolution DNS reste généralement publique. Une configuration VPN/DNS privé pourrait modifier ce comportement ; le TP suppose que le PC utilise l'Internet public ordinaire.

---

## 11. Phase I — Validation privée finale de bout en bout

Depuis **VM-Client**, appelez l'API avec l'**IP privée** de VM-App :

```bash
curl -i -X POST http://<APP_PRIVATE_IP>:5000/api/calculate \
  -H 'Content-Type: application/json' \
  -d '{"operation":"multiply","a":12,"b":22}'
```

**Exemple de réponse attendue** (identifiants variables) :

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

Vérifiez l'enregistrement avec **l'identité managée de VM-Client** :

```bash
az storage entity query \
  --account-name <STORAGE_ACCOUNT> \
  --table-name Calculations \
  --auth-mode login \
  --num-results 20 \
  --output table
```

Comparez le `rowKey` de la réponse avec le `RowKey` dans la table. Si vous avez accès à VM-App en SSH privé, consultez les journaux :

```bash
sudo journalctl -u calculator-api -n 80 --no-pager
```

**Flux final à expliquer au formateur :**

1. VM-Client envoie une requête HTTP à l'**IP privée** de VM-App via VNet Peering.
2. VM-App effectue le calcul Flask.
3. Python obtient un jeton grâce à **l'identité managée affectée par le système de VM-App**.
4. Azure RBAC accorde à cette identité le rôle **Storage Table Data Contributor**.
5. Le DNS résout le point d'accès Table Storage vers l'**IP du Private Endpoint**.
6. VM-App écrit l'entité par la connectivité réseau privée.
7. VM-Client utilise **sa propre** identité managée (`Storage Table Data Reader`) pour vérifier la ligne via le même Private Endpoint.
8. Le PC local ne peut toujours pas interroger Storage depuis l'Internet public.

### Matrice de connectivité attendue

| Test | Phase publique initiale | IP publique de VM-App supprimée, avant peering | Après peering, avant verrouillage Storage | Storage public désactivé, avant PE | État final : PE + DNS |
|---|---|---|---|---|---|
| PC → IP publique Flask VM-App | OK | Échec | Échec | Échec | Échec |
| VM-Client → IP publique Flask VM-App | OK | Échec | Échec | Échec | Échec |
| VM-Client → IP privée Flask VM-App `/health` | Échec (pas de peering) | Échec | OK | OK | OK |
| PC → données Table Storage via Azure CLI | OK | OK | OK | Échec | Échec |
| VM-Client → données Table Storage via Azure CLI | OK | OK | OK | Échec | OK en privé |
| VM-App → écriture Table Storage via Flask | OK | OK* | OK | Échec | OK en privé |

\*Sous réserve que VM-App conserve une connectivité sortante vers le service Storage public après suppression de son IP publique. Sinon, diagnostiquez la configuration de sortie Azure plutôt qu'un problème d'identité managée.

---

## 12. Guide de dépannage

| Symptôme | Cause probable | Vérification / action |
|---|---|---|
| `TableNotFound` | Table `Calculations` absente ou mauvais compte/casse | Compte Storage → **Tables** ; créer `Calculations` |
| Flask renvoie `502` | Échec d'écriture Storage | `sudo journalctl -u calculator-api -n 80 --no-pager` ; lire l'exception Azure sous-jacente |
| Storage `403` avant verrouillage | RBAC absent, mauvaise identité ou règle réseau | Vérifier `Storage Table Data Contributor` sur VM-App ; `Storage Table Data Reader` sur VM-Client/utilisateur ; attendre la propagation |
| `az login --identity` échoue | Identité managée désactivée, IMDS inaccessible ou mauvaise machine | VM-Client → **Identité** ; vérifier « Affectée par le système = Activé » ; exécuter sur la VM Azure |
| Azure CLI utilise le compte personnel au lieu de la VM | Ancienne connexion interactive active | Sur VM-Client : `az logout`, puis `az login --identity` et `az account show` |
| HTTP public expire | Mauvaise IP publique, Flask arrêté, IP source refusée, NSG/pare-feu | Vérifier service, `curl localhost`, règle NSG `/32` et IP publique de la VM |
| HTTP privé expire après peering | Peering déconnecté, refus NSG, mauvaise IP privée, pare-feu invité | Vérifier peering **Connected**, règles NSG effectives, écoute Flask sur `0.0.0.0:5000` |
| SSH vers VM-App ne fonctionne plus après suppression IP | Normal : plus de point d'entrée public | SSH depuis VM-Client après peering ou **Exécuter une commande** dans le portail |
| DNS Storage renvoie encore une IP publique après PE | Enregistrement A ou lien VNet absent, mauvaise zone | Vérifier `privatelink.table.core.windows.net`, enregistrement A, liens aux **deux** VNets |
| DNS privé correct mais requête Storage en échec | PE non approuvé, mauvaise sous-ressource, NSG/routage ou RBAC | Vérifier PE `table`, statut approuvé, port 443 et rôles d'accès aux données |
| `ManagedIdentityCredential` échoue | Identité de VM-App désactivée ou indisponible | VM-App → Identité ; lire les journaux ; le SDK utilise l'identité VM indépendamment d'Azure CLI |
| Installation de paquets impossible | Accès sortant de la VM indisponible | Installer avant de supprimer l'IP publique de VM-App ; vérifier les sorties réseau et dépôts |

### Commandes de diagnostic réseau facultatives

Sur VM-Client :

```bash
# L'application est-elle accessible en privé ?
curl -v --connect-timeout 5 http://<APP_PRIVATE_IP>:5000/health

# Storage se résout-il vers une IP privée ?
nslookup <STORAGE_ACCOUNT>.table.core.windows.net

# Le port HTTPS 443 accepte-t-il une connexion ? (pas un test RBAC)
curl -v --connect-timeout 5 https://<STORAGE_ACCOUNT>.table.core.windows.net/
```

Une réponse HTTP `403` ou une autre réponse du service à cette dernière commande peut prouver que la connexion réseau atteint Azure Storage ; **elle ne prouve pas** que l'accès aux données est autorisé.

---

## 13. Questions de vérification des acquis

1. Quelle différence existe-t-il entre l'IP publique et l'IP privée d'une VM ?
2. Pourquoi VM-Client ne peut-elle pas joindre l'IP privée de VM-App avant le VNet Peering ?
3. Le VNet Peering contourne-t-il automatiquement une règle NSG restrictive ?
4. Pourquoi `/health` peut-il fonctionner alors que `/api/calculate` échoue après la désactivation de l'accès public de Storage ?
5. Que remplace `ManagedIdentityCredential()` dans l'application ?
6. Quelle différence existe-t-il entre `Storage Table Data Reader` et `Storage Table Data Contributor` ?
7. Pourquoi lier la zone DNS privée aux deux VNets ?
8. Pourquoi le PC local ne peut-il toujours pas interroger Storage après la création du Private Endpoint ?
9. Quelle ressource nécessite un Private Endpoint : VM-App, le compte Storage ou les deux ?
10. Quels outils permettent de distinguer une erreur applicative d'une erreur DNS, réseau ou RBAC ?

## 14. Nettoyage — obligatoire

1. Conservez les captures d'écran ou notes demandées par le formateur.
2. **Portail Azure → Groupes de ressources → `rg-private-lab` → Supprimer le groupe de ressources**.
3. Saisissez le nom du groupe pour confirmer. Attendez la fin de la suppression.
4. Vérifiez qu'il ne reste aucune ressource du TP (IP publiques, disques managés, Private Endpoints, zones DNS privées). Supprimez séparément toute ressource créée hors du groupe.

**TP terminé :** vous avez démontré l'accès public, l'isolation réseau, le VNet Peering, le contrôle NSG, les identités managées et Azure RBAC, la désactivation de l'accès public à Table Storage, le Private Endpoint, le DNS privé et la validation privée de bout en bout.
