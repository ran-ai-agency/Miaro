"""
Parse C.A.T. Inc. Rate Confirmation PDF documents and extract structured data.
Outputs parsed data to separate JSON files by service type:
- FTL (Full Truckload): Standard lane-based rates
- LTL (Less-than-Truckload): Rates by number of skids
- Shunting: Hourly service rates
- Accessorial: Monthly fees and other charges

These PDFs contain formatted text (not actual tables), so we parse line by line
using regex patterns.
"""

import json
import re
import os
from pathlib import Path
from datetime import datetime

try:
    import pdfplumber
except ImportError:
    print("Installing pdfplumber...")
    import subprocess
    subprocess.check_call(["pip", "install", "pdfplumber"])
    import pdfplumber


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def parse_date(date_str: str) -> str:
    """Parse date string to ISO format."""
    date_str = date_str.strip()

    formats = [
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%d",
        "%d/%m/%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    return date_str


def extract_date_from_filename(filename: str) -> str | None:
    """
    Extract date from filename if it starts with a date pattern.

    Examples:
    - "2022-01-24 Rate Confirmation Monthly trailer detention.pdf" -> "2022-01-24"
    - "2021-08-10 Welton Group.pdf" -> "2021-08-10"
    """
    # Pattern: YYYY-MM-DD at start of filename
    match = re.match(r'^(\d{4}-\d{2}-\d{2})', filename)
    if match:
        return match.group(1)

    # Pattern: YYYY/MM/DD at start
    match = re.match(r'^(\d{4})/(\d{2})/(\d{2})', filename)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

    return None


def parse_currency_value(value_str: str) -> float | None:
    """Parse currency string like '$2,265' or '$ 575' to float."""
    if not value_str or value_str.strip() in ['-', '']:
        return None

    cleaned = re.sub(r'[$,\s]', '', value_str.strip())

    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_miles(miles_str: str) -> int | None:
    """Parse miles string to integer."""
    if not miles_str or miles_str.strip() in ['-', '']:
        return None

    cleaned = re.sub(r'[,\s]', '', miles_str.strip())

    try:
        return int(float(cleaned))
    except ValueError:
        return None


def normalize_line_text(text: str) -> str:
    """
    Normalize text that has strange spacing from PDF extraction.
    Examples:
    - "$ 8 75" -> "$875"
    - "$ 1 ,300" -> "$1,300"
    - "$ 2 .70" -> "$2.70"
    - "1 ,132" -> "1,132"
    """
    def fix_currency(match):
        parts = match.group(0)
        normalized = re.sub(r'\s+', '', parts)
        return normalized

    # Match $ followed by digits/spaces/commas/periods until USD/CAD
    text = re.sub(r'\$ [\d\s,\.]+(?= (?:USD|CAD))', fix_currency, text)
    text = re.sub(r'\$[\d\s,\.]+(?=\s*(?:USD|CAD))', fix_currency, text)

    # Fix decimal numbers with space before decimal point: "2 .70" -> "2.70"
    text = re.sub(r'(\d)\s+\.(\d)', r'\1.\2', text)

    # Fix standalone numbers with spaces before commas
    text = re.sub(r'(\d)\s+,\s*(\d)', r'\1,\2', text)

    # Fix miles: digit groups separated by spaces before $
    text = re.sub(r'(\d+)\s+(\d+)(?=\s+\$)', r'\1\2', text)

    return text


# =============================================================================
# SERVICE TYPE DETECTION
# =============================================================================

def detect_service_type(text: str) -> tuple[str, bool]:
    """
    Detect the type of service from PDF text.
    Returns: (type, is_confident)
        - type: 'FTL', 'LTL', 'Shunting', 'Accessorial', or 'Unknown'
        - is_confident: True if we matched a known pattern, False if defaulted

    Known types:
    1. FTL (Full Truckload): Standard lane-based rates with Origin/Destination/Miles/Flat
    2. LTL (Less-than-Truckload): Rates by number of skids/palettes
    3. Shunting: Hourly service rates for yard operations
    4. Accessorial: Monthly fees, detention charges, etc.

    Priority:
    1. Check for standard FTL lane headers first (Origin/Destination columns)
    2. Then check for LTL (skids-based)
    3. Then check for Shunting (hourly)
    4. Then check for Accessorial (monthly fees)
    5. If no match, return 'Unknown' for investigation
    """
    text_lower = text.lower()

    # Split text to check header area vs notes area
    # Notes section shouldn't influence classification
    notes_start = text_lower.find('notes:')
    header_text = text_lower[:notes_start] if notes_start > 0 else text_lower

    # Check for standard FTL lane headers FIRST
    # If we have Origin/Destination columns with Miles/Flat/Line Haul, it's FTL
    # Support both English and French headers
    has_origin_dest = ('origin' in header_text or 'origine' in header_text) and ('destination' in header_text or 'stops' in header_text)
    has_lane_rate = ('line haul' in header_text or 'flat' in header_text or
                     ('miles' in header_text and 'fund type' in header_text) or
                     'devise' in header_text)  # French: "Devise" = Fund Type

    if has_origin_dest and has_lane_rate:
        return ('FTL', True)

    # Check for LTL indicators
    if '# of skids' in header_text or 'number of skids' in header_text:
        return ('LTL', True)
    if 'less-than-truckload' in header_text or ('ltl' in header_text and 'equipment' in header_text):
        return ('LTL', True)

    # Check for Shunting indicators
    if 'shunting' in header_text:
        return ('Shunting', True)
    if 'rate per hour' in header_text or '/hrs' in header_text:
        return ('Shunting', True)
    if 'service description' in header_text and 'type' in header_text:
        return ('Shunting', True)

    # Check for Accessorial indicators (only in header, not notes)
    if 'accessorial' in header_text and 'per month' in header_text:
        return ('Accessorial', True)
    if 'per month' in header_text and 'fund type' in header_text:
        return ('Accessorial', True)
    if 'trailer detention' in header_text:
        return ('Accessorial', True)

    # No confident match - return Unknown for investigation
    # The caller can decide whether to default to FTL or flag for review
    return ('Unknown', False)


# =============================================================================
# HEADER EXTRACTION
# =============================================================================

def extract_header_from_text(text: str) -> dict:
    """Extract header information from PDF text. Supports English and French."""
    header = {
        "date": None,
        "customer": None,
        "equipment": None,
        "service_type": None
    }

    lines = text.split('\n')

    for line in lines:
        line = line.strip()
        line_lower = line.lower()

        # Date (English: "Date:", French: "Date:")
        if line_lower.startswith('date:') or line_lower.startswith('effective period'):
            match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', line)
            if match:
                header["date"] = parse_date(match.group(1))

        # Customer (English: "Customer", French: "Client")
        elif line_lower.startswith('customer') or line_lower.startswith('client'):
            match = re.search(r'(?:customer|client)\s*:?\s*(.+)', line, re.IGNORECASE)
            if match:
                header["customer"] = match.group(1).strip()

        # Equipment (English: "Equipment", French: "Équipement")
        elif line_lower.startswith('equipment') or line_lower.startswith('équipement') or line_lower.startswith('equipement'):
            match = re.search(r'(?:equipment|équipement|equipement)\s*:?\s*(.+)', line, re.IGNORECASE)
            if match:
                header["equipment"] = match.group(1).strip()

        # Service type (English: "Service type", French: "Type de service")
        elif line_lower.startswith('service type') or line_lower.startswith('type de service'):
            match = re.search(r'(?:service type|type de service)\s*:?\s*(.+)', line, re.IGNORECASE)
            if match:
                val = match.group(1).strip()
                if val:
                    header["service_type"] = val

    return header


def extract_notes_from_text(text: str) -> str:
    """Extract notes section from PDF text."""
    match = re.search(r'Notes:\s*(.+?)(?:$)', text, re.DOTALL | re.IGNORECASE)
    if match:
        notes = match.group(1).strip()
        notes = re.sub(r'4 Rue du Transport.*?Fax:\s*\([0-9\-]+\)', '', notes, flags=re.DOTALL)
        notes = re.sub(r'\s+', ' ', notes).strip()
        return notes
    return ""


# =============================================================================
# FTL PARSER (Full Truckload - Lane-based)
# =============================================================================

def detect_column_structure(header_line: str) -> list[str]:
    """Detect which columns are present in the document. Supports English and French."""
    header_lower = header_line.lower()
    columns = []

    # Origin (English: Origin, French: Origine)
    if 'origin' in header_lower or 'origine' in header_lower:
        columns.append('origin')

    # Destination (same in both languages)
    if 'destination' in header_lower or 'stops' in header_lower:
        columns.append('destination')

    # Miles (English: Miles, French: IM = Intermodal Miles, OTR = Over The Road)
    if 'miles' in header_lower or 'fsc miles' in header_lower or ' im ' in header_lower or ' otr' in header_lower:
        columns.append('miles')

    # Mode
    if 'mode' in header_lower:
        columns.append('mode')

    # RPM (Rate Per Mile)
    if 'rpm' in header_lower:
        columns.append('rpm')

    # Flat rate (English: Flat/Line Haul, French: Taux/Tarif)
    if 'flat' in header_lower or 'line haul' in header_lower or 'linehaul' in header_lower or 'taux' in header_lower or 'tarif' in header_lower:
        columns.append('flat')

    # Minimum
    if 'min.' in header_lower or 'min ' in header_lower:
        columns.append('min')

    # Fund type (English: Fund Type, French: Devise)
    if 'fund type' in header_lower or 'devise' in header_lower:
        columns.append('fund_type')

    return columns


def parse_lane_line_regex(line: str, has_mode: bool = False, has_rpm: bool = False, has_min: bool = False) -> dict | None:
    """Parse a FTL lane line using regex."""
    line = normalize_line_text(line)

    location_pattern = r'([A-Za-z][A-Za-z\s\.\-\']+,\s*[A-Z]{2})\b'
    locations = re.findall(location_pattern, line)

    if len(locations) < 2:
        return None

    origin = locations[0].strip()
    destination = locations[1].strip()

    dest_end = line.rfind(destination) + len(destination)
    remainder = line[dest_end:].strip()

    lane = {
        "origin": origin,
        "destination": destination,
        "miles": None,
        "mode": None,
        "rpm": None,
        "flat": None,
        "fund_type": None
    }

    fund_match = re.search(r'(USD|CAD)\s*$', remainder) or re.search(r'(USD|CAD)\b', remainder)
    if fund_match:
        lane["fund_type"] = fund_match.group(1)
        remainder = remainder[:fund_match.start()] + remainder[fund_match.end():]

    mode_match = re.search(r'\b(DV|FB|DRY|REEFER|FLATBED)\b', remainder, re.IGNORECASE)
    if mode_match:
        lane["mode"] = mode_match.group(1).upper()
        remainder = remainder[:mode_match.start()] + remainder[mode_match.end():]

    currency_pattern = r'\$\s*([\d,]+(?:\.\d+)?)'
    currency_matches = re.findall(currency_pattern, remainder)
    currency_values = [f"${m}" for m in currency_matches]

    remainder_no_currency = re.sub(r'\$\s*[\d,]+(?:\.\d+)?', ' ', remainder)

    miles_candidates = re.findall(r'\b([\d,]+)\b', remainder_no_currency)
    for candidate in miles_candidates:
        miles_val = parse_miles(candidate)
        if miles_val is not None and miles_val >= 1:
            lane["miles"] = miles_val
            break

    if has_rpm and (has_min or 'min' in str(has_min).lower()) and len(currency_values) >= 2:
        lane["rpm"] = parse_currency_value(currency_values[0])
        lane["flat"] = parse_currency_value(currency_values[1])
    elif len(currency_values) >= 1:
        lane["flat"] = parse_currency_value(currency_values[-1])
        if has_rpm and len(currency_values) >= 2:
            lane["rpm"] = parse_currency_value(currency_values[0])

    return lane


def parse_ftl_lanes(text: str) -> list[dict]:
    """Parse FTL (Full Truckload) lanes from text."""
    lines = text.split('\n')
    columns = []
    in_data_section = False
    all_lanes = []

    for line in lines:
        line_stripped = line.strip()

        if not line_stripped:
            continue

        # Detect header row (English: Origin/Destination, French: Origine/Destination)
        if ('Origin' in line or 'Origine' in line) and ('Destination' in line or 'Stops' in line):
            columns = detect_column_structure(line)
            in_data_section = True
            continue

        # Stop at Notes section (English: Notes, French: Notes/Remarques)
        if line_stripped.startswith('Notes:') or line_stripped.startswith('Remarques:'):
            break

        if not in_data_section:
            continue

        # Skip non-data lines (both English and French headers)
        if any(x in line_stripped for x in ['C.A.T.', 'Rate Confirmation', 'Confirmation de Taux',
                                            '4 Rue du Transport', 'Coteau du Lac', 'Tel:', 'Fax:', 'J0P 1B0',
                                            'Date:', 'Customer:', 'Client:', 'Equipment:', 'Équipement:',
                                            'Service type:', 'Type de service:']):
            continue

        has_mode = 'mode' in columns
        has_rpm = 'rpm' in columns
        has_min = 'min' in columns

        lane = parse_lane_line_regex(line, has_mode=has_mode, has_rpm=has_rpm, has_min=has_min)

        if lane and lane.get('origin') and lane.get('destination'):
            if lane['flat'] is not None or lane.get('rpm') is not None:
                final_lane = {
                    "origin": lane['origin'],
                    "destination": lane['destination'],
                    "miles": lane['miles'],
                    "mode": lane['mode'],
                    "flat": lane['flat'],
                    "fund_type": lane['fund_type']
                }
                if lane.get('rpm') is not None:
                    final_lane['rpm'] = lane['rpm']
                all_lanes.append(final_lane)

    return all_lanes


# =============================================================================
# LTL PARSER (Less-than-Truckload - Skid-based)
# =============================================================================

def parse_ltl_rates(text: str) -> dict:
    """
    Parse LTL (Less-than-Truckload) rates from text.
    Returns: {origin, destination, rates: [{skids, rate, fund_type}]}

    First data line contains: Origin, Destination, first skid count, rate, fund_type
    Subsequent lines contain: skid count, rate, fund_type
    """
    result = {
        "origin": None,
        "destination": None,
        "rates": []
    }

    lines = text.split('\n')
    in_data_section = False
    first_data_line = True

    for line in lines:
        line_stripped = line.strip()

        # Detect header row with "# of Skids"
        if '# of Skids' in line or 'Number of Skids' in line:
            in_data_section = True
            continue

        if line_stripped.startswith('Notes:'):
            break

        if not in_data_section:
            continue

        # Normalize the line
        line_normalized = normalize_line_text(line_stripped)

        # First data line: "Calgary, AB Richmond BC 1 $ 250.00 CAD"
        # Contains origin, destination, and first rate
        if first_data_line:
            # Look for locations (City, ST pattern)
            location_pattern = r'([A-Za-z][A-Za-z\s\.\-\']+,?\s*[A-Z]{2})\b'
            locations = re.findall(location_pattern, line_normalized)

            if len(locations) >= 2:
                result["origin"] = locations[0].strip()
                result["destination"] = locations[1].strip()

                # Extract the rate part after the locations
                # Find position after destination
                dest_pos = line_normalized.rfind(locations[1])
                if dest_pos >= 0:
                    remainder = line_normalized[dest_pos + len(locations[1]):].strip()

                    # Pattern: skid count, then price, then currency
                    # e.g., "1 $ 250.00 CAD" or "1 $250.00 CAD"
                    match = re.match(r'^(\d+)\s+\$\s*([\d,\.]+)\s*(USD|CAD)?', remainder)
                    if match:
                        skids = match.group(1)
                        rate = parse_currency_value(f"${match.group(2)}")
                        fund_type = match.group(3) or "CAD"

                        result["rates"].append({
                            "skids": skids,
                            "rate": rate,
                            "fund_type": fund_type
                        })

                first_data_line = False
                continue

        # Subsequent lines: "2 $ 345.00 CAD" or "14-26 $ 1,410.00 CAD"
        match = re.match(r'^(\d+(?:-\d+)?)\s+\$\s*([\d,\.]+)\s*(USD|CAD)?', line_normalized)
        if match:
            skids = match.group(1)
            rate = parse_currency_value(f"${match.group(2)}")
            fund_type = match.group(3) or "CAD"

            result["rates"].append({
                "skids": skids,
                "rate": rate,
                "fund_type": fund_type
            })

    return result


# =============================================================================
# SHUNTING PARSER (Hourly service)
# =============================================================================

def extract_shunting_location(description: str, source_file: str) -> str | None:
    """
    Extract the service location from description or source file name.

    Examples:
    - "Shunting service - Salaberry-de-Valleyfield's plant dedicated service" -> "Salaberry-de-Valleyfield"
    - "2021-01-01 Rate confirmation Shunting from Valleyfield 2021.pdf" -> "Valleyfield"
    """
    location = None

    # Try to extract from description first (more specific)
    # Pattern: Look for location after "Shunting service -" or before "'s plant"
    if description:
        # Pattern 1: "Shunting service - LocationName's plant"
        match = re.search(r'[Ss]hunting\s+service\s*-\s*([A-Za-z\-]+(?:\'s)?)', description)
        if match:
            location = match.group(1).replace("'s", "").strip()

        # Pattern 2: Look for city name before "plant" or "facility"
        if not location:
            match = re.search(r'([A-Za-z][A-Za-z\-\s]+?)(?:\'s)?\s+(?:plant|facility|yard|terminal)', description, re.IGNORECASE)
            if match:
                location = match.group(1).strip()

    # If not found in description, try source file name
    if not location and source_file:
        # Pattern: "Shunting from LocationName" in filename
        match = re.search(r'[Ss]hunting\s+(?:from\s+)?([A-Za-z][A-Za-z\-]+)', source_file)
        if match:
            location = match.group(1).strip()

    return location


def parse_shunting_services(text: str, source_file: str = "") -> list[dict]:
    """
    Parse Shunting service rates from text.
    Returns: [{description, location, rate_type, rate, fund_type}]
    """
    services = []
    lines = text.split('\n')
    in_data_section = False

    for line in lines:
        line_stripped = line.strip()

        # Detect header row
        if 'Service description' in line or 'Rate per hour' in line:
            in_data_section = True
            continue

        if line_stripped.startswith('Notes:'):
            break

        if not in_data_section:
            continue

        # Skip empty or header lines
        if not line_stripped or any(x in line_stripped for x in ['C.A.T.', 'Rate Confirmation',
                                                                  'Date:', 'Customer:', 'Equipment:']):
            continue

        line = normalize_line_text(line_stripped)

        # Pattern: description, then rate with /hrs, then currency
        # Example: "Shunting service - Salaberry-de-Valleyfield's plant dedicated service $ 62.50/hrs CAD"
        match = re.search(r'(.+?)\s+\$\s*([\d,\.]+)(?:/hrs?)?\s*(USD|CAD)?', line)
        if match:
            description = match.group(1).strip()
            rate = parse_currency_value(f"${match.group(2)}")
            fund_type = match.group(3) or "CAD"

            # Determine rate type
            rate_type = "hourly"
            if '/hrs' in line.lower() or 'per hour' in line.lower():
                rate_type = "hourly"

            # Extract location from description or source file
            location = extract_shunting_location(description, source_file)

            services.append({
                "description": description,
                "location": location,
                "rate_type": rate_type,
                "rate": rate,
                "fund_type": fund_type
            })

    return services


# =============================================================================
# ACCESSORIAL PARSER (Monthly fees, detention, etc.)
# =============================================================================

def parse_accessorial_charges(text: str) -> list[dict]:
    """
    Parse Accessorial charges from text.
    Returns: [{description, period, rate, fund_type}]
    """
    charges = []
    lines = text.split('\n')
    in_data_section = False

    for line in lines:
        line_stripped = line.strip()

        # Detect header row
        if 'Accessorial' in line or 'PER MONTH' in line:
            in_data_section = True
            continue

        if line_stripped.startswith('Notes:'):
            break

        if not in_data_section:
            continue

        # Skip empty or header lines
        if not line_stripped or any(x in line_stripped for x in ['C.A.T.', 'Rate Confirmation',
                                                                  'Date:', 'Customer:', 'Equipment:']):
            continue

        line = normalize_line_text(line_stripped)

        # Pattern: description, then rate, then currency
        match = re.search(r'(.+?)\s+\$\s*([\d,\.]+)\s*(USD|CAD)?', line)
        if match:
            description = match.group(1).strip()
            rate = parse_currency_value(f"${match.group(2)}")
            fund_type = match.group(3) or "CAD"

            # Determine period
            period = "monthly"
            if 'per month' in text.lower() or 'monthly' in text.lower():
                period = "monthly"
            elif 'per day' in text.lower() or 'daily' in text.lower():
                period = "daily"

            charges.append({
                "description": description,
                "period": period,
                "rate": rate,
                "fund_type": fund_type
            })

    return charges


# =============================================================================
# MAIN PDF PARSER
# =============================================================================

def parse_pdf(pdf_path: str) -> tuple[dict, bool]:
    """
    Parse a single PDF file and extract rate confirmation data.

    Returns: (result_dict, is_confident)
        - result_dict: The parsed data
        - is_confident: True if service type was confidently detected
    """
    result = {
        "source_file": os.path.basename(pdf_path),
        "date": None,
        "customer": None,
        "equipment": None,
        "service_type": None,
        "doc_type": None,
        "notes": "",
        "raw_text_preview": ""  # For debugging unknown types
    }

    all_text = ""

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            all_text += page_text + "\n"

    # Extract header info
    header_info = extract_header_from_text(all_text)
    result.update(header_info)

    # If date not found in content, try to extract from filename
    if not result["date"]:
        result["date"] = extract_date_from_filename(result["source_file"])

    # Extract notes
    result["notes"] = extract_notes_from_text(all_text)

    # Detect document type
    doc_type, is_confident = detect_service_type(all_text)
    result["doc_type"] = doc_type

    # If unknown type, store text preview for investigation
    if not is_confident:
        # Store first 1000 chars for debugging
        result["raw_text_preview"] = all_text[:1000]
        # Try FTL parsing as fallback
        doc_type = 'FTL'
        result["doc_type"] = doc_type

    # Parse based on document type
    if doc_type == 'FTL':
        result["lanes"] = parse_ftl_lanes(all_text)
    elif doc_type == 'LTL':
        ltl_data = parse_ltl_rates(all_text)
        result["origin"] = ltl_data["origin"]
        result["destination"] = ltl_data["destination"]
        result["rates"] = ltl_data["rates"]
    elif doc_type == 'Shunting':
        result["services"] = parse_shunting_services(all_text, result["source_file"])
    elif doc_type == 'Accessorial':
        result["charges"] = parse_accessorial_charges(all_text)

    # Remove raw_text_preview if type was confident (not needed)
    if is_confident and "raw_text_preview" in result:
        del result["raw_text_preview"]

    return result, is_confident


def main():
    """Main function to parse all PDF files and output JSON by type."""
    script_dir = Path(__file__).parent.parent
    pdf_dir = script_dir

    pdf_files = list(pdf_dir.glob("*.pdf"))

    if not pdf_files:
        print(f"No PDF files found in {pdf_dir}")
        return

    print(f"Found {len(pdf_files)} PDF files to process")

    # Separate results by type
    results_by_type = {
        'FTL': [],
        'LTL': [],
        'Shunting': [],
        'Accessorial': []
    }

    # Track unknown/unconfident types for investigation
    unknown_types = []

    for pdf_path in sorted(pdf_files):
        print(f"Processing: {pdf_path.name}")
        try:
            result, is_confident = parse_pdf(str(pdf_path))
            doc_type = result['doc_type']
            results_by_type[doc_type].append(result)

            # Flag if type detection was not confident
            confidence_marker = "" if is_confident else " [UNKNOWN TYPE - DEFAULTED TO FTL]"
            print(f"  - Type: {doc_type}{confidence_marker}, Date: {result['date']}, Customer: {result['customer']}")

            if not is_confident:
                unknown_types.append({
                    "file": pdf_path.name,
                    "customer": result.get('customer'),
                    "equipment": result.get('equipment'),
                    "raw_text_preview": result.get('raw_text_preview', '')[:500]
                })

            # Print count based on type
            if doc_type == 'FTL':
                print(f"  - Extracted {len(result.get('lanes', []))} lanes")
            elif doc_type == 'LTL':
                print(f"  - Extracted {len(result.get('rates', []))} rate tiers")
            elif doc_type == 'Shunting':
                print(f"  - Extracted {len(result.get('services', []))} services")
            elif doc_type == 'Accessorial':
                print(f"  - Extracted {len(result.get('charges', []))} charges")

        except Exception as e:
            print(f"  - ERROR: {e}")
            import traceback
            traceback.print_exc()

    # Output separate JSON files
    print("\n" + "="*60)
    print("OUTPUT FILES:")
    print("="*60)

    for doc_type, results in results_by_type.items():
        if results:
            output_path = script_dir / f"rate_confirmations_{doc_type.lower()}.json"
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(results, f, indent=2, ensure_ascii=False)

            # Count items
            if doc_type == 'FTL':
                total_items = sum(len(r.get('lanes', [])) for r in results)
                item_name = "lanes"
            elif doc_type == 'LTL':
                total_items = sum(len(r.get('rates', [])) for r in results)
                item_name = "rate tiers"
            elif doc_type == 'Shunting':
                total_items = sum(len(r.get('services', [])) for r in results)
                item_name = "services"
            elif doc_type == 'Accessorial':
                total_items = sum(len(r.get('charges', [])) for r in results)
                item_name = "charges"

            print(f"  {doc_type}: {output_path.name}")
            print(f"    - {len(results)} documents, {total_items} {item_name}")

    # Always save unknown types file (empty or not) for tracking
    unknown_path = script_dir / "rate_confirmations_unknown.json"
    with open(unknown_path, 'w', encoding='utf-8') as f:
        json.dump(unknown_types, f, indent=2, ensure_ascii=False)

    # Report unknown types if any
    if unknown_types:
        print("\n" + "="*60)
        print("WARNING: UNKNOWN SERVICE TYPES DETECTED")
        print("="*60)
        print(f"Found {len(unknown_types)} document(s) with unrecognized format.")
        print("These were defaulted to FTL but may need a new parser.")
        print("")
        print(f"Details saved to: {unknown_path.name}")
        print("")
        for item in unknown_types:
            print(f"  - {item['file']}")
            print(f"    Customer: {item['customer']}, Equipment: {item['equipment']}")

    print("\nProcessing complete!")


if __name__ == "__main__":
    main()
