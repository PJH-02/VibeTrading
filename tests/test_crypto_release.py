"""Deployment boundaries that must survive changes to the older profile."""
from pathlib import Path

import pytest

import scripts.build_crypto_release as crypto
from scripts.build_crypto_release import SERVICES, compose, files, prepare


def write(path, data=b'VALUE = 1\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


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


def test_current_repository_enumerates_all_canonical_python_sources():
    root = Path(__file__).resolve().parents[1]
    sources = files()
    relative = {path.relative_to(root).as_posix() for path in sources}
    canonical = {path.relative_to(root).as_posix()
                 for path in (root / 'src').rglob('*.py')
                 if path.is_file() and '__pycache__' not in path.parts}
    assert canonical
    assert canonical <= relative
    assert 'requirements.txt' in relative
    assert 'scripts/build_crypto_release.py' in relative
    assert not any('vibetrading' in name for name in relative)
    assert not any(name.startswith(('docs/', 'strategy/', 'vibetrading/')) for name in relative)
    for path in sources:
        assert not path.name.startswith('.env')
        assert path.suffix not in {'.sqlite3', '.db', '.pem', '.pyc'}
        assert '.server_patch' not in path.parts
        assert '.codex_remote' not in path.parts


def test_prepare_rejects_current_canonical_source_before_writing(tmp_path):
    destination = tmp_path / 'canonical-bundle.tar.gz'
    with pytest.raises(RuntimeError) as error:
        prepare(destination)
    assert 'historical' in str(error.value)
    assert 'scripts/build_release_images.py' in str(error.value)
    assert not destination.exists()
    assert not list(tmp_path.iterdir())


def test_canonical_src_without_legacy_namespace_is_inventoried(tmp_path, monkeypatch):
    repo = tmp_path / 'canonical'
    write(repo / 'src/core/__init__.py', b'')
    write(repo / 'src/runtime/runtime.py', b'RUNTIME = 1\n')
    write(repo / 'config/runtime.yaml', b'name: test\n')
    write(repo / 'strategies/legacy.py', b'LEGACY = 1\n')
    monkeypatch.setattr(crypto, 'ROOT', repo)
    relative = {path.relative_to(repo).as_posix() for path in crypto.files()}
    assert {'src/core/__init__.py', 'src/runtime/runtime.py', 'config/runtime.yaml'} <= relative
    assert 'strategies/legacy.py' not in relative
    destination = repo / 'bundle.tar.gz'
    with pytest.raises(RuntimeError, match='build_release_images'):
        crypto.prepare(destination)
    assert not destination.exists()


def test_canonical_files_rejects_src_root_symlink(tmp_path):
    external = tmp_path / 'external-src'
    write(external / 'core.py', b'CORE = 1\n')
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'src').symlink_to(external, target_is_directory=True)
    with pytest.raises(RuntimeError, match='symlink'):
        files(repo)


@pytest.mark.parametrize('kind', ['file', 'directory'])
def test_canonical_files_rejects_nested_source_symlink(tmp_path, kind):
    repo = tmp_path / ('repo-' + kind)
    write(repo / 'src/core/__init__.py', b'')
    write(repo / 'src/runtime/__init__.py', b'')
    external = tmp_path / ('external-' + kind)
    if kind == 'file':
        write(external, b'EXTERNAL = 1\n')
        (repo / 'src/core/linked.py').symlink_to(external)
    else:
        write(external / 'module.py', b'EXTERNAL = 1\n')
        (repo / 'src/core/linked').symlink_to(external, target_is_directory=True)
    with pytest.raises(RuntimeError, match='symlink'):
        files(repo)


@pytest.mark.parametrize('legacy', ['vibetrading', 'src/vibetrading'])
def test_canonical_and_historical_mixed_roots_fail_closed(tmp_path, monkeypatch, legacy):
    repo = tmp_path / 'mixed'
    write(repo / 'src/runtime/__init__.py', b'')
    write(repo / legacy / '__init__.py', b'')
    monkeypatch.setattr(crypto, 'ROOT', repo)
    with pytest.raises(RuntimeError, match='ambiguous'):
        files()
    destination = repo / 'bundle.tar.gz'
    with pytest.raises(RuntimeError, match='build_release_images'):
        prepare(destination)
    assert not destination.exists()
