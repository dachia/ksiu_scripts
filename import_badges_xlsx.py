"""Rebuild badges.csv from a hand-edited copy of it saved as xlsx (the human review loop).

Usage: python3 import_badges_xlsx.py "badges 1.xlsx"
The xlsx must have exactly the badges.csv header. Run make_badge.py afterwards (use --rows for changed rows).
"""
import csv, sys
import openpyxl

HEADER = ['№', 'Name', 'Company', 'Country', 'Category', 'Source status', 'Attendance note', 'QR file', 'QR path',
          'Match status', 'QR files found', 'Review note', 'Template colour', 'PDF path', 'TIFF path', 'Badge status']
rows = list(openpyxl.load_workbook(sys.argv[1]).active.iter_rows(values_only=True))
if list(rows[0]) != HEADER:
    sys.exit(f'header mismatch: {rows[0]}')
with open('badges.csv', 'w', encoding='utf-8-sig', newline='') as fh:
    w = csv.writer(fh)
    w.writerow(HEADER)
    w.writerows([('' if v is None else str(v)).strip() for v in r] for r in rows[1:])
print(f'wrote badges.csv: {len(rows) - 1} rows')
