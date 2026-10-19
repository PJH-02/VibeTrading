"""Deployment boundaries that must survive changes to the older profile."""
from scripts.build_crypto_release import SERVICES, compose, files


def test_crypto_release_does_not_recreate_market_or_mount_old_source():
    generated = compose()
    assert set(generated['services']) == set(SERVICES)
    assert generated['networks']['default'] == {'external': True, 'name': 'vibetrading_default'}
    for service in generated['services'].values():
        assert 'extends' not in service
        assert service['volumes'] == ['${VIBETRADING_HOST_DATA_ROOT:?set in compose.env}:/data',
                                     '${VIBETRADING_HOST_CRYPTO_STATE_ROOT:?set in compose.env}:/data/crypto_state']
        assert service['image'] == '${VIBETRADING_CRYPTO_IMAGE:?immutable crypto release required}'
    for provider in ('binance', 'bitget', 'bybit', 'arcus', 'grvt'):
        service = generated['services']['crypto-collector-' + provider]
        assert service['command'][-1] == provider + '-perp'
        assert service['healthcheck']['interval'] == '30s'
        assert service['environment']['CRYPTO_REQUIRE_STORAGE_READY'] == 'true'


def test_crypto_release_activates_all_safe_retention_gates_together():
    generated = compose()
    for service in generated['services'].values():
        env = service['environment']
        assert env['CRYPTO_EVENT_INDEX_FORMAT'] == 'immutable-runs-v1'
        assert env['CRYPTO_ENFORCE_LIVE_EVENT_WINDOW'] == 'true'
        assert env['CRYPTO_EVENT_RUN_RETENTION_ENABLED'] == 'true'
        assert env['MARKET_EVENT_MAX_AGE_SECONDS'] == '43200'
    env = generated['services']['crypto-maintenance']['environment']
    assert env['ARCHIVE_DELIVERY_HISTORY_PRUNE_ENABLED'] == 'true'
    assert env['ARCHIVE_DELIVERY_PRUNE_ACK_FLOOR_VERIFIED'] == 'true'
    assert env['ARCHIVE_DELIVERY_HISTORY_RETENTION_SECONDS'] == '21600'


def test_crypto_bundle_has_no_environment_database_or_server_patch():
    sources = files()
    assert sources
    for path in sources:
        assert not path.name.startswith('.env')
        assert path.suffix not in {'.sqlite3', '.db', '.pem', '.pyc'}
        assert '.server_patch' not in path.parts
        assert '.codex_remote' not in path.parts
