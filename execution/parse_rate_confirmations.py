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

def extract_customer_from_path(pdf_path: str) -> str | None:
    """
    Extract customer name from the file path.

    Production folder structure:
    C:/Users/mranaivoarison/C.A.T. Inc/C.A.T. Files Server - Customer_Files/Customers/[CUSTOMER]/[filename].pdf

    The customer is the parent folder of the PDF file.
    Returns None if the path doesn't match expected structure.
    """
    path = Path(pdf_path)

    # The customer folder is the parent of the PDF file
    parent_folder = path.parent.name

    # Skip if parent is the root PDF folder (not in a customer subfolder)
    # Check if we're in the "Customers" structure
    grandparent = path.parent.parent.name if path.parent.parent else None

    # If parent folder looks like a customer name (not a system folder)
    # and grandparent contains "Customers" or other expected folders, use parent as customer
    if parent_folder and grandparent:
        if 'Customers' in grandparent or 'Bids' in grandparent or 'Archive' in grandparent:
            return parent_folder

    # Fallback: if parent folder is not a date pattern or system folder, use it
    if parent_folder and not re.match(r'^\d{4}-\d{2}-\d{2}', parent_folder):
        # Skip common system folders
        skip_folders = {'Documents', 'OneDrive', 'Users', 'C.A.T. Inc',
                        'C.A.T. Files Server - Customer_Files', 'Customers', 'Customer_Files'}
        if parent_folder not in skip_folders:
            return parent_folder

    return None


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

def extract_company_name(text: str) -> str:
    """
    Extract the company name (C.A.T. or SLH) from PDF text.
    The company name typically appears in the header/letterhead of the first page.

    IMPORTANT: SLH documents may have C.A.T. as the CUSTOMER name, so we must
    check for SLH Transport in the letterhead FIRST.

    Returns: "C.A.T." or "SLH" or "Unknown"
    """
    text_upper = text.upper()

    # Check for SLH FIRST - SLH documents have "SLH Transport" in their letterhead
    # This must be checked before C.A.T. because some SLH docs have C.A.T. as customer
    if 'SLH TRANSPORT' in text_upper or 'O/A SLH TRANSPORT' in text_upper:
        return "SLH"

    # Also check for SLH address/website which is unique to SLH docs
    if 'WWW.SLH.CA' in text_upper or '1585 CENTENNIAL DRIVE' in text_upper:
        return "SLH"

    # Check for C.A.T. variations (company letterhead patterns)
    if 'C.A.T.' in text or 'C.A.T' in text_upper or 'CAT INC' in text_upper or 'CAT GLOBAL' in text_upper:
        return "C.A.T."

    # If no match found
    return "Unknown"


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
# SLH PARSER (SLH Transport Inc. - Rate Quotation)
# =============================================================================

def parse_slh_text_date(date_str: str) -> str | None:
    """
    Parse SLH date formats like "JANUARY 1ST, 2019" or "MAY 1, 2019" to ISO format.
    """
    if not date_str:
        return None

    date_str = date_str.strip().upper()

    # Remove ordinal suffixes (ST, ND, RD, TH)
    date_str = re.sub(r'(\d+)(ST|ND|RD|TH)', r'\1', date_str)

    # Try various formats
    formats = [
        "%B %d, %Y",      # JANUARY 1, 2019
        "%B %d %Y",       # JANUARY 1 2019
        "%d %B %Y",       # 1 JANUARY 2019
        "%d %B, %Y",      # 1 JANUARY, 2019
        "%m/%d/%Y",       # 01/01/2019
        "%Y-%m-%d",       # 2019-01-01
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None


def extract_slh_header(text: str, source_file: str) -> dict:
    """
    Extract header information from SLH Rate Quotation document.

    Returns dict with:
    - customer: Company name
    - acct_code: Account code
    - address: Customer address
    - effective_date: Start date
    - expiry_date: End date
    - account_manager: VP of Sales name
    - currency: CAD (default, from notes)
    """
    header = {
        "customer": None,
        "acct_code": None,
        "address": None,
        "effective_date": None,
        "expiry_date": None,
        "account_manager": None,
        "currency": "CAD",  # Default for SLH
    }

    lines = text.split('\n')

    for i, line in enumerate(lines):
        line_stripped = line.strip()
        line_upper = line_stripped.upper()

        # Company Name: "COMPANY NAME: PIVAL INTERNATIONAL ACCT CODE: PIVM6"
        if 'COMPANY NAME:' in line_upper:
            # May have ACCT CODE on same line or separate
            match = re.search(r'COMPANY NAME:\s*(.+?)(?:\s+ACCT CODE:|$)', line_stripped, re.IGNORECASE)
            if match:
                header["customer"] = match.group(1).strip()

            # ACCT CODE on same line
            acct_match = re.search(r'ACCT CODE:\s*(\S+)', line_stripped, re.IGNORECASE)
            if acct_match:
                header["acct_code"] = acct_match.group(1).strip()

        # Address line (usually after COMPANY NAME)
        elif 'ADDRESS:' in line_upper:
            match = re.search(r'ADDRESS:\s*(.+?)(?:\s+ACCT CODE:|$)', line_stripped, re.IGNORECASE)
            if match:
                header["address"] = match.group(1).strip()

        # Effective Date: "EFFECTIVE DATE: JANUARY 1ST, 2019"
        elif 'EFFECTIVE DATE:' in line_upper:
            match = re.search(r'EFFECTIVE DATE:\s*(.+?)(?:\s+CUSTOMER|$)', line_stripped, re.IGNORECASE)
            if match:
                header["effective_date"] = parse_slh_text_date(match.group(1).strip())

        # Expiry Date: "EXPIRY DATE: DECEMBER 31ST, 2019"
        elif 'EXPIRY DATE:' in line_upper:
            match = re.search(r'EXPIRY DATE:\s*(.+?)(?:\s*\(|$)', line_stripped, re.IGNORECASE)
            if match:
                header["expiry_date"] = parse_slh_text_date(match.group(1).strip())

        # Account Manager - Look for "CUSTOMER PLEASE PRINT NAME" and get the name on the next line
        elif 'CUSTOMER PLEASE PRINT NAME' in line_upper:
            # The signatory name is on the next line
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                # Verify it's a name (not a title or other content)
                if next_line and not any(x in next_line.upper() for x in [
                    'VP ', 'VICE ', 'PRESIDENT', 'MANAGER', 'DIRECTOR', 'SALES',
                    'NOTES:', 'EFFECTIVE', 'EXPIRY', 'PAGE ', 'CUSTOMER'
                ]):
                    header["account_manager"] = next_line

    # Extract document date from filename if not found in content
    if not header["effective_date"]:
        date_match = re.match(r'^(\d{4}-\d{2}-\d{2})', source_file)
        if date_match:
            header["effective_date"] = date_match.group(1)

    return header


def extract_slh_notes(text: str) -> str:
    """Extract notes section from SLH document."""
    match = re.search(r'NOTES:\s*(.+?)(?:EFFECTIVE DATE:|$)', text, re.DOTALL | re.IGNORECASE)
    if match:
        notes = match.group(1).strip()
        # Clean up: remove extra whitespace, keep line items
        notes = re.sub(r'\n\s*', ' ', notes)
        notes = re.sub(r'\s+', ' ', notes).strip()
        return notes
    return ""


def extract_currency_from_notes(notes: str) -> str:
    """
    Extract currency from notes section.

    Looks for patterns like:
    - "QUOTED IN CANADIAN FUNDS" -> CAD
    - "QUOTED IN USD FUNDS" -> USD
    - "QUOTED IN US FUNDS" -> USD
    - "IN CANADIAN DOLLARS" -> CAD
    - "IN US DOLLARS" -> USD

    Returns "CAD" as default if not found.
    """
    if not notes:
        return "CAD"

    notes_upper = notes.upper()

    # Check for USD patterns first (more specific)
    if any(pattern in notes_upper for pattern in [
        'USD FUNDS', 'US FUNDS', 'US DOLLARS', 'USD DOLLARS',
        'AMERICAN FUNDS', 'AMERICAN DOLLARS'
    ]):
        return "USD"

    # Check for CAD patterns
    if any(pattern in notes_upper for pattern in [
        'CANADIAN FUNDS', 'CANADIAN DOLLARS', 'CAD FUNDS', 'CAD DOLLARS'
    ]):
        return "CAD"

    # Default to CAD
    return "CAD"


def parse_slh_lanes_from_table(pdf_path: str) -> list[dict]:
    """
    Parse SLH Rate Quotation lanes using pdfplumber table extraction.
    This is more accurate for documents with proper table structure.

    Returns list of lane dicts, or empty list if no table found.
    """
    lanes = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()

            for table in tables:
                if not table or len(table) < 2:
                    continue

                # Check if this looks like a rate quotation table
                header = table[0]
                if not header:
                    continue

                header_upper = [str(h).upper() if h else '' for h in header]

                # Find column indices
                origin_idx = None
                stop_off_idx = None
                dest_idx = None
                rate_idx = None
                equipment_idx = None
                miles_idx = None
                new_rpm_idx = None
                current_rpm_idx = None
                new_min_charge_idx = None
                # Specific rate columns for Dufresne-style documents
                rate_dry_van_idx = None
                rate_dry_van_lcv_idx = None
                rate_pup_rocky_idx = None
                rate_dry_van_rocky_idx = None
                rate_pup_single_idx = None
                effective_date_idx = None

                for i, h in enumerate(header_upper):
                    # Normalize header (remove newlines)
                    h_clean = ' '.join(h.split())
                    if 'ORIGIN' in h_clean:
                        origin_idx = i
                    elif 'STOP' in h_clean and 'OFF' in h_clean:
                        stop_off_idx = i
                    elif 'DESTINATION' in h_clean:
                        dest_idx = i
                    elif 'MILES' in h_clean:
                        miles_idx = i
                    elif 'NEW' in h_clean and 'RPM' in h_clean:
                        new_rpm_idx = i
                    elif 'CURRENT' in h_clean and 'RPM' in h_clean:
                        current_rpm_idx = i
                    elif 'NEW' in h_clean and 'MINIMUM' in h_clean and 'CHARGE' in h_clean:
                        new_min_charge_idx = i
                    # Specific rate columns (Dufresne-style with multiple rate types)
                    elif 'RATE' in h_clean and 'DRY' in h_clean and 'VAN' in h_clean:
                        if 'NO LCV' in h_clean or '(NO LCV)' in h_clean:
                            # RATE PER DRY VAN (No LCV)
                            rate_dry_van_idx = i
                        elif 'LCV' in h_clean and 'MATCHED' not in h_clean and 'PUP' not in h_clean:
                            # RATE PER DRY VAN LCV
                            rate_dry_van_lcv_idx = i
                        elif 'MATCHED' in h_clean and 'PUP' in h_clean:
                            # RATE PER DRY VAN WHEN MATCHED UP WITH A PUP (ROCKY)
                            rate_dry_van_rocky_idx = i
                    elif 'RATE' in h_clean and 'PUP' in h_clean:
                        if 'ROCKY' in h_clean or 'TWO-PUP' in h_clean or 'TWO PUP' in h_clean:
                            # RATE PER 28' PUP BASED ON ROCKY CONFIGURATION OR TWO-PUP SHIPMENT
                            rate_pup_rocky_idx = i
                        elif 'SINGLE' in h_clean:
                            # RATE PER 28' PUP BASED ON SINGLE PUP SHIPMENT
                            rate_pup_single_idx = i
                    elif 'RATE' in h_clean and 'CURRENT' not in h_clean:
                        # Generic RATE column (fallback)
                        rate_idx = i
                    elif any(x in h_clean for x in ['MODE', 'EQUIPMENT']):
                        equipment_idx = i
                    elif 'EFFECTIVE' in h_clean and 'DATE' in h_clean:
                        effective_date_idx = i

                # Must have at least origin and destination
                if origin_idx is None or dest_idx is None:
                    continue

                # Check if we have any rate source
                has_rate_source = any([
                    rate_idx is not None,
                    new_rpm_idx is not None,
                    new_min_charge_idx is not None,
                    rate_dry_van_idx is not None,
                    rate_dry_van_lcv_idx is not None,
                    rate_pup_rocky_idx is not None,
                    rate_dry_van_rocky_idx is not None,
                    rate_pup_single_idx is not None,
                ])
                if not has_rate_source:
                    continue

                # Helper function to parse rate value
                def parse_rate_value(val_str):
                    """Parse rate value, returning None for NA/empty values."""
                    if not val_str:
                        return None
                    val_str = str(val_str).strip().upper()
                    if val_str in ['NA', 'N/A', '-', '-----', '']:
                        return None
                    rate_match = re.search(r'\$?\s*([\d,]+(?:\.\d+)?)', val_str)
                    if rate_match:
                        return float(rate_match.group(1).replace(',', ''))
                    return None

                # Parse data rows
                for row in table[1:]:
                    # Check minimum row length (at least origin and destination)
                    if not row or len(row) <= max(origin_idx, dest_idx):
                        continue

                    origin = row[origin_idx] if origin_idx is not None and origin_idx < len(row) else None
                    stop_off = row[stop_off_idx] if stop_off_idx is not None and stop_off_idx < len(row) else None
                    destination = row[dest_idx] if dest_idx is not None and dest_idx < len(row) else None
                    equipment = row[equipment_idx] if equipment_idx is not None and equipment_idx < len(row) else None
                    miles_str = row[miles_idx] if miles_idx is not None and miles_idx < len(row) else None

                    # Generic rate columns
                    rate_str = row[rate_idx] if rate_idx is not None and rate_idx < len(row) else None
                    new_rpm_str = row[new_rpm_idx] if new_rpm_idx is not None and new_rpm_idx < len(row) else None
                    new_min_charge_str = row[new_min_charge_idx] if new_min_charge_idx is not None and new_min_charge_idx < len(row) else None

                    # Specific rate columns (Dufresne-style)
                    rate_dry_van_str = row[rate_dry_van_idx] if rate_dry_van_idx is not None and rate_dry_van_idx < len(row) else None
                    rate_dry_van_lcv_str = row[rate_dry_van_lcv_idx] if rate_dry_van_lcv_idx is not None and rate_dry_van_lcv_idx < len(row) else None
                    rate_pup_rocky_str = row[rate_pup_rocky_idx] if rate_pup_rocky_idx is not None and rate_pup_rocky_idx < len(row) else None
                    rate_dry_van_rocky_str = row[rate_dry_van_rocky_idx] if rate_dry_van_rocky_idx is not None and rate_dry_van_rocky_idx < len(row) else None
                    rate_pup_single_str = row[rate_pup_single_idx] if rate_pup_single_idx is not None and rate_pup_single_idx < len(row) else None

                    # Clean up values (remove newlines, normalize)
                    if origin:
                        origin = ' '.join(str(origin).split())
                    if stop_off:
                        stop_off = ' '.join(str(stop_off).split())
                        # Handle NA, N/A, ----- as no stop off
                        if stop_off.upper() in ['NA', 'N/A', '-----', '-']:
                            stop_off = None
                    if destination:
                        destination = ' '.join(str(destination).split())
                    if equipment:
                        equipment = ' '.join(str(equipment).split())

                    # Parse miles
                    miles = None
                    if miles_str:
                        miles_match = re.search(r'([\d,]+)', str(miles_str))
                        if miles_match:
                            miles = int(miles_match.group(1).replace(',', ''))

                    # Parse all rate columns
                    rate = parse_rate_value(rate_str)
                    rpm = parse_rate_value(new_rpm_str)
                    rate_dry_van = parse_rate_value(rate_dry_van_str)
                    rate_dry_van_lcv = parse_rate_value(rate_dry_van_lcv_str)
                    rate_pup_rocky = parse_rate_value(rate_pup_rocky_str)
                    rate_dry_van_rocky = parse_rate_value(rate_dry_van_rocky_str)
                    rate_pup_single = parse_rate_value(rate_pup_single_str)

                    # Parse NEW MINIMUM CHARGE as flat_rate
                    flat_rate = parse_rate_value(new_min_charge_str)

                    # If we have a direct rate but no flat_rate and no specific rates, use rate as flat_rate
                    if rate is not None and flat_rate is None and rate_dry_van is None:
                        flat_rate = rate

                    # Determine if we have any valid rate data
                    has_any_rate = any([
                        flat_rate is not None,
                        rpm is not None,
                        rate_dry_van is not None,
                        rate_dry_van_lcv is not None,
                        rate_pup_rocky is not None,
                        rate_dry_van_rocky is not None,
                        rate_pup_single is not None,
                    ])

                    # Only add if we have valid data (origin, destination, and some rate info)
                    if origin and destination and has_any_rate:
                        lanes.append({
                            "origin": origin,
                            "destination": destination,
                            "stop_offs": stop_off,
                            "miles": miles,
                            "equipment": equipment,
                            "rate_dry_van": rate_dry_van,
                            "rate_dry_van_lcv": rate_dry_van_lcv,
                            "rate_pup_rocky": rate_pup_rocky,
                            "rate_dry_van_rocky": rate_dry_van_rocky,
                            "rate_pup_single": rate_pup_single,
                            "flat_rate": flat_rate,
                            "rpm": rpm,
                        })

    return lanes


def parse_slh_lanes(text: str) -> list[dict]:
    """
    Parse SLH Rate Quotation lanes.

    SLH has various column structures:
    1. Standard: ORIGIN, DESTINATION, RATE, COMMENTS
    2. With STOP OFF: ORIGIN, STOP OFF, DESTINATION, RATE, EQUIPMENT/MODE
    3. With current rate: ORIGIN, DESTINATION, CURRENT RATE, RATE (DIRECT), COMMENTS
    4. With RPM: ORIGIN, DESTINATION ZIP, STATE, RPM, COMMENTS

    Some documents have multi-line data where origin/stop_off/destination are on separate lines.

    Returns list of dicts with all possible fields, using None for missing values.
    """
    lanes = []
    lines = text.split('\n')
    in_data_section = False
    has_rpm_column = False
    has_stop_off_column = False
    has_current_rate = False

    # Canadian province codes and US states
    provinces = {'ON', 'QC', 'BC', 'AB', 'SK', 'MB', 'NS', 'NB', 'NL', 'NF', 'PE', 'NT', 'YT', 'NU'}
    us_states = {'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID', 'IL', 'IN',
                 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV',
                 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN',
                 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'}
    all_regions = provinces | us_states

    # For multi-line parsing
    pending_data = []

    for line in lines:
        line_stripped = line.strip()

        if not line_stripped:
            continue

        # Detect header row
        line_upper = line_stripped.upper()

        if 'ORIGIN' in line_upper:
            if 'RPM' in line_upper:
                in_data_section = True
                has_rpm_column = True
                continue
            elif 'DESTINATION' in line_upper or 'ZIP' in line_upper:
                in_data_section = True
                has_rpm_column = False
                has_stop_off_column = 'STOP OFF' in line_upper or 'STOP-OFF' in line_upper
                has_current_rate = 'CURRENT' in line_upper or '(DIRECT)' in line_upper
                continue

        # Stop at Notes section
        if line_stripped.upper().startswith('NOTES:'):
            break

        # Stop at page footer
        if 'Page ' in line_stripped and 'Customer Initials' in line_stripped:
            continue

        if not in_data_section:
            continue

        # Skip non-data lines
        if any(x in line_stripped for x in ['SLH Transport', 'Telephone', 'RATE QUOTATION',
                                            'COMPANY NAME:', 'ADDRESS:', 'www.slh.ca',
                                            'ACCT CODE:']):
            continue

        # Parse based on detected format
        if has_rpm_column:
            # RPM format: "SCARBOROUGH, ON A0A NF $3.58 53' OCEAN CONTAINER SERVICE"
            match = re.search(r'([A-Za-z][A-Za-z\s\.\-\']+,\s*[A-Z]{2})\s+([A-Z]\d[A-Z])\s+([A-Z]{2})\s+\$\s*([\d,\.]+)\s*(.*)', line_stripped)
            if match:
                lanes.append({
                    "origin": match.group(1).strip(),
                    "destination": f"{match.group(2)} {match.group(3)}",
                    "stop_offs": None,
                    "miles": None,
                    "equipment": match.group(5).strip() if match.group(5) else None,
                    "rate_dry_van": None,
                    "rate_dry_van_lcv": None,
                    "rate_pup_rocky": None,
                    "rate_dry_van_rocky": None,
                    "rate_pup_single": None,
                    "flat_rate": None,
                    "rpm": float(match.group(4).replace(',', '')),
                })
            else:
                match = re.search(r'([A-Za-z][A-Za-z\s\.\-\']+,\s*[A-Z]{2})\s+([A-Z]\d[A-Z])\s+([A-Z]{2})\s+\$\s*([\d,\.]+)', line_stripped)
                if match:
                    lanes.append({
                        "origin": match.group(1).strip(),
                        "destination": f"{match.group(2)} {match.group(3)}",
                        "stop_offs": None,
                        "miles": None,
                        "equipment": "53' OCEAN CONTAINER SERVICE",
                        "rate_dry_van": None,
                        "rate_dry_van_lcv": None,
                        "rate_pup_rocky": None,
                        "rate_dry_van_rocky": None,
                        "rate_pup_single": None,
                        "flat_rate": None,
                        "rpm": float(match.group(4).replace(',', '')),
                    })

        elif has_stop_off_column:
            # Format with STOP OFF column
            # Can be single line: "LACHUTE, QC LAVAL, QC AMHERST, NS $1,755 53' DRY VAN"
            # Or multi-line where data spans multiple lines
            # Special case: "NA" or "N/A" means no stop off

            # Check if line has a rate (indicates complete or end of multi-line record)
            rate_matches = list(re.finditer(r'\$\s*([\d,]+(?:\.\d+)?)', line_stripped))

            if rate_matches:
                # This line has a rate - process it
                first_rate_pos = rate_matches[0].start()
                location_part = line_stripped[:first_rate_pos].strip()

                # Combine with pending data if any
                if pending_data:
                    location_part = ' '.join(pending_data) + ' ' + location_part
                    pending_data = []

                # Everything after rate is equipment
                last_rate_match = rate_matches[-1]
                remainder = line_stripped[last_rate_match.end():].strip()

                # Parse locations - find all CITY, PROV or CITY PROV patterns
                # Handle "-----" and "NA" as empty stop_off
                location_part = location_part.replace('-----', ' ----- ')
                # Replace standalone NA or N/A with marker
                location_part = re.sub(r'\bNA\b', ' ----- ', location_part)
                location_part = re.sub(r'\bN/A\b', ' ----- ', location_part, flags=re.IGNORECASE)

                words = location_part.split()
                locations = []
                current_city = []
                incomplete_origin = None  # For multi-line cases where origin has no province yet

                for word in words:
                    if word == '-----':
                        if current_city:
                            # Save incomplete city as potential origin (province comes later)
                            incomplete_origin = ' '.join(current_city).rstrip(',')
                            current_city = []
                        locations.append('-----')
                    elif word.upper().rstrip(',') in all_regions:
                        if current_city:
                            city_name = ' '.join(current_city).rstrip(',')
                            locations.append(f"{city_name}, {word.upper().rstrip(',')}")
                            current_city = []
                        else:
                            # Orphan province code - might belong to previous incomplete city
                            # Check if we have a pending lane that needs this province
                            if lanes and lanes[-1].get('_needs_origin_province'):
                                # Add province to the previous lane's origin
                                lanes[-1]['origin'] = lanes[-1]['origin'] + f", {word.upper().rstrip(',')}"
                                del lanes[-1]['_needs_origin_province']
                    else:
                        current_city.append(word)

                # We expect 2 or 3 locations (origin, [stop_off], destination)
                # Special case: if we have incomplete_origin and locations has "-----" + destination
                if incomplete_origin and len(locations) >= 1 and locations[0] == '-----':
                    # Format: ORIGIN_CITY, NA DESTINATION, PROV $RATE
                    # incomplete_origin = "NEW WESTMINSTER"
                    # locations = ["-----", "KAMLOOPS, BC"] or just ["-----"] with destination in locations[1]
                    origin = incomplete_origin  # Province will be added later
                    stop_offs = None
                    # Find first non-dash location as destination
                    destination = None
                    for loc in locations:
                        if loc != '-----':
                            destination = loc
                            break

                    if destination:
                        # Extract rate
                        rate = float(rate_matches[0].group(1).replace(',', ''))

                        # Extract equipment
                        equipment_match = re.search(r"(\d+['\"]?\s*(?:FT\s*)?(?:DRY VAN|FLATBED|REEFER|VAN|TANDEM)[^\$]*)", remainder, re.IGNORECASE)
                        equipment = equipment_match.group(1).strip() if equipment_match else None

                        lane_data = {
                            "origin": origin,
                            "destination": destination,
                            "stop_offs": stop_offs,
                            "miles": None,
                            "equipment": equipment,
                            "rate_dry_van": None,
                            "rate_dry_van_lcv": None,
                            "rate_pup_rocky": None,
                            "rate_dry_van_rocky": None,
                            "rate_pup_single": None,
                            "flat_rate": rate,
                            "rpm": None,
                            "_needs_origin_province": True,  # Province comes on next line
                        }
                        lanes.append(lane_data)

                elif len(locations) >= 2:
                    if len(locations) == 2:
                        origin = locations[0]
                        stop_offs = None
                        destination = locations[1]
                    else:
                        # 3+ locations: first is origin, last is destination, middle are stop_offs
                        origin = locations[0]
                        destination = locations[-1]
                        middle = locations[1:-1]
                        # Filter out "-----" from stop_offs
                        stop_offs_list = [loc for loc in middle if loc != '-----']
                        stop_offs = ', '.join(stop_offs_list) if stop_offs_list else None

                    # Extract rate
                    rate = float(rate_matches[0].group(1).replace(',', ''))
                    if has_current_rate and len(rate_matches) >= 2:
                        rate = float(rate_matches[1].group(1).replace(',', ''))

                    # Extract equipment
                    equipment_match = re.search(r"(\d+['\"]?\s*(?:FT\s*)?(?:DRY VAN|FLATBED|REEFER|VAN|TANDEM)[^\$]*)", remainder, re.IGNORECASE)
                    equipment = equipment_match.group(1).strip() if equipment_match else None

                    lane_data = {
                        "origin": origin,
                        "destination": destination,
                        "stop_offs": stop_offs,
                        "miles": None,
                        "equipment": equipment,
                        "rate_dry_van": None,
                        "rate_dry_van_lcv": None,
                        "rate_pup_rocky": None,
                        "rate_dry_van_rocky": None,
                        "rate_pup_single": None,
                        "flat_rate": rate,
                        "rpm": None,
                    }

                    # Check if origin is missing province (ends with comma or no comma+province)
                    if origin.endswith(',') or ',' not in origin:
                        lane_data['_needs_origin_province'] = True

                    lanes.append(lane_data)

                elif len(locations) == 1 and current_city:
                    # Special case: We have one complete location and an incomplete city
                    # This happens with multi-line format like:
                    # "NEW WESTMINSTER, NA KAMLOOPS, BC $1,196" where NEW WESTMINSTER is origin
                    # but its province BC comes on the next line
                    # In this case, current_city = ["NEW", "WESTMINSTER,"] and locations = ["KAMLOOPS, BC"]

                    # The incomplete city (current_city) is the ORIGIN
                    # The complete location is the DESTINATION
                    origin_incomplete = ' '.join(current_city).rstrip(',')
                    destination = locations[0]

                    # Extract rate
                    rate = float(rate_matches[0].group(1).replace(',', ''))

                    # Extract equipment
                    equipment_match = re.search(r"(\d+['\"]?\s*(?:FT\s*)?(?:DRY VAN|FLATBED|REEFER|VAN|TANDEM)[^\$]*)", remainder, re.IGNORECASE)
                    equipment = equipment_match.group(1).strip() if equipment_match else None

                    lane_data = {
                        "origin": origin_incomplete,  # Will get province added later
                        "destination": destination,
                        "stop_offs": None,
                        "miles": None,
                        "equipment": equipment,
                        "rate_dry_van": None,
                        "rate_dry_van_lcv": None,
                        "rate_pup_rocky": None,
                        "rate_dry_van_rocky": None,
                        "rate_pup_single": None,
                        "flat_rate": rate,
                        "rpm": None,
                        "_needs_origin_province": True,
                    }
                    lanes.append(lane_data)

            else:
                # No rate on this line - might be part of multi-line record
                # Or might be an orphan province code for previous lane
                line_upper_stripped = line_stripped.upper().strip()

                # Check if this is just a province/state code
                if line_upper_stripped in all_regions and lanes and lanes[-1].get('_needs_origin_province'):
                    # This is the province for the previous lane's origin
                    lanes[-1]['origin'] = lanes[-1]['origin'].rstrip(',') + f", {line_upper_stripped}"
                    del lanes[-1]['_needs_origin_province']
                elif line_stripped and not line_stripped.upper().startswith(('NOTES:', 'EFFECTIVE', 'EXPIRY')):
                    pending_data.append(line_stripped)

        else:
            # Standard format without STOP OFF column
            rate_matches = list(re.finditer(r'\$\s*([\d,]+(?:\.\d+)?)', line_stripped))

            if not rate_matches:
                continue

            first_rate_pos = rate_matches[0].start()
            location_part = line_stripped[:first_rate_pos].strip()
            last_rate_match = rate_matches[-1]
            remainder_after_rates = line_stripped[last_rate_match.end():].strip()
            remainder_after_rates = re.sub(r'^SAME\s*', '', remainder_after_rates, flags=re.IGNORECASE)

            words = location_part.split()
            locations = []
            current_city = []

            for word in words:
                if word.upper().rstrip(',') in all_regions:
                    if current_city:
                        city_name = ' '.join(current_city).rstrip(',')
                        locations.append(f"{city_name}, {word.upper().rstrip(',')}")
                        current_city = []
                else:
                    current_city.append(word)

            if len(locations) >= 2:
                origin = locations[0]
                destination = locations[-1]

                rates = [float(m.group(1).replace(',', '')) for m in rate_matches]

                equipment_match = re.search(r"(\d+['\"]?\s*(?:FT\s*)?(?:DRY VAN|FLATBED|REEFER|VAN)[^\$]*)", remainder_after_rates, re.IGNORECASE)
                equipment = equipment_match.group(1).strip() if equipment_match else None

                lane = {
                    "origin": origin,
                    "destination": destination,
                    "stop_offs": None,
                    "miles": None,
                    "equipment": equipment,
                    "rate_dry_van": None,
                    "rate_dry_van_lcv": None,
                    "rate_pup_rocky": None,
                    "rate_dry_van_rocky": None,
                    "rate_pup_single": None,
                    "flat_rate": None,
                    "rpm": None,
                }

                if rates:
                    if has_current_rate and len(rates) >= 2:
                        lane["flat_rate"] = rates[1]
                    else:
                        lane["flat_rate"] = rates[0]

                if lane["flat_rate"] is not None:
                    lanes.append(lane)

    # Clean up lanes - remove temporary markers and filter incomplete lanes
    cleaned_lanes = []
    for lane in lanes:
        # Remove temporary markers
        if '_needs_origin_province' in lane:
            del lane['_needs_origin_province']
        # Only include lanes that have both origin and destination
        if lane.get('origin') and lane.get('destination'):
            cleaned_lanes.append(lane)

    return cleaned_lanes


def parse_slh_pdf(pdf_path: str) -> dict:
    """
    Parse an SLH Transport Inc. Rate Quotation PDF.

    Uses table extraction first (more accurate for tabular data),
    falls back to text parsing if no table found.

    Returns a dict with structure matching the requested columns:
    - customer
    - document_date
    - effective_date
    - expiry_date
    - account_manager
    - currency
    - notes
    - lanes: list of lane dicts
    - source_file
    """
    source_file = os.path.basename(pdf_path)

    all_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            all_text += page_text + "\n"

    # Extract header info
    header = extract_slh_header(all_text, source_file)

    # Extract notes
    notes = extract_slh_notes(all_text)

    # Extract currency from notes (overrides default CAD)
    currency = extract_currency_from_notes(notes)

    # Try both table extraction and text parsing, use whichever gives more results
    # Table extraction is more accurate for multi-line cell data
    # Text parsing works better for some document formats
    lanes_from_table = parse_slh_lanes_from_table(pdf_path)
    lanes_from_text = parse_slh_lanes(all_text)

    # Use whichever method extracted more lanes
    lanes = lanes_from_table if len(lanes_from_table) >= len(lanes_from_text) else lanes_from_text

    # Get document date from filename
    document_date = extract_date_from_filename(source_file)

    result = {
        "source_file": source_file,
        "customer": header["customer"],
        "document_date": document_date,
        "effective_date": header["effective_date"],
        "expiry_date": header["expiry_date"],
        "account_manager": header["account_manager"],
        "currency": currency,
        "notes": notes,
        "lanes": lanes,
    }

    return result


# =============================================================================
# MAIN PDF PARSER
# =============================================================================

def parse_pdf(pdf_path: str) -> tuple[dict, bool, str]:
    """
    Parse a single PDF file and extract rate confirmation data.

    Returns: (result_dict, is_confident, all_text)
        - result_dict: The parsed data
        - is_confident: True if service type was confidently detected
        - all_text: The full extracted text (for company name extraction)
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

    return result, is_confident, all_text


def main(pdf_dir_override: str = None, output_dir_override: str = None):
    """
    Main function to parse all PDF files and output JSON by type.

    Args:
        pdf_dir_override: Optional path to PDF source directory (for Jupyter/Colab)
        output_dir_override: Optional path to output directory (for Jupyter/Colab)
    """
    # Handle Jupyter/Colab environment where __file__ is not defined
    try:
        script_dir = Path(__file__).parent.parent
    except NameError:
        # Running in Jupyter/Colab - use current directory or override
        script_dir = Path.cwd()

    # PDF source directory (Production: C.A.T. Files Server - Customer_Files/Customers)
    if pdf_dir_override:
        pdf_dir = Path(pdf_dir_override)
    else:
        pdf_dir = Path(r"C:\Users\mranaivoarison\C.A.T. Inc\C.A.T. Files Server - Customer_Files\Customers")

    # Output directory (project root)
    if output_dir_override:
        output_dir = Path(output_dir_override)
    else:
        output_dir = script_dir

    # Recursively find all PDF files (including in subfolders like "03. Bids Archive/[Customer]/")
    all_pdf_files = list(pdf_dir.rglob("*.pdf"))

    # Normalize paths to fix double backslashes (common issue with OneDrive/SharePoint)
    def normalize_path(p: Path) -> Path:
        """Normalize path to fix double backslashes and other path issues."""
        # Use os.path.normpath for robust path normalization
        path_str = os.path.normpath(str(p))
        return Path(path_str)

    # Normalize all found paths
    all_pdf_files = [normalize_path(f) for f in all_pdf_files]

    # Filter only PDFs whose filename starts with a date (YYYY-MM-DD)
    date_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}')
    pdf_files = [f for f in all_pdf_files if date_pattern.match(f.name)]

    if not pdf_files:
        print(f"No PDF files starting with date (YYYY-MM-DD) found in {pdf_dir}")
        if all_pdf_files:
            print(f"  (Found {len(all_pdf_files)} PDFs but none match the date pattern)")
        return

    print(f"Found {len(pdf_files)} PDF files to process (filtered by date pattern YYYY-MM-DD)")

    # Separate results by type (C.A.T.)
    results_by_type = {
        'FTL': [],
        'LTL': [],
        'Shunting': [],
        'Accessorial': []
    }

    # SLH results (separate)
    slh_results = []

    # Track unknown/unconfident types for investigation
    unknown_types = []

    # Track processing summary for Excel report
    processing_summary = []

    for pdf_path in sorted(pdf_files):
        print(f"Processing: {pdf_path.name}")
        try:
            # Check if file actually exists (OneDrive cloud-only files may show in rglob but not be accessible)
            if not pdf_path.exists():
                print(f"  - SKIPPED: File not found (may be cloud-only in OneDrive)")
                processing_summary.append({
                    "Company": "Unknown",
                    "Type de Service": "SKIPPED",
                    "Extracted Lanes": 0,
                    "File Name": pdf_path.name,
                    "Full Path": str(pdf_path),
                    "Error": "File not found (cloud-only)"
                })
                continue

            # Normalize path string for pdfplumber (fix any remaining double backslashes)
            pdf_path_str = os.path.normpath(str(pdf_path))

            # First, detect company by reading first page
            with pdfplumber.open(pdf_path_str) as pdf:
                first_page_text = pdf.pages[0].extract_text() or ""

            company_name = extract_company_name(first_page_text)

            # Extract customer from folder path (parent folder of the PDF)
            customer_from_path = extract_customer_from_path(pdf_path_str)

            # Route to appropriate parser based on company
            if company_name == "SLH":
                # Use SLH parser
                result = parse_slh_pdf(pdf_path_str)

                # Override customer with folder name if available
                if customer_from_path:
                    result['customer'] = customer_from_path

                # Store full path for reference
                result['full_path'] = pdf_path_str

                slh_results.append(result)

                extracted_count = len(result.get('lanes', []))
                print(f"  - Company: {company_name}, Type: SLH Rate Quotation, Date: {result['document_date']}, Customer: {result['customer']}")
                print(f"  - Extracted {extracted_count} lanes")

                # Add to processing summary
                processing_summary.append({
                    "Company": company_name,
                    "Type de Service": "SLH",
                    "Extracted Lanes": extracted_count,
                    "File Name": pdf_path.name,
                    "Full Path": pdf_path_str
                })

            else:
                # Use C.A.T. parser
                result, is_confident, all_text = parse_pdf(pdf_path_str)
                doc_type = result['doc_type']

                # Override customer with folder name if available
                if customer_from_path:
                    result['customer'] = customer_from_path

                # Store full path for reference
                result['full_path'] = pdf_path_str

                results_by_type[doc_type].append(result)

                # Flag if type detection was not confident
                confidence_marker = "" if is_confident else " [UNKNOWN TYPE - DEFAULTED TO FTL]"
                print(f"  - Company: {company_name}, Type: {doc_type}{confidence_marker}, Date: {result['date']}, Customer: {result['customer']}")

                if not is_confident:
                    unknown_types.append({
                        "file": pdf_path.name,
                        "customer": result.get('customer'),
                        "equipment": result.get('equipment'),
                        "raw_text_preview": result.get('raw_text_preview', '')[:500]
                    })

                # Count extracted items based on type
                if doc_type == 'FTL':
                    extracted_count = len(result.get('lanes', []))
                elif doc_type == 'LTL':
                    extracted_count = len(result.get('rates', []))
                elif doc_type == 'Shunting':
                    extracted_count = len(result.get('services', []))
                elif doc_type == 'Accessorial':
                    extracted_count = len(result.get('charges', []))
                else:
                    extracted_count = 0

                print(f"  - Extracted {extracted_count} items")

                # Add to processing summary with new column names
                processing_summary.append({
                    "Company": company_name,
                    "Type de Service": doc_type,
                    "Extracted Lanes": extracted_count,
                    "File Name": pdf_path.name,
                    "Full Path": pdf_path_str
                })

        except FileNotFoundError as e:
            print(f"  - SKIPPED: File not accessible (OneDrive cloud-only or moved)")
            processing_summary.append({
                "Company": "Unknown",
                "Type de Service": "SKIPPED",
                "Extracted Lanes": 0,
                "File Name": pdf_path.name,
                "Full Path": os.path.normpath(str(pdf_path)),
                "Error": "FileNotFoundError - cloud-only or moved"
            })
            continue

        except Exception as e:
            print(f"  - ERROR: {e}")
            import traceback
            traceback.print_exc()

            # Add error to summary
            processing_summary.append({
                "Company": "Unknown",
                "Type de Service": "ERROR",
                "Extracted Lanes": 0,
                "File Name": pdf_path.name,
                "Full Path": os.path.normpath(str(pdf_path)),
                "Error": str(e)
            })

    # Output separate JSON files
    print("\n" + "="*60)
    print("OUTPUT FILES:")
    print("="*60)

    for doc_type, results in results_by_type.items():
        if results:
            output_path = output_dir / f"CAT_{doc_type.lower()}.json"
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

    # Output SLH results
    if slh_results:
        # Save JSON
        slh_json_path = output_dir / "SLH_rates.json"
        with open(slh_json_path, 'w', encoding='utf-8') as f:
            json.dump(slh_results, f, indent=2, ensure_ascii=False)

        total_slh_lanes = sum(len(r.get('lanes', [])) for r in slh_results)
        print(f"  SLH: {slh_json_path.name}")
        print(f"    - {len(slh_results)} documents, {total_slh_lanes} lanes")

        # Save Excel with flattened structure (one row per lane)
        try:
            import pandas as pd
            slh_rows = []
            for doc in slh_results:
                for lane in doc.get('lanes', []):
                    row = {
                        'CUSTOMER': doc.get('customer'),
                        'DOCUMENT DATE': doc.get('document_date'),
                        'ORIGIN': lane.get('origin'),
                        'STOP OFFS': lane.get('stop_offs'),
                        'DESTINATION': lane.get('destination'),
                        'MILES': lane.get('miles'),
                        'EQUIPMENT': lane.get('equipment'),
                        'CURRENCY': doc.get('currency', 'CAD'),
                        'EFFECTIVE DATE': doc.get('effective_date'),
                        'EXPIRY DATE': doc.get('expiry_date'),
                        'ACCOUNT MANAGER': doc.get('account_manager'),
                        'RATE PER DRY VAN (No LCV)': lane.get('rate_dry_van'),
                        'RATE PER DRY VAN LCV': lane.get('rate_dry_van_lcv'),
                        'RATE PER 28\' PUP BASED ON ROCKY CONFIGURATION OR TWO-PUP SHIPMENT': lane.get('rate_pup_rocky'),
                        'RATE PER DRY VAN WHEN MATCHED UP WITH A PUP (ROCKY)': lane.get('rate_dry_van_rocky'),
                        'RATE PER 28\' PUP BASED ON SINGLE PUP SHIPMENT': lane.get('rate_pup_single'),
                        'FLAT RATE': lane.get('flat_rate'),
                        'RPM': lane.get('rpm'),
                        'NOTES': doc.get('notes'),
                        'File Source': doc.get('source_file'),
                    }
                    slh_rows.append(row)

            if slh_rows:
                slh_df = pd.DataFrame(slh_rows)
                slh_excel_path = output_dir / "SLH_rates.xlsx"
                slh_df.to_excel(slh_excel_path, index=False, sheet_name='SLH Rates')
                print(f"  SLH Excel: {slh_excel_path.name}")
                print(f"    - {len(slh_rows)} lane rows")
        except ImportError:
            print("  Warning: pandas not available, skipping SLH Excel")

    # Always save unknown types file (empty or not) for tracking
    unknown_path = output_dir / "rate_confirmations_unknown.json"
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

    # Generate processing summary Excel file
    if processing_summary:
        try:
            import pandas as pd
            summary_df = pd.DataFrame(processing_summary)
            summary_path = output_dir / "processing_summary.xlsx"
            summary_df.to_excel(summary_path, index=False, sheet_name='Processing Summary')
            print(f"\n  Summary: {summary_path.name}")
            print(f"    - {len(processing_summary)} files processed")
        except ImportError:
            print("\n  Warning: pandas not available, skipping Excel summary")

    print("\nProcessing complete!")


if __name__ == "__main__":
    main()
