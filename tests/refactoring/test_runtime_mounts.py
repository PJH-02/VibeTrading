import json
from types import SimpleNamespace

import pytest

from runtime.runtime_paths import linux_mounts, validate_runtime_paths
from runtime.settings import RuntimeSettings


def profile(tmp_path, entry):
    contract = tmp_path/"mounts.json"
    contract.write_text(json.dumps({"schema_version":1,"mounts":[entry],"path_bindings":{name:str(tmp_path) for name in ("data_root","state_dir","parquet_root")}}), encoding="utf-8")
    return SimpleNamespace(env={"VIBETRADING_ENFORCE_MOUNTS":"true","VIBETRADING_STORAGE_MOUNTS_FILE":str(contract)},
        data_root=tmp_path, state_dir=tmp_path/"state", parquet=SimpleNamespace(data_root=tmp_path/"market"))


def line(tmp_path, *, device="8:32", options="rw"):
    return f"31 24 {device} / {tmp_path} {options} - ext4 /dev/vdc rw\n"


def test_missing_mount_blocks_before_directory_creation(tmp_path):
    settings = profile(tmp_path, {"path":str(tmp_path),"device":"8:32"})
    with pytest.raises(RuntimeError, match="not a mount point"):
        validate_runtime_paths(settings, mountinfo="")
    assert not settings.state_dir.exists()


@pytest.mark.parametrize("change", ["wrong_device", "read_only", "outside", "missing_identity"])
def test_bad_runtime_contract_fails_closed(tmp_path, change):
    entry={"path":str(tmp_path),"device":"8:32"}
    settings = profile(tmp_path, entry)
    info = line(tmp_path)
    if change == "wrong_device":
        info=line(tmp_path, device="8:0")
    elif change == "read_only":
        info=line(tmp_path, options="ro")
    elif change == "outside":
        settings.state_dir=tmp_path.parent/"outside"
    else:
        settings=profile(tmp_path, {"path":str(tmp_path)})
    with pytest.raises(RuntimeError):
        validate_runtime_paths(settings, mountinfo=info)


def test_valid_contract_checks_mount_identity_and_volume_marker(tmp_path):
    (tmp_path/".vibetrading-volume-id").write_text("fixture-volume", encoding="utf-8")
    settings=profile(tmp_path, {"path":str(tmp_path),"device":"8:32","volume_id":"fixture-volume"})
    assert validate_runtime_paths(settings, mountinfo=line(tmp_path))[0]['identity_verified']
    (tmp_path/".vibetrading-volume-id").write_text("other", encoding="utf-8")
    with pytest.raises(RuntimeError, match="marker mismatch"):
        validate_runtime_paths(settings, mountinfo=line(tmp_path))


def test_mountinfo_unescapes_space_without_shell_parsing():
    assert "/data with space" in linux_mounts(r"31 24 8:32 / /data\040with\040space rw - ext4 /dev/vdc rw")


def test_enforcement_cannot_silently_fall_back_without_contract(tmp_path):
    settings=SimpleNamespace(env={"VIBETRADING_ENFORCE_MOUNTS":"true"})
    with pytest.raises(RuntimeError, match="requires a reviewed"):
        validate_runtime_paths(settings)


def test_unreviewed_nested_mount_cannot_redirect_writes_to_root_disk(tmp_path):
    settings = profile(tmp_path, {"path":str(tmp_path),"device":"8:32"})
    info = line(tmp_path) + line(settings.state_dir, device="8:0")
    with pytest.raises(RuntimeError, match="unreviewed nested mount"):
        validate_runtime_paths(settings, mountinfo=info)
    assert not settings.state_dir.exists()


@pytest.mark.parametrize("read_only", [False, True])
def test_registered_child_mount_checks_actual_write_filesystem(tmp_path, read_only):
    settings = profile(tmp_path, {"path":str(tmp_path),"device":"8:32"})
    settings.state_dir.mkdir()
    contract = tmp_path/"mounts.json"
    document = json.loads(contract.read_text(encoding="utf-8"))
    document["mounts"].append({"path":str(settings.state_dir),"device":"8:33","writable":not read_only})
    document["path_bindings"]["state_dir"]=str(settings.state_dir)
    contract.write_text(json.dumps(document), encoding="utf-8")
    info = line(tmp_path) + line(settings.state_dir, device="8:33", options="ro" if read_only else "rw")
    if read_only:
        with pytest.raises(RuntimeError, match="data path uses a read-only mount"):
            validate_runtime_paths(settings, mountinfo=info)
    else:
        assert len(validate_runtime_paths(settings, mountinfo=info)) == 2


def test_extra_spool_path_cannot_use_another_registered_mount_under_data_binding(tmp_path):
    settings=profile(tmp_path,{'path':str(tmp_path),'device':'8:32'})
    nested=tmp_path/'tools'
    nested.mkdir()
    contract=tmp_path/'mounts.json'
    document=json.loads(contract.read_text(encoding='utf-8'))
    document['mounts'].append({'path':str(nested),'device':'8:33'})
    contract.write_text(json.dumps(document),encoding='utf-8')
    with pytest.raises(RuntimeError,match='unreviewed nested mount'):
        validate_runtime_paths(settings,mountinfo=line(tmp_path)+line(nested,device='8:33'),
            extra_data_paths=(nested/'spool',))
    assert not (nested/'spool').exists()


def test_data_path_symlink_cannot_escape_reviewed_mount(tmp_path):
    mount = tmp_path/"data"
    outside = tmp_path/"outside"
    mount.mkdir()
    outside.mkdir()
    settings = profile(mount, {"path":str(mount),"device":"8:32"})
    try:
        settings.state_dir.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    with pytest.raises(RuntimeError, match="outside reviewed storage mounts"):
        validate_runtime_paths(settings, mountinfo=line(mount))
