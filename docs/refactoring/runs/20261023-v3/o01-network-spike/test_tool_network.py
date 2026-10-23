import os
from pathlib import Path

def test_shell_has_private_network_namespace():
    assert os.readlink("/proc/self/ns/net") != 'net:[4026531840]'

def test_shell_cannot_modify_source():
    import pytest
    with pytest.raises(OSError):
        Path("/work/test_labels.py").write_text("forbidden")
