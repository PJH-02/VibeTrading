"""Offline, fail-closed migration of the crypto delivery ledger.

The source database is preserved beside the replacement. Delivery rows are
streamed once into a new compact database so migration does not amplify the
small random I/O bottleneck that it is intended to remove.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
import time
from pathlib import Path


COMPACT_TABLES="""
CREATE TABLE delivery_stream (
  stream_id INTEGER PRIMARY KEY, stream_name TEXT NOT NULL, stream_generation TEXT NOT NULL,
  ack_floor INTEGER NOT NULL DEFAULT 0, updated_ts_ns INTEGER NOT NULL,
  UNIQUE(stream_name,stream_generation));
CREATE TABLE parquet_delivery_compact (
  stream_id INTEGER NOT NULL, stream_sequence INTEGER NOT NULL,
  event_key BLOB NOT NULL, file_rowid INTEGER NOT NULL, persisted_ts_ns INTEGER NOT NULL,
  PRIMARY KEY(stream_id,stream_sequence));
"""
COMPACT_INDEXES="""
CREATE INDEX idx_delivery_compact_event ON parquet_delivery_compact(event_key);
CREATE INDEX idx_delivery_compact_prune ON parquet_delivery_compact(persisted_ts_ns,stream_id,stream_sequence);
"""
EXCLUDED_TABLES={"parquet_delivery","parquet_delivery_compact","delivery_stream","sqlite_sequence"}
EXCLUDED_INDEXES={"idx_delivery_event_id","idx_delivery_compact_event","idx_delivery_compact_prune"}


def progress(stage:str,**detail)->None:
    print(json.dumps({"stage":stage,**detail},separators=(",",":")),file=sys.stderr,flush=True)


def event_key(value:str|None)->bytes:
    return hashlib.sha256(str(value or "").encode("utf-8")).digest()


def latest_backup(db:sqlite3.Connection)->dict|None:
    row=db.execute("SELECT remote_key,sha256,created_ts_ns FROM archive_backup ORDER BY created_ts_ns DESC LIMIT 1").fetchone()
    return {"key":row[0],"sha256":row[1],"created_ts_ns":int(row[2])} if row else None


def inspect(database:Path,retain_hours:int)->dict:
    del retain_hours
    db=sqlite3.connect(f"file:{database.as_posix()}?mode=ro",uri=True)
    try:return {"database":str(database),"bytes":database.stat().st_size,"last_remote_backup":latest_backup(db)}
    finally:db.close()


def _copy_small_tables(source:sqlite3.Connection,target:sqlite3.Connection)->dict[str,int]:
    schemas=source.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    copied={}
    for name,ddl in schemas:
        if name in EXCLUDED_TABLES:continue
        target.execute(str(ddl));columns=[str(row[1]) for row in source.execute(f'PRAGMA table_info("{name}")')]
        marks=",".join("?" for _ in columns);column_sql=",".join(f'"{column}"' for column in columns)
        preserve_rowid=name=="parquet_manifest"
        cursor=source.execute(f'SELECT {"rowid," if preserve_rowid else ""}{column_sql} FROM "{name}"');count=0
        while True:
            rows=cursor.fetchmany(1000)
            if not rows:break
            if preserve_rowid:
                target.executemany(f'INSERT INTO "{name}"(rowid,{column_sql}) VALUES(?,{marks})',rows)
            else:target.executemany(f'INSERT INTO "{name}"({column_sql}) VALUES({marks})',rows)
            count+=len(rows)
        copied[name]=count
    for name,ddl in source.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL ORDER BY name"):
        if name not in EXCLUDED_INDEXES:target.execute(str(ddl))
    return copied


def _stream_deliveries(source:sqlite3.Connection,target:sqlite3.Connection,*,stream_drained:bool,
                       acked_through:int|None,current_generation:str|None=None)->tuple[int,dict[str,dict[str,int]]]:
    manifest={str(row[0]):int(row[1]) for row in source.execute("SELECT file_id,rowid FROM parquet_manifest")}
    streams:dict[tuple[str,str],dict[str,int]]={};stream_ids:dict[tuple[str,str],int]={};rows_written=0
    cursor=source.execute("SELECT stream_name,stream_generation,stream_sequence,event_id,file_id,persisted_ts_ns FROM parquet_delivery ORDER BY rowid")
    target.execute("BEGIN")
    try:
        while True:
            rows=cursor.fetchmany(10_000)
            if not rows:break
            output=[]
            for stream_name,generation,sequence,event_id,file_id,persisted in rows:
                file_rowid=manifest.get(str(file_id))
                if file_rowid is None:raise RuntimeError(f"delivery row references missing manifest: {file_id}")
                key=(str(stream_name),str(generation))
                if key not in stream_ids:
                    stream_id=len(stream_ids)+1;stream_ids[key]=stream_id
                    streams[key]={"stream_id":stream_id,"min":int(sequence),"max":int(sequence)}
                state=streams[key];state["min"]=min(state["min"],int(sequence));state["max"]=max(state["max"],int(sequence))
                output.append((state["stream_id"],int(sequence),event_key(event_id),file_rowid,int(persisted)))
            target.executemany("INSERT INTO parquet_delivery_compact VALUES(?,?,?,?,?)",output);rows_written+=len(output)
            if rows_written%500_000==0:progress("delivery_copy",rows=rows_written)
        now=time.time_ns();active_key=None
        if not stream_drained:
            candidates=[key for key in streams if current_generation and key[1]==current_generation]
            if not candidates:
                candidates=[key for key,state in streams.items() if state["min"]<=int(acked_through or -1)<=state["max"]]
            if not candidates:
                candidates=[key for key,state in streams.items() if int(acked_through or -1)<=state["max"]]
            if len(candidates)!=1:raise RuntimeError(f"ACK floor does not identify exactly one active stream generation: {candidates}")
            active_key=candidates[0]
        for key,state in streams.items():
            floor=state["max"] if stream_drained or key!=active_key else min(state["max"],int(acked_through or 0))
            target.execute("INSERT INTO delivery_stream VALUES(?,?,?,?,?)",(state["stream_id"],key[0],key[1],floor,now))
        target.execute("COMMIT")
    except Exception:
        target.execute("ROLLBACK");raise
    return rows_written,{f"{key[0]}:{key[1]}":state for key,state in streams.items()}


def migrate(database:Path,retain_hours:int,min_free_gb:float,*,stream_drained:bool=True,
            current_generation:str|None=None,acked_through:int|None=None)->dict:
    del retain_hours
    source_stat=database.stat()
    free=shutil.disk_usage(database.parent).free;minimum=int(min_free_gb*1024**3)
    if free<minimum:raise RuntimeError(f"requires at least {minimum} free bytes; found {free}")
    stamp=time.strftime("%Y%m%dT%H%M%SZ",time.gmtime());building=database.with_name(f".{database.name}.{stamp}.building")
    preserved=database.with_name(f"{database.name}.precompact-{stamp}")
    if building.exists() or preserved.exists():raise RuntimeError("migration output path already exists")
    writable=sqlite3.connect(database,isolation_level=None,timeout=120)
    try:
        busy=writable.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
        if busy:raise RuntimeError("SQLite WAL is busy; storage or maintenance is still connected")
    finally:writable.close()
    source=sqlite3.connect(f"file:{database.as_posix()}?mode=ro&immutable=1",uri=True)
    target=sqlite3.connect(building,isolation_level=None)
    try:
        backup=latest_backup(source)
        if not backup:raise RuntimeError("no recorded remote SQLite backup; refusing migration")
        target.execute("PRAGMA journal_mode=OFF");target.execute("PRAGMA synchronous=OFF")
        copied=_copy_small_tables(source,target);target.executescript(COMPACT_TABLES);progress("catalogue_copied",tables=copied)
        rows,streams=_stream_deliveries(source,target,stream_drained=stream_drained,acked_through=acked_through,
                                        current_generation=current_generation)
        progress("ledger_converted",compact_rows=rows,streams=streams)
        target.executescript(COMPACT_INDEXES);progress("indexes_complete",compact_rows=rows)
        if target.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise RuntimeError("compact database integrity check failed")
        target.commit()
    except Exception:
        target.close();source.close();building.unlink(missing_ok=True);raise
    else:target.close();source.close()
    os.chmod(building,stat.S_IMODE(source_stat.st_mode))
    if hasattr(os,"chown"):
        os.chown(building,source_stat.st_uid,source_stat.st_gid)
    with building.open("rb+") as output:os.fsync(output.fileno())
    try:directory_fd=os.open(database.parent,os.O_RDONLY)
    except OSError:directory_fd=None
    try:
        os.replace(database,preserved)
        for suffix in ("-wal","-shm"):
            sidecar=Path(str(database)+suffix)
            if sidecar.exists():os.replace(sidecar,Path(str(preserved)+suffix))
        os.replace(building,database)
        if directory_fd is not None:os.fsync(directory_fd)
    finally:
        if directory_fd is not None:os.close(directory_fd)
    return {"status":"migrated","database":str(database),"old_bytes":preserved.stat().st_size,
            "new_bytes":database.stat().st_size,"compact_rows":rows,"streams":streams,
            "last_remote_backup":backup,"preserved_database":str(preserved)}


def main()->None:
    parser=argparse.ArgumentParser();parser.add_argument("--database",type=Path,required=True)
    parser.add_argument("--retain-hours",type=int,default=24);parser.add_argument("--min-free-gb",type=float,default=4.0)
    parser.add_argument("--apply",action="store_true");parser.add_argument("--confirm-stream-drained",action="store_true")
    parser.add_argument("--current-generation");parser.add_argument("--acked-through",type=int);args=parser.parse_args()
    if args.apply:
        if not args.confirm_stream_drained and args.acked_through is None:raise SystemExit("--apply requires a drained stream or --acked-through")
        result=migrate(args.database,args.retain_hours,args.min_free_gb,stream_drained=args.confirm_stream_drained,
                       current_generation=args.current_generation,acked_through=args.acked_through)
    else:result={**inspect(args.database,args.retain_hours),"status":"dry-run"}
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
