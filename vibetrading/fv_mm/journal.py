from __future__ import annotations
import json
import sqlite3
import time
from pathlib import Path


class Journal:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True,exist_ok=True)
        self.directory=directory
        self.db=sqlite3.connect(directory/"run.sqlite")
        self.db.row_factory=sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS samples (venue TEXT, strategy TEXT, asset TEXT, t REAL, wall_ns INTEGER, phase TEXT, mid REAL, spread REAL, depth REAL, x REAL, fv REAL, ref_mid REAL, reason TEXT, window REAL, source_ns INTEGER, sequence INTEGER);
        CREATE TABLE IF NOT EXISTS quotes (venue TEXT, asset TEXT, t REAL, wall_ns INTEGER, source_ns INTEGER, bid TEXT, ask TEXT, bid_qty TEXT, ask_qty TEXT, seq INTEGER, reset INTEGER);
        CREATE TABLE IF NOT EXISTS trades (venue TEXT, asset TEXT, t REAL, source_ns INTEGER, side TEXT, qty TEXT);
        CREATE TABLE IF NOT EXISTS events (t REAL, wall_ns INTEGER, kind TEXT, venue TEXT, asset TEXT, data TEXT);
        CREATE TABLE IF NOT EXISTS orders (client_id TEXT PRIMARY KEY, venue TEXT, strategy TEXT, asset TEXT, side TEXT, role TEXT, qty TEXT, price TEXT, decision REAL, source_ns INTEGER, receive REAL, send REAL, ack REAL, live REAL, terminal REAL, status TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS fills (venue TEXT, exec_id TEXT, client_id TEXT, asset TEXT, side TEXT, qty TEXT, price TEXT, fee TEXT, maker INTEGER, source_ns INTEGER, receive REAL, PRIMARY KEY(venue,exec_id));
        CREATE TABLE IF NOT EXISTS equity (t REAL, venue TEXT, equity TEXT, available TEXT, margin TEXT);
        CREATE INDEX IF NOT EXISTS samples_idx ON samples(venue,strategy,t);
        CREATE INDEX IF NOT EXISTS quotes_idx ON quotes(venue,asset,t);
        """)

    def event(self, kind, venue="",asset="",**data):
        self.db.execute("INSERT INTO events VALUES(?,?,?,?,?,?)",(time.monotonic(),time.time_ns(),kind,venue,asset,json.dumps(data,default=str,separators=(",",":"))))

    def save(self,name,data):
        target=self.directory/name
        temporary=target.with_suffix(target.suffix+".tmp")
        temporary.write_text(json.dumps(data,default=str,indent=2),encoding="utf-8")
        temporary.replace(target)

    def commit(self):
        self.db.commit()

    def close(self):
        self.commit(); self.db.close()
