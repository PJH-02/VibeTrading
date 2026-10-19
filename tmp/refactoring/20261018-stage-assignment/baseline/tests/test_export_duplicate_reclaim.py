from __future__ import annotations

import hashlib
import json
import tarfile

import pytest

from scripts.reclaim_export_duplicates import verify


@pytest.fixture
def exported(tmp_path):
    name = 'historical-export'
    directory = tmp_path / name
    directory.mkdir()
    (directory / 'data.parquet').write_bytes(b'preserved raw export data')
    (directory / 'export_metadata.json').write_text(json.dumps({'files': 1}))
    archive = tmp_path / (name + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(directory, arcname=name)
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    (directory / 'export_metadata.json').write_text(json.dumps({'files': 1, 'archive': {'sha256': sha}}))
    return tmp_path.resolve(), name, sha, archive.stat().st_size


def test_preserved_archive_contains_every_extracted_file(exported):
    result = verify(*exported, lambda count: None)
    assert len(result['files']) == 2
    assert result['export_metadata'] == {'files': 1}


def test_changed_extracted_data_is_not_reclaimable(exported):
    root, name, *_ = exported
    (root / name / 'data.parquet').write_bytes(b'changed raw export data!')
    with pytest.raises(RuntimeError, match='differs'):
        verify(*exported, lambda count: None)
    assert (root / name).exists()


def test_extra_unarchived_file_is_not_reclaimable(exported):
    root, name, *_ = exported
    (root / name / 'unarchived.data').write_bytes(b'must preserve')
    with pytest.raises(RuntimeError, match='file set mismatch'):
        verify(*exported, lambda count: None)


def test_archive_digest_must_match_existing_known_export(exported):
    root, name, _, size = exported
    with pytest.raises(RuntimeError, match='checksum'):
        verify(root, name, '0' * 64, size, lambda count: None)
