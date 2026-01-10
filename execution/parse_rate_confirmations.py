"""
Parse C.A.T. Inc. Rate Confirmation PDF documents and extract structured data.
Outputs all parsed data to a JSON file.

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


def extract_header_from_text(text: str) -> dict:
    """Extract header information from PDF text."""
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

        # Date
        if line_lower.startswith('date:') or line_lower.startswith('effective period'):
            match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', line)
            if match:
                header["date"] = parse_date(match.group(1))

        # Customer
        elif line_lower.startswith('customer'):
            match = re.search(r'customer\s*:?\s*(.+)', line, re.IGNORECASE)
            if match:
                header["customer"] = match.group(1).strip()

        # Equipment
        elif line_lower.startswith('equipment'):
            match = re.search(r'equipment\s*:?\s*(.+)', line, re.IGNORECASE)
            if match:
                header["equipment"] = match.group(1).strip()

        # Service type
        elif line_lower.startswith('service type'):
            match = re.search(r'service type\s*:?\s*(.+)', line, re.IGNORECASE)
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
        # Remove address block if present
        notes = re.sub(r'4 Rue du Transport.*?Fax:\s*\([0-9\-]+\)', '', notes, flags=re.DOTALL)
        notes = re.sub(r'\s+', ' ', notes).strip()
        return notes
    return ""


def detect_column_structure(header_line: str) -> list[str]:
    """Detect which columns are present in the document."""
    header_lower = header_line.lower()
    columns = []

    # Check for each possible column
    if 'origin' in header_lower:
        columns.append('origin')
    if 'destination' in header_lower or 'stops' in header_lower:
        columns.append('destination')
    if 'miles' in header_lower or 'fsc miles' in header_lower:
        columns.append('miles')
    if 'mode' in header_lower:
        columns.append('mode')
    if 'rpm' in header_lower:
        columns.append('rpm')
    if 'flat' in header_lower or 'line haul' in header_lower or 'linehaul' in header_lower:
        columns.append('flat')
    if 'min.' in header_lower or 'min ' in header_lower:
        columns.append('min')
    if 'fund type' in header_lower:
        columns.append('fund_type')

    return columns


def normalize_line_text(text: str) -> str:
    """
    Normalize text that has strange spacing from PDF extraction.
    Examples:
    - "$ 8 75" -> "$875"
    - "$ 1 ,300" -> "$1,300"
    - "$ 2.70" -> "$2.70"
    - "1 ,132" -> "1,132"
    """
    # Fix currency values like "$ 8 75 CAD" -> "$875 CAD"
    # Pattern: $ followed by space-separated digits until USD/CAD
    def fix_currency(match):
        parts = match.group(0)
        # Remove all spaces within the currency value
        normalized = re.sub(r'\s+', '', parts)
        return normalized

    # Match $ followed by digits/spaces/commas/periods until USD/CAD
    # Need to escape $ as \$ in the pattern
    text = re.sub(r'\$ [\d\s,\.]+(?= (?:USD|CAD))', fix_currency, text)

    # Also handle $ values without space after $ sign
    text = re.sub(r'\$[\d\s,\.]+(?=\s*(?:USD|CAD))', fix_currency, text)

    # Fix standalone numbers with spaces before commas (e.g., "1 ,132")
    text = re.sub(r'(\d)\s+,\s*(\d)', r'\1,\2', text)

    # Fix miles: digit groups separated by spaces before $
    # e.g., "7 04 $" -> "704 $"
    text = re.sub(r'(\d+)\s+(\d+)(?=\s+\$)', r'\1\2', text)

    return text


def parse_lane_line_regex(line: str, has_mode: bool = False, has_rpm: bool = False, has_min: bool = False) -> dict | None:
    """
    Parse a lane line using regex.

    Typical formats:
    - "Duncan, SC Laredo, TX 1,311 $2,265 USD"
    - "Fife, WA Calgary, AB 704 $ 2.70 $ 1,900 USD" (with RPM and Min)
    - "Burlington, ON Springfield, MO 955 DV $2,200 CAD" (with Mode)
    """
    # Normalize currency values first (handle PDF extraction quirks)
    line = normalize_line_text(line)

    # Location pattern: City name(s), 2-letter state/province
    # More restrictive to avoid matching numbers
    location_pattern = r'([A-Za-z][A-Za-z\s\.\-\']+,\s*[A-Z]{2})\b'

    # Try to find two locations
    locations = re.findall(location_pattern, line)

    if len(locations) < 2:
        return None

    origin = locations[0].strip()
    destination = locations[1].strip()

    # Extract the rest of the line after the destination
    dest_end = line.rfind(destination) + len(destination)
    remainder = line[dest_end:].strip()

    # Initialize lane data
    lane = {
        "origin": origin,
        "destination": destination,
        "miles": None,
        "mode": None,
        "rpm": None,
        "flat": None,
        "fund_type": None
    }

    # Extract fund type (USD or CAD) - usually at the end
    # Also check in original line since normalization might affect spacing
    fund_match = re.search(r'(USD|CAD)\s*$', remainder) or re.search(r'(USD|CAD)\b', remainder)
    if fund_match:
        lane["fund_type"] = fund_match.group(1)
        # Remove fund type from remainder for easier parsing
        remainder = remainder[:fund_match.start()] + remainder[fund_match.end():]

    # Extract mode if present (DV, FB, DRY, REEFER, FLATBED)
    mode_match = re.search(r'\b(DV|FB|DRY|REEFER|FLATBED)\b', remainder, re.IGNORECASE)
    if mode_match:
        lane["mode"] = mode_match.group(1).upper()
        remainder = remainder[:mode_match.start()] + remainder[mode_match.end():]

    # Pattern for currency values: $ followed by number with optional comma and decimal
    # Must capture the full number including commas
    currency_pattern = r'\$\s*([\d,]+(?:\.\d+)?)'

    # Find all currency values
    currency_matches = re.findall(currency_pattern, remainder)
    currency_values = [f"${m}" for m in currency_matches]

    # Remove currency values from remainder to find miles
    remainder_no_currency = re.sub(r'\$\s*[\d,]+(?:\.\d+)?', ' ', remainder)

    # Find miles - look for a number that's at least 2-4 digits (typical mileage)
    # Miles should be a standalone number, not part of a currency value
    miles_candidates = re.findall(r'\b([\d,]+)\b', remainder_no_currency)
    for candidate in miles_candidates:
        miles_val = parse_miles(candidate)
        if miles_val is not None and miles_val >= 1:  # Valid mileage
            lane["miles"] = miles_val
            break

    # Parse currency values based on column structure
    if has_rpm and (has_min or 'min' in str(has_min).lower()) and len(currency_values) >= 2:
        # RPM is first, Min/Flat is second
        lane["rpm"] = parse_currency_value(currency_values[0])
        lane["flat"] = parse_currency_value(currency_values[1])
    elif len(currency_values) >= 1:
        # Just flat rate - take the last/largest currency value
        lane["flat"] = parse_currency_value(currency_values[-1])
        if has_rpm and len(currency_values) >= 2:
            lane["rpm"] = parse_currency_value(currency_values[0])

    return lane


def parse_pdf(pdf_path: str) -> dict:
    """Parse a single PDF file and extract rate confirmation data."""
    result = {
        "source_file": os.path.basename(pdf_path),
        "date": None,
        "customer": None,
        "equipment": None,
        "service_type": None,
        "lanes": [],
        "notes": ""
    }

    all_text = ""

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            all_text += page_text + "\n"

    # Extract header info
    header_info = extract_header_from_text(all_text)
    result.update(header_info)

    # Extract notes
    result["notes"] = extract_notes_from_text(all_text)

    # Parse lanes from text
    lines = all_text.split('\n')
    columns = []
    in_data_section = False
    all_lanes = []

    for i, line in enumerate(lines):
        line_stripped = line.strip()

        # Skip empty lines
        if not line_stripped:
            continue

        # Detect header row
        if 'Origin' in line and ('Destination' in line or 'Stops' in line):
            columns = detect_column_structure(line)
            in_data_section = True
            continue

        # Stop at Notes section
        if line_stripped.startswith('Notes:'):
            break

        # Skip non-data lines
        if not in_data_section:
            continue

        # Skip known non-data patterns
        if any(x in line_stripped for x in ['C.A.T.', 'Rate Confirmation', '4 Rue du Transport',
                                            'Coteau du Lac', 'Tel:', 'Fax:', 'J0P 1B0',
                                            'Date:', 'Customer:', 'Equipment:', 'Service type:']):
            continue

        # Parse the line
        has_mode = 'mode' in columns
        has_rpm = 'rpm' in columns
        has_min = 'min' in columns

        lane = parse_lane_line_regex(line, has_mode=has_mode, has_rpm=has_rpm, has_min=has_min)

        if lane and lane.get('origin') and lane.get('destination'):
            # Must have at least one rate value
            if lane['flat'] is not None or lane.get('rpm') is not None:
                # Build final lane object
                final_lane = {
                    "origin": lane['origin'],
                    "destination": lane['destination'],
                    "miles": lane['miles'],
                    "mode": lane['mode'],
                    "flat": lane['flat'],
                    "fund_type": lane['fund_type']
                }
                # Add rpm if present
                if lane.get('rpm') is not None:
                    final_lane['rpm'] = lane['rpm']

                all_lanes.append(final_lane)

    result["lanes"] = all_lanes
    return result


def main():
    """Main function to parse all PDF files and output JSON."""
    script_dir = Path(__file__).parent.parent
    pdf_dir = script_dir

    pdf_files = list(pdf_dir.glob("*.pdf"))

    if not pdf_files:
        print(f"No PDF files found in {pdf_dir}")
        return

    print(f"Found {len(pdf_files)} PDF files to process")

    all_results = []

    for pdf_path in sorted(pdf_files):
        print(f"Processing: {pdf_path.name}")
        try:
            result = parse_pdf(str(pdf_path))
            all_results.append(result)
            print(f"  - Date: {result['date']}, Customer: {result['customer']}")
            print(f"  - Extracted {len(result['lanes'])} lanes")
        except Exception as e:
            print(f"  - ERROR: {e}")
            import traceback
            traceback.print_exc()
            all_results.append({
                "source_file": pdf_path.name,
                "error": str(e),
                "lanes": []
            })

    # Output JSON file
    output_path = script_dir / "rate_confirmations.json"

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    print(f"\nOutput written to: {output_path}")

    total_lanes = sum(len(r.get('lanes', [])) for r in all_results)
    print(f"Total documents processed: {len(all_results)}")
    print(f"Total lanes extracted: {total_lanes}")


if __name__ == "__main__":
    main()
