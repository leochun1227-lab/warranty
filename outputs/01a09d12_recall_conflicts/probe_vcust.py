import ast
import json
import os
from pathlib import Path
import pyodbc

ROOT = Path(__file__).resolve().parents[2]
tree = ast.parse((ROOT / 'fetch_all_tickets_fast_with_firebase_MANDT800_REJECTION_FILTER.py').read_text(encoding='utf-8-sig'))
dsn = os.getenv('SAP_HANA_DSN')
if not dsn:
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SAP_HANA_DSN' for t in n.targets):
            dsn = ast.literal_eval(n.value.args[1])
assert dsn

def query(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]

if __name__ == '__main__':
    with pyodbc.connect(dsn, autocommit=True, timeout=15) as conn:
        result = {}
        for table in ['KNA1', 'KNVV', 'ADRC', 'ADR6', 'TSTC']:
            result[table + '_columns'] = query(conn, 'SELECT COLUMN_NAME, DATA_TYPE_NAME FROM SYS.TABLE_COLUMNS WHERE SCHEMA_NAME=? AND TABLE_NAME=? ORDER BY POSITION', ['SAPHANADB', table])
        result['customer_counts'] = query(conn, 'SELECT MANDT, LAND1, COUNT(*) AS N FROM SAPHANADB.KNA1 GROUP BY MANDT, LAND1 ORDER BY MANDT, LAND1')
        result['vcust_transaction'] = query(conn, "SELECT TCODE, PGMNA, DYPNO FROM SAPHANADB.TSTC WHERE TCODE='VCUST'")
        result['sample_customer'] = query(conn, "SELECT MANDT,KUNNR,NAME1,PSTLZ,ADRNR,ERDAT,KTOKD FROM SAPHANADB.KNA1 WHERE KUNNR='0000205445'")
        (Path(__file__).parent / 'vcust_hana_probe.json').write_text(json.dumps(result, default=str, indent=2), encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if not k.endswith('_columns')}, default=str, indent=2))
        print('Column metadata saved locally.')
