"""Release builders consume canonical src sources while keeping legacy flat bundles."""
import hashlib
import json
import tarfile

import pytest

from scripts import build_crypto_release as crypto
from scripts import build_etf_scope_release as etf
from scripts import build_telegram_restore_release as telegram
from scripts.refactoring import build_release_manifest as manifest
from scripts.refactoring import build_server_candidate as candidate


def write(path, data=b'VALUE = 1\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def package_path(root, layout):
    return root / ('src/vibetrading' if layout == 'src' else 'vibetrading')


# --- crypto release -------------------------------------------------------

def crypto_repo(tmp_path, layout):
    repo = tmp_path / ('crypto-' + layout)
    package = package_path(repo, layout)
    write(repo / 'main.py', b'print("main")\n')
    write(repo / 'requirements.txt', b'PyYAML\n')
    write(repo / 'scripts/build_crypto_release.py', b'# builder\n')
    write(package / '__init__.py', b'')
    write(package / 'core.py', b'CORE = 1\n')
    write(package / 'nested/module.py', b'NESTED = 1\n')
    write(package / '__pycache__/ignored.py', b'IGNORED = 1\n')
    write(package / '.env', b'SECRET=1\n')
    write(package / 'ignored.txt', b'IGNORED\n')
    write(repo / 'config/runtime.yaml', b'name: test\n')
    write(repo / 'strategies/base.py', b'STRATEGY = 1\n')
    services = '\n'.join(
        f"  {name}:\n    command: ['python', '{name}']\n"
        "    environment: {}\n    healthcheck: {test: ['CMD', 'true']}"
        for name in crypto.SERVICES)
    write(repo / 'deploy/crypto-profile.yml',
          ('secrets: {}\nservices:\n' + services + '\n').encode())
    return repo


@pytest.mark.parametrize('layout', ['src', 'flat'])
def test_crypto_bundle_keeps_legacy_logical_names(tmp_path, monkeypatch, layout):
    repo = crypto_repo(tmp_path, layout)
    monkeypatch.setattr(crypto, 'ROOT', repo)
    destination = repo / 'bundle.tar.gz'
    crypto.prepare(destination)
    with tarfile.open(destination) as archive:
        names = set(archive.getnames())
        contents = {name: archive.extractfile(name).read() for name in names}
    assert 'vibetrading/core.py' in names
    assert contents['vibetrading/core.py'] == b'CORE = 1\n'
    assert contents['vibetrading/nested/module.py'] == b'NESTED = 1\n'
    assert not any(name.startswith('src/') for name in names)
    assert not any('__pycache__' in name or name.endswith('/.env') or name.endswith('ignored.txt')
                   for name in names)
    generated_manifest = json.loads(contents['bundle-manifest.json'])
    assert 'vibetrading/core.py' in generated_manifest
    assert 'vibetrading/nested/module.py' in generated_manifest
    assert b'COPY --chown=10001:10001 vibetrading /app/vibetrading' in contents['Dockerfile.r2']


def test_crypto_rejects_ambiguous_source_roots(tmp_path):
    repo = tmp_path / 'crypto-ambiguous'
    (repo / 'vibetrading').mkdir(parents=True)
    (repo / 'src/vibetrading').mkdir(parents=True)
    with pytest.raises(RuntimeError, match='ambiguous'):
        crypto.source_package(repo)


def test_crypto_rejects_missing_package(tmp_path):
    repo = tmp_path / 'crypto-empty'
    repo.mkdir()
    with pytest.raises(RuntimeError, match='missing'):
        crypto.source_package(repo)
    with pytest.raises(RuntimeError, match='missing'):
        crypto.files(repo)


# --- ETF and telegram historical writers ---------------------------------

@pytest.mark.parametrize('layout', ['src', 'flat'])
def test_etf_bundle_maps_src_to_flat_payload(tmp_path, layout):
    repo = tmp_path / ('etf-' + layout)
    package = package_path(repo, layout)
    expected = {}
    for name in etf.NAMES:
        physical = package / name[len('vibetrading/'):] if name.startswith('vibetrading/') else repo / name
        data = (name + '\n').encode()
        write(physical, data)
        expected[name] = data
    result = etf.build(repo)
    with tarfile.open(result['bundle']) as archive:
        names = set(archive.getnames())
        contents = {name: archive.extractfile(name).read() for name in names}
    assert set(expected).issubset(names)
    assert not any(name.startswith('src/') for name in names)
    assert all(contents[name] == data for name, data in expected.items())
    generated_manifest = json.loads(contents['manifest.json'])
    assert set(generated_manifest['files']) == set(expected)
    assert b'COPY --chown=10001:10001 vibetrading/ /app/vibetrading/' in contents['Dockerfile']


@pytest.mark.parametrize('layout', ['src', 'flat'])
def test_telegram_bundle_maps_src_to_flat_payload(tmp_path, layout):
    repo = tmp_path / ('telegram-' + layout)
    package = package_path(repo, layout)
    write(repo / 'main.py', b'print("main")\n')
    write(package / '__init__.py', b'')
    write(package / 'core.py', b'CORE = 1\n')
    write(package / 'nested/module.py', b'NESTED = 1\n')
    write(package / '__pycache__/ignored.py', b'IGNORED = 1\n')
    write(package / '.env', b'SECRET=1\n')
    write(repo / 'config/app.yaml', b'name: test\n')
    write(repo / 'strategy/base.py', b'STRATEGY = 1\n')
    write(repo / 'strategies/base.py', b'STRATEGY = 2\n')
    write(repo / 'tests/test_example.py', b'TEST = 1\n')
    write(repo / 'deploy/app.yml', b'service: app\n')
    result = telegram.build(repo)
    with tarfile.open(repo / '.server_patch/telegram-recovery-20261014.tar.gz') as archive:
        names = set(archive.getnames())
        contents = {name: archive.extractfile(name).read() for name in names}
    assert 'vibetrading/core.py' in names
    assert contents['vibetrading/core.py'] == b'CORE = 1\n'
    assert contents['vibetrading/nested/module.py'] == b'NESTED = 1\n'
    assert 'config/app.yaml' in names and 'tests/test_example.py' in names
    assert not any(name.startswith('src/') for name in names)
    assert not any('__pycache__' in name or name.endswith('.env') for name in names)
    generated_manifest = json.loads(contents['manifest.json'])
    assert 'vibetrading/core.py' in generated_manifest['files']
    assert result['files'] == len(generated_manifest['files'])
    assert b'COPY --chown=10001:10001 vibetrading/ /app/vibetrading/' in contents['Dockerfile']


def test_all_readers_reject_missing_package(tmp_path):
    repo = tmp_path / 'no-package'
    repo.mkdir()
    with pytest.raises(RuntimeError, match='missing'):
        etf.source_package(repo)
    with pytest.raises(RuntimeError, match='missing'):
        telegram.source_package(repo)
    with pytest.raises(FileNotFoundError, match='missing'):
        manifest.source_package(repo)
    with pytest.raises(ValueError, match='missing'):
        candidate.package_layout(repo)


def symlinked_repo(tmp_path, name, ancestor):
    repo = tmp_path / name
    external = tmp_path / (name + '-external')
    write(external / 'vibetrading/core.py', b'CORE = 1\n')
    repo.mkdir()
    if ancestor == 'src':
        (repo / 'src').symlink_to(external, target_is_directory=True)
    else:
        (repo / 'vibetrading').symlink_to(external / 'vibetrading', target_is_directory=True)
    return repo


@pytest.mark.parametrize('reader', ['crypto', 'etf', 'telegram', 'manifest', 'candidate'])
@pytest.mark.parametrize('ancestor', ['flat', 'src'])
def test_readers_reject_symlinked_package_roots(tmp_path, reader, ancestor):
    repo = symlinked_repo(tmp_path, f'{reader}-{ancestor}', ancestor)
    readers = {
        'crypto': crypto.source_package,
        'etf': etf.source_package,
        'telegram': telegram.source_package,
        'manifest': manifest.source_package,
        'candidate': candidate.package_layout,
    }
    with pytest.raises((RuntimeError, ValueError, FileNotFoundError), match='symlink'):
        readers[reader](repo)


# --- release manifest -----------------------------------------------------

def manifest_repo(tmp_path, layout):
    repo = tmp_path / ('manifest-' + layout)
    package = package_path(repo, layout)
    write(package / 'same.py', b'SAME = 1\n')
    write(package / 'changed.py', b'CHANGED = 2\n')
    baseline = {
        'files': {
            'vibetrading/same.py': {'sha256': hashlib.sha256(b'SAME = 1\n').hexdigest()},
            'vibetrading/changed.py': {'sha256': '0' * 64},
        },
        'source_set_sha256': 'baseline-set',
    }
    write(repo / 'docs/refactoring/baseline_manifest.json',
          json.dumps(baseline).encode())
    return repo


def snapshot():
    return {
        'containers': [{
            'name': 'role', 'image': 'image', 'image_id': 'id', 'command': ['python'],
            'compose_files': ['compose.yml'], 'mounts': [], 'memory_limit': 1,
            'source_hashes': {},
        }],
        'memory': {'stdout': 'Mem: 100\n'},
        'cpu_count': 4,
        'observed_utc': '2026-10-23T00:00:00Z',
    }


@pytest.mark.parametrize('layout', ['src', 'flat'])
def test_manifest_compares_with_logical_vibetrading_keys(tmp_path, layout):
    repo = manifest_repo(tmp_path, layout)
    result = manifest.build(repo, snapshot())
    assert set(result['source_hashes']) == {'vibetrading/same.py', 'vibetrading/changed.py'}
    assert 'vibetrading/same.py' not in result['changed_sources']
    assert set(result['changed_sources']) == {'vibetrading/changed.py'}
    assert result['roles'][0]['deployment_status'] == 'NOT_BUILT_NOT_DEPLOYED'
    assert any('not built/verified' in blocker for blocker in result['blockers'])


def test_manifest_rejects_ambiguous_source_roots(tmp_path):
    repo = tmp_path / 'manifest-ambiguous'
    (repo / 'vibetrading').mkdir(parents=True)
    (repo / 'src/vibetrading').mkdir(parents=True)
    with pytest.raises(ValueError, match='ambiguous'):
        manifest.source_package(repo)


# --- server candidate -----------------------------------------------------

def candidate_base(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    base = root / 'tmp/refactoring'
    base.mkdir(parents=True)
    monkeypatch.setattr(candidate, 'ROOT', root)
    return base


def test_candidate_normalizes_src_local_against_flat_server(tmp_path, monkeypatch):
    base = candidate_base(tmp_path, monkeypatch)
    baseline, server, local, output = (base / name for name in ('baseline', 'server', 'local', 'candidate'))
    write(baseline / 'vibetrading/pkg.py', b'BASE = 1\n')
    write(server / 'vibetrading/pkg.py', b'BASE = 1\n')
    write(server / 'vibetrading/only.py', b'SERVER_ONLY = 1\n')
    write(local / 'src/vibetrading/pkg.py', b'LOCAL = 2\n')
    write(local / 'src/vibetrading/new.py', b'NEW = 3\n')
    write(local / 'notes.txt', b'notes\n')
    result = candidate.build(
        server, local, output, {'vibetrading/pkg.py', 'vibetrading/new.py'}, baseline=baseline)
    assert (output / 'vibetrading/pkg.py').read_bytes() == b'LOCAL = 2\n'
    assert (output / 'vibetrading/new.py').read_bytes() == b'NEW = 3\n'
    assert (output / 'vibetrading/only.py').read_bytes() == b'SERVER_ONLY = 1\n'
    assert (output / 'notes.txt').read_bytes() == b'notes\n'
    assert not (output / 'src').exists()
    assert set(result['changes']) == {'vibetrading/pkg.py', 'vibetrading/new.py'}
    assert result['changes']['vibetrading/pkg.py']['state'] == 'EXACT_SERVER_PREIMAGE_PATCH'
    assert result['changes']['vibetrading/new.py']['state'] == 'NEW_REFACTOR_MODULE'


def test_candidate_accepts_src_form_delta_path(tmp_path, monkeypatch):
    base = candidate_base(tmp_path, monkeypatch)
    baseline, server, local, output = (base / name for name in ('baseline', 'server', 'local', 'candidate'))
    write(baseline / 'vibetrading/pkg.py', b'BASE = 1\n')
    write(server / 'vibetrading/pkg.py', b'BASE = 1\n')
    write(server / 'vibetrading/only.py', b'SERVER_ONLY = 1\n')
    write(local / 'src/vibetrading/pkg.py', b'LOCAL = 2\n')
    write(local / 'src/vibetrading/not-selected.py', b'NOT_SELECTED = 3\n')
    result = candidate.build(server, local, output, {'src/vibetrading/pkg.py'}, baseline=baseline)
    assert (output / 'vibetrading/pkg.py').read_bytes() == b'LOCAL = 2\n'
    assert not (output / 'vibetrading/not-selected.py').exists()
    assert (output / 'vibetrading/only.py').read_bytes() == b'SERVER_ONLY = 1\n'
    assert set(result['changes']) == {'vibetrading/pkg.py'}
    assert result['selected_delta_paths'] == ['vibetrading/pkg.py']


def test_candidate_normalizes_mixed_delta_forms_and_rejects_traversal():
    assert candidate.normalize_deltas(
        {'vibetrading/pkg.py', 'src/vibetrading/pkg.py'}) == {'vibetrading/pkg.py'}
    with pytest.raises(ValueError, match='unsafe'):
        candidate.normalize_deltas({'src/vibetrading/../escape.py'})


def test_candidate_normalizes_src_server_and_baseline_for_flat_local(tmp_path, monkeypatch):
    base = candidate_base(tmp_path, monkeypatch)
    baseline, server, local, output = (base / name for name in ('baseline', 'server', 'local', 'candidate'))
    write(baseline / 'src/vibetrading/pkg.py', b'BASE = 1\n')
    write(server / 'src/vibetrading/pkg.py', b'BASE = 1\n')
    write(server / 'src/vibetrading/only.py', b'SERVER_ONLY = 1\n')
    write(local / 'vibetrading/pkg.py', b'LOCAL = 2\n')
    result = candidate.build(server, local, output, {'vibetrading/pkg.py'}, baseline=baseline)
    assert (output / 'vibetrading/pkg.py').read_bytes() == b'LOCAL = 2\n'
    assert (output / 'vibetrading/only.py').read_bytes() == b'SERVER_ONLY = 1\n'
    assert not (output / 'src').exists()
    assert set(result['server_preimages']) == {'vibetrading/pkg.py', 'vibetrading/only.py'}
    assert result['changes']['vibetrading/pkg.py']['state'] == 'EXACT_SERVER_PREIMAGE_PATCH'


def test_candidate_rejects_ambiguous_local_roots(tmp_path, monkeypatch):
    base = candidate_base(tmp_path, monkeypatch)
    baseline, server, local, output = (base / name for name in ('baseline', 'server', 'local', 'candidate'))
    write(baseline / 'vibetrading/pkg.py', b'BASE = 1\n')
    write(server / 'vibetrading/pkg.py', b'BASE = 1\n')
    write(local / 'vibetrading/pkg.py', b'LOCAL = 2\n')
    write(local / 'src/vibetrading/pkg.py', b'LOCAL = 2\n')
    with pytest.raises(ValueError, match='ambiguous'):
        candidate.build(server, local, output, {'vibetrading/pkg.py'}, baseline=baseline)


def test_candidate_rejects_unsafe_delta_path(tmp_path, monkeypatch):
    base = candidate_base(tmp_path, monkeypatch)
    baseline, server, local, output = (base / name for name in ('baseline', 'server', 'local', 'candidate'))
    write(baseline / 'vibetrading/pkg.py', b'BASE = 1\n')
    write(server / 'vibetrading/pkg.py', b'BASE = 1\n')
    write(local / 'vibetrading/pkg.py', b'LOCAL = 2\n')
    with pytest.raises(ValueError, match='unsafe'):
        candidate.build(server, local, output, {'../escape.py'}, baseline=baseline)
