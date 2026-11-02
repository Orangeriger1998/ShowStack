#!/usr/bin/env python3
# Prints what ShowStack shows, after a fresh scan, so a change to showstack.json, tool-catalog.json, local-apps.json
# or a project can be checked without the window.  Usage: python3 showstack-report.py
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import showstack_engine as engine  # noqa: E402


def table(rows, columns):
    rows = [[str(row.get(column) or '') for column in columns] for row in rows]
    widths = [max([len(column)] + [len(row[i]) for row in rows]) for i, column in enumerate(columns)]
    line = '  '.join(column.ljust(widths[i]) for i, column in enumerate(columns))
    print(line)
    print('  '.join('-' * width for width in widths))
    for row in rows:
        print('  '.join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    print()


status = engine.get_status(engine.update_inventory())
print('TOOLS')
table(status['Tools'], ['Name', 'Category', 'State', 'Version', 'Detail'])
print('SERVICES AND SERVERS')
table(status['Rows'], ['Section', 'Group', 'Name', 'State', 'Action', 'Detail'])
if status['Hidden']:
    print('{} server(s) hidden'.format(status['Hidden']))
