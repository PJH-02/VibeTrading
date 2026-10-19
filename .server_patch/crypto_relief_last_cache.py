"""Remove the remaining inactive VS Code binary cache, preserving the CLI."""
from pathlib import Path
import json
import os
import shutil

path = Path('/root/.vscode-server/cli/servers/Stable-07f806f999227108933c2e30515b26eecc1fda74')
assert path.resolve(strict=True) == path and not path.is_symlink()
assert path.parent == Path('/root/.vscode-server/cli/servers')
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    try:
        values = [(proc / 'cmdline').read_bytes().decode(errors='replace')]
        for link in [proc / 'exe', proc / 'cwd', *list((proc / 'fd').iterdir())]:
            try:
                values.append(os.readlink(link))
            except OSError:
                pass
        assert all(str(path) not in value for value in values), ('active binary cache', proc.name)
    except (OSError, ProcessLookupError):
        pass
before = shutil.disk_usage('/').free
shutil.rmtree(path)
print(json.dumps({'removed_inactive_binary_cache': str(path), 'freed_bytes': shutil.disk_usage('/').free - before,
                  'cli_agent_and_user_data_preserved': True}), flush=True)
