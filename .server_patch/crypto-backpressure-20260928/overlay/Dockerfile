FROM vibetrading-crypto:20260927-fix
USER root
COPY --chown=10001:10001 archive.py sqlite_store.py runtime.py settings.py storage_worker.py storage_backend.py gap_ledger.py /app/vibetrading/
USER vibetrading
