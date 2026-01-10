"""
Convert rate confirmation JSON files to Excel format.
Each service type has its own JSON and Excel file:
- FTL: rate_confirmations_ftl.json -> rate_confirmations_ftl.xlsx
- LTL: rate_confirmations_ltl.json -> rate_confirmations_ltl.xlsx
- Shunting: rate_confirmations_shunting.json -> rate_confirmations_shunting.xlsx
- Accessorial: rate_confirmations_accessorial.json -> rate_confirmations_accessorial.xlsx
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


def convert_ftl_to_excel(json_path: str, excel_path: str):
    """Convert FTL (Full Truckload) JSON to Excel. One row per lane."""

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

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

    df = pd.DataFrame(rows)

    column_order = [
        'source_file', 'date', 'customer', 'equipment', 'service_type',
        'origin', 'destination', 'miles', 'mode', 'flat', 'rpm',
        'fund_type', 'notes'
    ]
    columns = [col for col in column_order if col in df.columns]
    df = df[columns]

    df.to_excel(excel_path, index=False, sheet_name='FTL Lanes')
    return df


def convert_ltl_to_excel(json_path: str, excel_path: str):
    """Convert LTL (Less-than-Truckload) JSON to Excel. One row per rate tier."""

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    rows = []
    for doc in data:
        source_file = doc.get('source_file', '')
        date = doc.get('date', '')
        customer = doc.get('customer', '')
        equipment = doc.get('equipment', '')
        origin = doc.get('origin', '')
        destination = doc.get('destination', '')
        notes = doc.get('notes', '')

        for rate in doc.get('rates', []):
            row = {
                'source_file': source_file,
                'date': date,
                'customer': customer,
                'equipment': equipment,
                'origin': origin,
                'destination': destination,
                'skids': rate.get('skids', ''),
                'rate': rate.get('rate'),
                'fund_type': rate.get('fund_type', ''),
                'notes': notes
            }
            rows.append(row)

    df = pd.DataFrame(rows)

    column_order = [
        'source_file', 'date', 'customer', 'equipment',
        'origin', 'destination', 'skids', 'rate', 'fund_type', 'notes'
    ]
    columns = [col for col in column_order if col in df.columns]
    df = df[columns]

    df.to_excel(excel_path, index=False, sheet_name='LTL Rates')
    return df


def convert_shunting_to_excel(json_path: str, excel_path: str):
    """Convert Shunting JSON to Excel. One row per service."""

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    rows = []
    for doc in data:
        source_file = doc.get('source_file', '')
        date = doc.get('date', '')
        customer = doc.get('customer', '')
        equipment = doc.get('equipment', '')
        notes = doc.get('notes', '')

        for service in doc.get('services', []):
            row = {
                'source_file': source_file,
                'date': date,
                'customer': customer,
                'equipment': equipment,
                'location': service.get('location', ''),
                'description': service.get('description', ''),
                'rate_type': service.get('rate_type', ''),
                'rate': service.get('rate'),
                'fund_type': service.get('fund_type', ''),
                'notes': notes
            }
            rows.append(row)

    df = pd.DataFrame(rows)

    column_order = [
        'source_file', 'date', 'customer', 'equipment', 'location',
        'description', 'rate_type', 'rate', 'fund_type', 'notes'
    ]
    columns = [col for col in column_order if col in df.columns]
    df = df[columns]

    df.to_excel(excel_path, index=False, sheet_name='Shunting Services')
    return df


def convert_accessorial_to_excel(json_path: str, excel_path: str):
    """Convert Accessorial JSON to Excel. One row per charge."""

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    rows = []
    for doc in data:
        source_file = doc.get('source_file', '')
        date = doc.get('date', '')
        customer = doc.get('customer', '')
        equipment = doc.get('equipment', '')
        notes = doc.get('notes', '')

        for charge in doc.get('charges', []):
            row = {
                'source_file': source_file,
                'date': date,
                'customer': customer,
                'equipment': equipment,
                'description': charge.get('description', ''),
                'period': charge.get('period', ''),
                'rate': charge.get('rate'),
                'fund_type': charge.get('fund_type', ''),
                'notes': notes
            }
            rows.append(row)

    df = pd.DataFrame(rows)

    column_order = [
        'source_file', 'date', 'customer', 'equipment',
        'description', 'period', 'rate', 'fund_type', 'notes'
    ]
    columns = [col for col in column_order if col in df.columns]
    df = df[columns]

    df.to_excel(excel_path, index=False, sheet_name='Accessorial Charges')
    return df


def main():
    script_dir = Path(__file__).parent.parent

    # Define file mappings
    conversions = [
        ('rate_confirmations_ftl.json', 'rate_confirmations_ftl.xlsx', 'FTL', convert_ftl_to_excel),
        ('rate_confirmations_ltl.json', 'rate_confirmations_ltl.xlsx', 'LTL', convert_ltl_to_excel),
        ('rate_confirmations_shunting.json', 'rate_confirmations_shunting.xlsx', 'Shunting', convert_shunting_to_excel),
        ('rate_confirmations_accessorial.json', 'rate_confirmations_accessorial.xlsx', 'Accessorial', convert_accessorial_to_excel),
    ]

    print("=" * 60)
    print("CONVERTING JSON TO EXCEL")
    print("=" * 60)

    for json_file, excel_file, doc_type, converter in conversions:
        json_path = script_dir / json_file
        excel_path = script_dir / excel_file

        if not json_path.exists():
            print(f"\n{doc_type}: No JSON file found ({json_file})")
            continue

        print(f"\n{doc_type}:")
        df = converter(str(json_path), str(excel_path))
        print(f"  - Input: {json_file}")
        print(f"  - Output: {excel_file}")
        print(f"  - Rows: {len(df)}")

        # Print summary by customer if available
        if 'customer' in df.columns and len(df) > 0:
            print(f"  - Customers: {df['customer'].nunique()}")

    print("\n" + "=" * 60)
    print("Conversion complete!")


if __name__ == "__main__":
    main()
