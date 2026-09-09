"""Reject missing Intel slices and dependencies newer than macOS 14."""
import re
import subprocess
from pathlib import Path

bundle = Path('dist/OKXLocal.app')
checked = 0
for path in bundle.rglob('*'):
    if not path.is_file() or path.is_symlink():
        continue
    kind = subprocess.check_output(['file', '-b', str(path)], text=True)
    if 'Mach-O' not in kind:
        continue
    checked += 1
    assert 'x86_64' in kind, f'Missing Intel architecture: {path}'
    output = subprocess.check_output(['otool', '-arch', 'x86_64', '-l', str(path)], text=True)
    versions = re.findall(r'\bminos\s+(\d+(?:\.\d+)+)', output)
    versions += re.findall(r'cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version (\d+(?:\.\d+)+)', output)
    assert versions, f'No deployment target: {path}'
    for version in versions:
        assert tuple(map(int, version.split('.'))) <= (14, 0, 0), f'{path} requires {version}'
assert checked > 5, 'App dependencies not bundled'
print(f'PASS: {checked} Mach-O files include Intel and declare macOS <= 14.0. Runtime testing on macOS 14 still required.')
