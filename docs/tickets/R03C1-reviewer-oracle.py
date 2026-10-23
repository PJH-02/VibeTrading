import json,runpy
from pathlib import Path
import pytest

def test_map_decoding_and_digest_share_one_generation(tmp_path,monkeypatch):
    ns=runpy.run_path('/work/tests/refactoring/test_agent_task.py')
    dev=ns['dev'];root=ns['_mapped_setup'](tmp_path);real=dev._load_json_file;fired=False
    def race(path,label='json'):
        nonlocal fired
        value=real(path,label)
        if label=='path_map.json' and not fired:
            fired=True
            replacement=json.dumps(value).replace('src/vibetrading/','other/vibetrading/')
            Path(path).write_text(replacement)
        return value
    monkeypatch.setattr(dev,'_load_json_file',race)
    try:
        packet=dev.make_packet(root,'VN01',ns['_mapped_spec']())
        dev.verify_source(root,packet)
    except ValueError:
        return  # Detected source generation drift is the fail-closed result.
    current=json.loads((root/dev.PATH_MAP_CANONICAL).read_text())
    prefix=current['root_moves'][0]['to']
    assert all(not r.startswith('src/vibetrading/') or prefix=='src/vibetrading/' for r in packet['read_paths']), 'packet scope decoded from a different generation than its pinned map digest'

def test_atomic_map_replacement_after_descriptor_read_is_rejected(tmp_path,monkeypatch):
    ns=runpy.run_path('/work/tests/refactoring/test_agent_task.py')
    dev=ns['dev'];root=ns['_mapped_setup'](tmp_path)
    target=root/dev.PATH_MAP_CANONICAL;inode=target.stat().st_ino
    replacement=target.read_text().replace('src/vibetrading/','other/vibetrading/')
    real=dev.os.read;fired=False
    def replace_after_read(fd,length):
        nonlocal fired
        data=real(fd,length)
        if data and not fired and dev.os.fstat(fd).st_ino==inode:
            fired=True
            other=target.with_suffix('.swap');other.write_text(replacement);other.replace(target)
        return data
    monkeypatch.setattr(dev.os,'read',replace_after_read)
    with pytest.raises(ValueError,match='canonical|changed'):
        dev.make_packet(root,'VN01',ns['_mapped_spec']())
    assert fired, 'descriptor replacement boundary must be exercised'
