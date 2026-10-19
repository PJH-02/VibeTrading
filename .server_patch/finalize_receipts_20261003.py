import json,pathlib,subprocess,time,sqlite3
root=pathlib.Path('/opt/vibetrading/releases/exchange-expansion-20261003')
release=json.loads((root/'release.json').read_text())
output=subprocess.check_output(['docker','run','--rm','--memory=192m','--env-file','/etc/vibetrading/app.env',
    '-v',str(root/'probe_extended_auth.py')+':/app/probe_extended_auth.py:ro',release['image'],'python','/app/probe_extended_auth.py'],text=True)
result=json.loads(output); (root/'auth-verification.json').write_text(json.dumps(result,indent=2)); print(output)
crypto=json.loads((root/'deploy/crypto-expansion.yml').read_text())
public_config=[]
for p in pathlib.Path('/root/vibetrading/config').rglob('*'):
    if p.is_file() and p.suffix in ('.yaml','.yml','.json'):
        t=p.read_text(); matches=[s for s in ('SKHYUSDT','SKhynixUSDT') if s in t]
        if matches: public_config.append({'file':str(p),'protected_symbols_present':matches})
protected_manifest=[]
market_db=pathlib.Path('/srv/vibetrading-data/runtime_data/state/vibetrading.sqlite3')
if market_db.exists():
    conn=sqlite3.connect('file:'+str(market_db)+'?mode=ro',uri=True,timeout=10); conn.row_factory=sqlite3.Row
    protected_manifest=[dict(r) for r in conn.execute("SELECT provider,venue,event_type,count(*) files,sum(rows) rows FROM parquet_manifest WHERE lifecycle_state='active' GROUP BY provider,venue,event_type")]
    files=[dict(conn.execute("SELECT path,provider,venue,event_type FROM parquet_manifest WHERE provider=? AND event_type='order_book' AND lifecycle_state='active' AND local_present=1 ORDER BY created_ts_ns DESC LIMIT 1",(p,)).fetchone()) for p in ('binance','hyperliquid')]
    code='import json,pyarrow.parquet as pq; files='+repr(files)+'; print(json.dumps([{**f,"symbols":sorted(set(pq.ParquetFile(f["path"]).read(columns=["instrument_std_symbol"]).column(0).to_pylist()))} for f in files]))'
    protected_books=json.loads(subprocess.check_output(['docker','exec','vibetrading-storage-1','python','-c',code],text=True))
crypto_conn=sqlite3.connect('file:/srv/vibetrading-crypto-state/vibetrading.sqlite3?mode=ro',uri=True)
crypto_conn.row_factory=sqlite3.Row
status={'time_ns':time.time_ns(),'protected_config':public_config,'protected_latest_books':protected_books,'market_manifest':protected_manifest,
    'mounts_configured':subprocess.check_output(['findmnt','--verify','--verbose'],text=True,stderr=subprocess.STDOUT),
    'crypto_stream_generations':[dict(r) for r in crypto_conn.execute('SELECT stream_name,stream_generation,ack_floor FROM delivery_stream')],
    'live_order_environment':{k:crypto['services']['crypto-collector-grvt']['environment'].get(k,'default false') for k in ('LIVE_ORDER_ENABLED','ARCUS_ORDER_SUBMIT_ENABLED','GRVT_ORDER_SUBMIT_ENABLED')}}
(root/'operational-verification.json').write_text(json.dumps(status,indent=2)); print(json.dumps(status,indent=2))
