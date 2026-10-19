"""Small read-only snapshot; no credentials, cookies or order signatures."""
import argparse
import json
from pathlib import Path
import sqlite3

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("directory",type=Path);args=p.parse_args()
    db=sqlite3.connect(args.directory/"run.sqlite");db.row_factory=sqlite3.Row
    result={"status":json.loads((args.directory/"status.json").read_text()),"order_counts":[dict(r) for r in db.execute("SELECT venue,strategy,status,count(*) AS n FROM orders GROUP BY venue,strategy,status")],"recent_orders":[dict(r) for r in db.execute("SELECT venue,strategy,asset,side,role,qty,price,status,error,send,ack,live,terminal FROM orders ORDER BY decision DESC LIMIT 8")],"errors":[dict(r) for r in db.execute("SELECT kind,venue,asset,data FROM events WHERE kind LIKE '%error%' OR kind='create_rejected' OR kind='feed_gap' OR kind='private_gap' ORDER BY t DESC LIMIT 8")]}
    print(json.dumps(result,indent=2));db.close()
