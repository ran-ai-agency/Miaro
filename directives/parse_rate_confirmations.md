# Directive: Parse Rate Confirmation PDFs

## Objectif
Extraire les données structurées des documents PDF de confirmation de tarifs C.A.T. Inc. et les convertir en formats exploitables (JSON et Excel).

## Contexte
Les documents "Rate Confirmation" sont soumis par l'équipe de tarification C.A.T. Il existe **4 types de services** différents, chacun avec sa propre structure:

| Type | Description | Fichiers générés |
|------|-------------|------------------|
| **FTL** | Full Truckload - Routes avec tarifs forfaitaires | `rate_confirmations_ftl.json/.xlsx` |
| **LTL** | Less-than-Truckload - Tarifs par nombre de skids | `rate_confirmations_ltl.json/.xlsx` |
| **Shunting** | Services de manoeuvre - Tarifs horaires | `rate_confirmations_shunting.json/.xlsx` |
| **Accessorial** | Frais accessoires - Frais mensuels | `rate_confirmations_accessorial.json/.xlsx` |

Un fichier `rate_confirmations_unknown.json` est toujours généré pour tracker les types non reconnus (vide si aucun).

## Inputs
- Fichiers PDF dans le dossier OneDrive: `C:\Users\ranai\OneDrive\Documents\Miaro\*.pdf`
- Format attendu: Documents C.A.T. Inc. Rate Confirmation (anglais ou français)

## Scripts d'exécution

### 1. Parser PDF → JSON (par type)
```bash
python execution/parse_rate_confirmations.py
```

**Outputs:**
- `rate_confirmations_ftl.json`
- `rate_confirmations_ltl.json`
- `rate_confirmations_shunting.json`
- `rate_confirmations_accessorial.json`
- `rate_confirmations_unknown.json` (toujours créé, vide si aucun type inconnu)

### 2. Convertir JSON → Excel
```bash
python execution/json_to_excel.py
```

**Outputs:**
- `rate_confirmations_ftl.xlsx`
- `rate_confirmations_ltl.xlsx`
- `rate_confirmations_shunting.xlsx`
- `rate_confirmations_accessorial.xlsx`

## Structure des données par type

### Métadonnées communes (tous les types)
| Champ | Description | Exemple |
|-------|-------------|---------|
| `source_file` | Nom du fichier PDF source | `2025-12-03 Laredo Inboud.pdf` |
| `date` | Date de la confirmation (ISO) | `2025-12-03` |
| `customer` | Nom du client | `TIMSA` |
| `equipment` | Type d'équipement | `Dry van` |
| `doc_type` | Type de document détecté | `FTL`, `LTL`, `Shunting`, `Accessorial` |
| `notes` | Notes et conditions | `Fuel surcharge included...` |

### FTL - Lanes (Routes)
| Champ | Description | Exemple |
|-------|-------------|---------|
| `origin` | Ville et état/province d'origine | `Duncan, SC` |
| `destination` | Ville et état/province de destination | `Laredo, TX` |
| `miles` | Distance en miles | `1311` |
| `mode` | Mode de transport (optionnel) | `DV`, `FB`, `REEFER` |
| `flat` | Tarif forfaitaire (Line Haul) | `2265.0` |
| `rpm` | Tarif par mile (optionnel) | `2.70` |
| `fund_type` | Devise | `USD` ou `CAD` |

### LTL - Rates (Tarifs par skids)
| Champ | Description | Exemple |
|-------|-------------|---------|
| `origin` | Ville d'origine | `Calgary, AB` |
| `destination` | Ville de destination | `Richmond BC` |
| `skids` | Nombre de skids | `1`, `2`, `14-26` |
| `rate` | Tarif | `250.0` |
| `fund_type` | Devise | `CAD` |

### Shunting - Services
| Champ | Description | Exemple |
|-------|-------------|---------|
| `description` | Description du service | `Shunting service - Valleyfield's plant` |
| `location` | Lieu du service | `Salaberry-de-Valleyfield` |
| `rate_type` | Type de tarif | `hourly` |
| `rate` | Tarif horaire | `62.5` |
| `fund_type` | Devise | `CAD` |

### Accessorial - Charges
| Champ | Description | Exemple |
|-------|-------------|---------|
| `description` | Description du frais | `WOODSTOCK TRAILER DETENTION` |
| `period` | Période de facturation | `monthly`, `daily` |
| `rate` | Montant | `550.0` |
| `fund_type` | Devise | `CAD` |

## Détection automatique du type de service

Le système détecte le type basé sur les en-têtes du document:

| Indicateurs | Type détecté |
|-------------|--------------|
| `Origin/Origine` + `Destination` + (`Flat`/`Line Haul`/`Miles`/`Devise`) | FTL |
| `# of Skids` ou `Number of Skids` | LTL |
| `Shunting` ou `Rate per hour` ou `/hrs` | Shunting |
| `Per month` + `Fund Type` ou `Trailer detention` | Accessorial |

Si aucun pattern n'est reconnu → marqué comme **Unknown** (défaut FTL) et ajouté à `rate_confirmations_unknown.json` pour investigation.

## Support bilingue (anglais/français)

Le parser reconnaît les en-têtes dans les deux langues:

| Anglais | Français |
|---------|----------|
| Origin | Origine |
| Customer | Client |
| Equipment | Équipement |
| Fund Type | Devise |
| Rate Confirmation | Confirmation de Taux |
| Notes | Remarques |

## Particularités du parsing

### Variations de colonnes dans les PDFs
- `Flat` / `Line Haul` / `Linehaul` / `Min.` / `Taux` / `Tarif` → `flat`
- `Miles` / `FSC Miles` / `IM` / `OTR` → `miles`
- `Destination` / `Stops` → `destination`

### Problèmes d'extraction de texte PDF
Le parsing gère automatiquement:
- Espaces dans les montants: `$ 8 75` → `$875`
- Espaces avant décimales: `$ 2 .70` → `$2.70`
- Espaces dans les nombres: `1 ,132` → `1,132`
- Documents multi-pages (ex: Cascades avec 56 lanes)

### Extraction de date fallback
Si la date n'est pas trouvée dans le contenu, elle est extraite du nom de fichier:
- `2022-01-24 Rate Confirmation...pdf` → `2022-01-24`

### Extraction de location (Shunting)
La location est extraite de:
1. La description du service (`Shunting service - Valleyfield's plant`)
2. Le nom du fichier source (`Shunting from Valleyfield`)

## Workflow complet

```bash
# 1. Placer les PDFs dans le dossier OneDrive:
#    C:\Users\ranai\OneDrive\Documents\Miaro\

# 2. Parser tous les PDFs (génère 5 fichiers JSON)
python execution/parse_rate_confirmations.py

# 3. Convertir en Excel (génère 4 fichiers Excel)
python execution/json_to_excel.py

# 4. Vérifier les résultats
# - rate_confirmations_ftl.json/.xlsx
# - rate_confirmations_ltl.json/.xlsx
# - rate_confirmations_shunting.json/.xlsx
# - rate_confirmations_accessorial.json/.xlsx
# - rate_confirmations_unknown.json (devrait être vide [])
```

## Dépendances Python
- `pdfplumber` - Extraction de texte PDF
- `pandas` - Manipulation de données
- `openpyxl` - Écriture Excel

Les dépendances sont installées automatiquement si manquantes.

## Edge Cases et Limitations

### Gérés
- Documents multi-pages
- Différents formats de colonnes
- Espacement irrégulier dans le texte extrait
- Valeurs manquantes (miles, mode)
- Documents en français
- Date manquante dans le contenu (fallback sur filename)
- 4 types de services différents
- **Chemins longs Windows** (> 260 caractères) - via préfixe `\\?\`

### Non gérés
- PDFs scannés (images) - nécessiterait OCR
- Formats de documents non-C.A.T.

## Validation

Après exécution, vérifier:
1. `rate_confirmations_unknown.json` est vide `[]`
2. Nombre de documents traités correspond au nombre de PDFs
3. Valeurs de `flat`/`rate` sont des montants réalistes
4. `fund_type` est toujours USD ou CAD
5. Les dates sont au format ISO (YYYY-MM-DD)

## Historique des améliorations

- **v1**: Parsing basique avec pdfplumber tables (échoué - pas de vraies tables)
- **v2**: Parsing par regex sur texte extrait
- **v3**: Normalisation des valeurs monétaires avec espaces
- **v4**: Correction extraction fund_type après normalisation
- **v5**: Support multi-types (FTL, LTL, Shunting, Accessorial)
- **v6**: Support bilingue (anglais/français)
- **v7**: Extraction date depuis filename comme fallback
- **v8**: Extraction location pour Shunting
- **v9**: Correction décimales RPM (`2 .70` → `2.70`)
- **v10**: Tracking des types inconnus (rate_confirmations_unknown.json toujours créé)
- **v11**: Support chemins longs Windows (> 260 chars) via `get_extended_path()` avec préfixe `\\?\`
- **v11.1**: Fix: Appliquer `get_extended_path()` AVANT la vérification `exists()` pour traiter les fichiers avec chemins longs
