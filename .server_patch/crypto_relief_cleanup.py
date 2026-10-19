"""Reclaim only three explicitly selected, inactive VS Code binary caches."""
import json
import os
from pathlib import Path
import shutil

parent = Path('/root/.vscode-server/cli/servers').resolve(strict=True)
names = ['Stable-04c0d99f4fb0d8afe6ce4f0c58e31e183ac3e4b1',
         'Stable-2242ebbb54efeeb0129e08e919e7e8d43033cd83',
         'Stable-7debcd0e2acdea1c52de81bf9ee1620444407dda']
targets = []
for name in names:
    path = parent / name
    assert not path.is_symlink() and path.resolve(strict=True).parent == parent
    assert path.is_dir()
    targets.append(path)
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    values = []
    try:
        values.append((proc / 'cmdline').read_bytes().decode(errors='replace'))
        for link in [proc / 'exe', proc / 'cwd', *list((proc / 'fd').iterdir())]:
            try:
                values.append(os.readlink(link))
            except OSError:
                pass
    except (OSError, ProcessLookupError):
        continue
    assert not any(str(path) in value for path in targets for value in values), ('active cache', proc.name)
before = shutil.disk_usage('/')
for path in targets:
    shutil.rmtree(path)
after = shutil.disk_usage('/')
print(json.dumps({'removed_inactive_binary_caches': [str(p) for p in targets],
                  'kept': str(parent / 'Stable-07f806f999227108933c2e30515b26eecc1fda74'),
                  'free_before': before.free, 'free_after': after.free,
                  'freed_bytes': after.free - before.free}))
