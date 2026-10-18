# TP Microsoft Azure — Cloud Computing, Réseaux & Cybersécurité | AZ-900, AZ-104, AZ-500

> **Projet pédagogique réalisé dans le cadre de mes interventions en tant que formateur auprès des étudiants de [Guardia Cybersecurity School](https://guardia.school/), sur le campus de Paris–La Défense.** Ce dépôt est un support de travaux pratiques indépendant ; il ne constitue ni une publication officielle de l'école, ni un support officiel Microsoft.

**Mots-clés :** formation Azure, TP Azure, cybersécurité Cloud, Azure Fundamentals AZ-900, Azure Administrator AZ-104, Azure Security AZ-500, Microsoft Azure, Azure Functions Python 3.12, Virtual Network (VNet), VNet peering, Network Security Group (NSG), Private Endpoint, Private DNS, Azure Storage Table, Managed Identity, Azure RBAC, DevSecOps, Guardia Cybersecurity School, Paris La Défense.

## À propos du projet et de Guardia Cybersecurity School

J'interviens comme **formateur Cloud & DevOps / Microsoft Azure** auprès des étudiants de **Guardia Cybersecurity School**. J'ai conçu ce TP pour relier les notions de **Cloud Computing**, d'**administration Azure** et de **sécurité des infrastructures Cloud** à une situation concrète : déployer une API serverless, la tester sur Internet, puis sécuriser progressivement ses communications et ses données.

[Guardia Cybersecurity School](https://guardia.school/) est une école spécialisée en **cybersécurité**, proposant notamment des cursus Bachelor et Mastère sur ses campus de **Paris, Lyon et Bordeaux**. Son approche met l'accent sur **l'apprentissage par projets** et la pratique en environnement technique. Le campus de Paris est situé à **La Défense**, à proximité du Campus Cyber. Le cursus **[Mastère Expert Sécurité des Systèmes, Réseaux et Infrastructures Cloud](https://guardia.school/formations/mastere-expert-securite-des-infrastructures-cloud.html)** illustre la place des infrastructures et de la sécurité Cloud dans l'offre de formation de l'école.

Ce laboratoire s'inscrit dans cet esprit : **apprendre en déployant, tester, provoquer une panne contrôlée, diagnostiquer, puis corriger**.

## Que va-t-on apprendre ?

Ce TP guidé d'environ **3 à 4 heures** combine les thèmes des certifications **Microsoft AZ-900, AZ-104 et AZ-500** :

- **AZ-900 — Cloud Fundamentals :** IaaS, PaaS, serverless, régions, Resource Groups et modèle de responsabilité partagée.
- **AZ-104 — Azure Administration :** Ubuntu VM, Virtual Networks, subnets, VNet peering, DNS, Azure CLI, Function App et Storage Account.
- **AZ-500 — Azure Security :** NSG, Private Endpoint, désactivation du public network access, Managed Identity, Azure RBAC et accès privé aux données.

**Scénario :** une Function App Python 3.12 reçoit une requête HTTP, calcule un résultat et l'enregistre dans **Azure Table Storage**. Une VM Ubuntu située dans un autre VNet sert de poste de développement et de test. Les étudiants commencent par les endpoints publics, puis configurent **Private Link**, **VNet peering** et **Private DNS**. Ils comparent enfin l'accès à Storage avant et après sa fermeture au réseau public, avec authentification par **Managed Identity**.

**Méthode :** toutes les ressources Azure sont créées via **Azure Portal** ; les commandes Linux/Azure CLI servent à installer les outils, écrire et publier le code, effectuer les tests et interroger la table. **Python 3.12** est conservé.

## À qui s'adresse ce TP ?

Aux étudiants en **cybersécurité**, **Cloud Computing**, **administration systèmes et réseaux**, ainsi qu'aux personnes qui souhaitent préparer les notions fondamentales d'**AZ-900**, approfondir **AZ-104** ou découvrir les contrôles de sécurité abordés dans **AZ-500**. Ce TP ne remplace pas à lui seul les parcours officiels de certification.

## Questions fréquentes (FAQ)

**Faut-il connaître Python pour réaliser ce laboratoire ?**  
Non. Le code de la Function App est fourni dans les étapes ; l'objectif principal est de comprendre l'architecture et sa sécurisation.

**Pourquoi utiliser deux Virtual Networks ?**  
Pour observer l'isolation réseau initiale, puis comprendre le rôle de **VNet peering** et de la résolution **Private DNS**.

**Quelle est la différence entre VNet Integration et Private Endpoint pour Azure Functions ?**  
**VNet Integration** sert à la connectivité sortante de la Function App ; le **Private Endpoint** permet de l'atteindre sur une adresse IP privée en entrée.

**Pourquoi utiliser Managed Identity ?**  
Pour obtenir des jetons Microsoft Entra sans stocker de secrets dans le code. Sur Ubuntu, `az login --identity` est nécessaire pour utiliser l'identité managée de la VM, plutôt que la session utilisateur précédente.

**Peut-on réaliser le TP uniquement depuis Azure Portal ?**  
La création et la configuration des ressources se font dans le Portal ; la VM Ubuntu est utilisée pour écrire les fichiers, publier l'application et lancer les commandes de validation.

## Liens utiles

- [Guardia Cybersecurity School — site officiel](https://guardia.school/)
- [Campus Guardia Paris — La Défense](https://guardia.school/campus/paris.html)
- [Formations Guardia en cybersécurité](https://guardia.school/formations/)
- [Mastère Sécurité des Infrastructures & Cloud](https://guardia.school/formations/mastere-expert-securite-des-infrastructures-cloud.html)
- [Microsoft Learn — Azure](https://learn.microsoft.com/fr-fr/azure/)
