import csv
import io


def safe_cell(value):
    """CSV 公式注入防护：以危险符号开头的文本加前导单引号。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = str(value)
    return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text


def to_csv(columns, rows):
    out = io.StringIO()
    out.write('﻿')
    writer = csv.writer(out)
    writer.writerow([safe_cell(c.get('label', c.get('key', ''))) for c in columns])
    for row in rows:
        writer.writerow([safe_cell(row.get(c['key'], '')) for c in columns])
    return out.getvalue().encode('utf-8')
