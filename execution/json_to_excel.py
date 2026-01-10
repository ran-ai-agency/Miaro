"""
Convert rate_confirmations.json to Excel format.
Each row represents a lane with document-level fields flattened.
"""

import json
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    print("Installing pandas...")
    import subprocess
    subprocess.check_call(["pip", "install", "pandas"])
    import pandas as pd

try:
    import openpyxl
except ImportError:
    print("Installing openpyxl...")
    import subprocess
    subprocess.check_call(["pip", "install", "openpyxl"])
    import openpyxl


def json_to_excel(json_path: str, excel_path: str):
    """Convert JSON rate confirmations to Excel."""

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Flatten the data: one row per lane
    rows = []

    for doc in data:
        source_file = doc.get('source_file', '')
        date = doc.get('date', '')
        customer = doc.get('customer', '')
        equipment = doc.get('equipment', '')
        service_type = doc.get('service_type', '')
        notes = doc.get('notes', '')

        for lane in doc.get('lanes', []):
            row = {
                'source_file': source_file,
                'date': date,
                'customer': customer,
                'equipment': equipment,
                'service_type': service_type,
                'origin': lane.get('origin', ''),
                'destination': lane.get('destination', ''),
                'miles': lane.get('miles'),
                'mode': lane.get('mode', ''),
                'flat': lane.get('flat'),
                'rpm': lane.get('rpm'),
                'fund_type': lane.get('fund_type', ''),
                'notes': notes
            }
            rows.append(row)

    # Create DataFrame
    df = pd.DataFrame(rows)

    # Reorder columns as specified
    column_order = [
        'source_file',
        'date',
        'customer',
        'equipment',
        'service_type',
        'origin',
        'destination',
        'miles',
        'mode',
        'flat',
        'rpm',
        'fund_type',
        'notes'
    ]

    # Only include columns that exist
    columns = [col for col in column_order if col in df.columns]
    df = df[columns]

    # Write to Excel
    df.to_excel(excel_path, index=False, sheet_name='Rate Confirmations')

    print(f"Excel file created: {excel_path}")
    print(f"Total rows: {len(df)}")

    return df


def main():
    script_dir = Path(__file__).parent.parent
    json_path = script_dir / "rate_confirmations.json"
    excel_path = script_dir / "rate_confirmations_new.xlsx"

    if not json_path.exists():
        print(f"JSON file not found: {json_path}")
        return

    df = json_to_excel(str(json_path), str(excel_path))

    # Print summary
    print("\nSummary by customer:")
    print(df.groupby('customer').size().to_string())


if __name__ == "__main__":
    main()
