"""Package only coupled collection/ETF execution changes for server overlay."""
import hashlib
import io
import json
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
names = ['vibetrading/'+s for s in ['models.py','bus.py','runtime.py','health.py','telegram.py','session_alerts.py','market_policy.py','parquet_store.py','brokers.py','cli.py',
    'kis_execution.py','telegram_trade.py','trade_reporting.py','etf_reference.py','etf_csv_history.py','etf_csv_sync.py','etf_history.py','etf_journal.py','etf_trading.py',
    'providers/kis.py','providers/kis_client.py','providers/kiwoom.py','providers/universe.py']]
names += ['config/'+s for s in ['runtime.yaml','crypto_runtime.yaml','universe.yaml','etf_strategy_provenance.json']]
names += ['tests/'+s+'.py' for s in ['test_dual_mock_and_assumed','test_kiwoom_disconnect_alerts','test_publish_and_mock_notifications','test_telegram_status','test_session_alerts','test_etf_mock_trading','test_etf_execution_update','test_etf_csv_sync','test_models','test_settings','test_parquet_store',
    'test_collector_recovery_20261007','test_universe','test_storage_pipeline','test_durable_pipeline_guards']]
files = {n:(root/n).read_bytes() for n in names}
hashes = {n:hashlib.sha256(b).hexdigest() for n,b in files.items()}
revision = 'etf-scope-'+hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()[:16]
files['manifest.json'] = json.dumps({'revision':revision,'files':hashes},indent=2).encode()
files['Dockerfile'] = b'''ARG BASE
FROM ${BASE}
USER root
COPY --chown=10001:10001 vibetrading/ /app/vibetrading/
COPY --chown=10001:10001 config/ /app/config/
ARG REVISION
LABEL org.opencontainers.image.revision=${REVISION}
ENV VIBETRADING_BUILD_REVISION=${REVISION}
USER vibetrading
'''
destination = root/'.server_patch/etf-scope-20261013.tar.gz'
with tarfile.open(destination,'w:gz') as archive:
    for name,data in files.items():
        info = tarfile.TarInfo(name)
        info.size = len(data)
        archive.addfile(info,io.BytesIO(data))
print(json.dumps({'revision':revision,'bundle':str(destination)}))
