"""Fail closed before committing staged files. Requires Gitleaks 8.x."""
from pathlib import PurePosixPath
import os
import shutil
import subprocess
import sys


scanner = os.environ.get('GITLEAKS_BIN') or shutil.which('gitleaks')
if not scanner:
    raise SystemExit('Install Gitleaks or set GITLEAKS_BIN before committing.')
names = subprocess.check_output(['git', 'diff', '--cached', '--name-only', '-z']).decode('utf-8').split('\0')
blocked = []
for name in filter(None, names):
    path = PurePosixPath(name.lower())
    if (path.suffix in {'.pem', '.key', '.p12', '.pfx', '.dump', '.backup'}
            or path.name == '.env' or path.name.startswith('.env.')
            or path.name in {'credentials.json', 'secrets.json'}
            or '-private.' in path.name or name.startswith(('data/raw/', 'data/processed/'))):
        blocked.append(name)
if blocked:
    print('Sensitive file types or private data are staged. Remove them before committing.')
    raise SystemExit(1)
diff = subprocess.check_output(['git', 'diff', '--cached', '--no-ext-diff', '--binary'])
if not diff:
    raise SystemExit('Nothing is staged. Stage the reviewed changes before scanning.')
result = subprocess.run([scanner, 'stdin', '--no-banner', '--redact'], input=diff)
if result.returncode:
    raise SystemExit(result.returncode)
print('PASS: staged file restrictions and redacted Gitleaks scan')
