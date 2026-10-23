"""Package only coupled collection/ETF execution changes for server overlay."""
import hashlib
import io
import json
import tarfile
from pathlib import Path

PACKAGE = 'vibetrading'
NAMES = ['vibetrading/'+s for s in ['models.py','bus.py','runtime.py','health.py','telegram.py','session_alerts.py','market_policy.py','parquet_store.py','brokers.py','cli.py',
    'kis_execution.py','telegram_trade.py','trade_reporting.py','etf_reference.py','etf_csv_history.py','etf_csv_sync.py','etf_history.py','etf_journal.py','etf_trading.py',
    'providers/kis.py','providers/kis_client.py','providers/kiwoom.py','providers/universe.py']]
NAMES += ['config/'+s for s in ['runtime.yaml','crypto_runtime.yaml','universe.yaml','etf_strategy_provenance.json']]
NAMES += ['tests/'+s+'.py' for s in ['test_dual_mock_and_assumed','test_kiwoom_disconnect_alerts','test_publish_and_mock_notifications','test_telegram_status','test_session_alerts','test_etf_mock_trading','test_etf_execution_update','test_etf_csv_sync','test_models','test_settings','test_parquet_store',
    'test_collector_recovery_20261007','test_universe','test_storage_pipeline','test_durable_pipeline_guards']]
DOCKERFILE = b'''ARG BASE
FROM ${BASE}
USER root
COPY --chown=10001:10001 vibetrading/ /app/vibetrading/
COPY --chown=10001:10001 config/ /app/config/
ARG REVISION
LABEL org.opencontainers.image.revision=${REVISION}
ENV VIBETRADING_BUILD_REVISION=${REVISION}
USER vibetrading
'''


def source_package(root):
    flat, src = root / PACKAGE, root / 'src' / PACKAGE
    if flat.is_symlink() or src.is_symlink() or (root / 'src').is_symlink():
        raise RuntimeError('release package source symlink rejected')
    if flat.is_dir() and src.is_dir():
        raise RuntimeError('ambiguous package source roots: vibetrading and src/vibetrading both exist')
    if src.is_dir():
        return src
    if flat.is_dir():
        return flat
    raise RuntimeError('package source root missing')


def physical_path(root, package, name):
    relative = Path(name)
    if relative.parts and relative.parts[0] == PACKAGE:
        return package / Path(*relative.parts[1:])
    return root / relative


def build(root):
    package = source_package(root)
    files = {name: physical_path(root, package, name).read_bytes() for name in NAMES}
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    revision = 'etf-scope-'+hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:16]
    files['manifest.json'] = json.dumps({'revision': revision, 'files': hashes}, indent=2).encode()
    files['Dockerfile'] = DOCKERFILE
    destination = root/'.server_patch/etf-scope-20261013.tar.gz'
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, 'w:gz') as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return {'revision': revision, 'bundle': str(destination)}


def main():
    print(json.dumps(build(Path(__file__).resolve().parents[1])))


if __name__ == '__main__':
    main()
