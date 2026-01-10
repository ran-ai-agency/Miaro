# Directive: Parse Rate Confirmation PDFs

## Objectif
Extraire les données structurées des documents PDF de confirmation de tarifs C.A.T. Inc. et les convertir en formats exploitables (JSON et Excel).

## Contexte
Les documents "Rate Confirmation" sont soumis par l'équipe de tarification C.A.T. Chaque document contient:
- Des métadonnées (date, client, équipement)
- Une liste de lanes (routes) avec origine, destination, distance et tarifs
- Des notes sur les conditions tarifaires

## Inputs
- Fichiers PDF dans le répertoire racine du projet (`*.pdf`)
- Format attendu: Documents C.A.T. Inc. Rate Confirmation

## Scripts d'exécution

### 1. Parser PDF → JSON
```bash
python execution/parse_rate_confirmations.py
```

**Output:** `rate_confirmations.json`

### 2. Convertir JSON → Excel
```bash
python execution/json_to_excel.py
```

**Output:** `rate_confirmations.xlsx`

## Structure des données extraites

### Niveau Document
| Champ | Description | Exemple |
|-------|-------------|---------|
| `source_file` | Nom du fichier PDF source | `2025-12-03 Laredo Inboud.pdf` |
| `date` | Date de la confirmation (ISO) | `2025-12-03` |
| `customer` | Nom du client | `TIMSA` |
| `equipment` | Type d'équipement | `Dry van` |
| `service_type` | Type de service (optionnel) | `Multi-stop lane` |
| `notes` | Notes et conditions | `Fuel surcharge included...` |

### Niveau Lane (Route)
| Champ | Description | Exemple |
|-------|-------------|---------|
| `origin` | Ville et état/province d'origine | `Duncan, SC` |
| `destination` | Ville et état/province de destination | `Laredo, TX` |
| `miles` | Distance en miles | `1311` |
| `mode` | Mode de transport (optionnel) | `DV`, `FB`, `DRY`, `REEFER`, `FLATBED` |
| `flat` | Tarif forfaitaire (Line Haul) | `2265.0` |
| `rpm` | Tarif par mile (optionnel) | `2.70` |
| `fund_type` | Devise | `USD` ou `CAD` |

## Particularités du parsing

### Variations de colonnes dans les PDFs
Les en-têtes de colonnes varient selon les documents:
- `Flat` / `Line Haul` / `Linehaul` / `Min.` → tous mappés vers `flat`
- `Miles` / `FSC Miles` → mappé vers `miles`
- `Destination` / `Stops` → mappé vers `destination`
- `Mode` / `MODE` → mappé vers `mode`

### Problèmes d'extraction de texte PDF
Le parsing gère automatiquement:
- Espaces dans les montants: `$ 8 75` → `$875`
- Espaces dans les nombres: `1 ,132` → `1,132`
- Documents multi-pages (ex: Cascades avec 56 lanes)

### Modes de transport reconnus
- `DV` - Dry Van
- `FB` - Flatbed
- `DRY` - Dry
- `REEFER` - Réfrigéré
- `FLATBED` - Flatbed

## Workflow complet

```bash
# 1. Placer les PDFs dans le répertoire racine

# 2. Parser tous les PDFs
python execution/parse_rate_confirmations.py

# 3. Convertir en Excel
python execution/json_to_excel.py

# 4. Vérifier les résultats
# - rate_confirmations.json (données structurées)
# - rate_confirmations.xlsx (tableau Excel)
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

### Non gérés
- PDFs scannés (images) - nécessiterait OCR
- Formats de documents non-C.A.T.
- Langues autres que l'anglais/français

## Validation

Après exécution, vérifier:
1. Nombre de documents traités correspond au nombre de PDFs
2. Nombre de lanes semble cohérent
3. Valeurs de `flat` sont des montants réalistes (pas 1.0, 2.0, etc.)
4. `fund_type` est toujours USD ou CAD

## Historique des améliorations

- **v1**: Parsing basique avec pdfplumber tables (échoué - pas de vraies tables)
- **v2**: Parsing par regex sur texte extrait
- **v3**: Normalisation des valeurs monétaires avec espaces
- **v4**: Correction extraction fund_type après normalisation
