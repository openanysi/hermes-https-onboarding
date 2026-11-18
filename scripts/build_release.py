#!/usr/bin/env python3
"""Build a local, deterministic release candidate. Never publishes anything."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile
from release import VERSION, RELEASE_STATE, PRODUCTION_CLI_VERSION

ROOT = Path(__file__).resolve().parents[1]


def build(output, date):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    installer = (ROOT / 'install.sh').read_bytes()
    if f'OPENANY_SETUP_VERSION={VERSION}\n'.encode() not in installer or f'OPENANY_SETUP_STATE={RELEASE_STATE}\n'.encode() not in installer:
        raise ValueError('Bootstrap version/state must match scripts/release.py before packaging.')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
        raise ValueError('Use --date YYYY-MM-DD.')
    name = f'openany-hermes-https-onboarding-v{VERSION}.zip'
    entries = [ROOT / 'install.sh', ROOT / 'README.md', ROOT / 'GUIDE.en.md']
    for directory, suffixes in [('scripts', {'.py'}), ('tests', {'.py', '.mjs'}), ('assets', {'.png', '.md'})]:
        entries += [p for p in (ROOT / directory).rglob('*') if p.is_file() and p.suffix in suffixes and '__pycache__' not in p.parts]
    with zipfile.ZipFile(output / name, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(entries):
            if path.is_symlink():
                raise ValueError('Refusing a symlinked release source.')
            info = zipfile.ZipInfo('hermes-https-onboarding/' + str(path.relative_to(ROOT)), (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    (output / 'install.sh').write_bytes(installer)
    hashes = {}
    for filename in ['install.sh', name]:
        hashes[filename] = hashlib.sha256((output / filename).read_bytes()).hexdigest()
        (output / (filename + '.sha256')).write_text(hashes[filename] + '  ' + filename + '\n')
    manifest = dict(schema_version=1, repository='openanysi/hermes-https-onboarding', version=VERSION,
                    release_state=RELEASE_STATE, verified_at=date, verification_scope='local_tests',
                    installer_sha256=hashes['install.sh'], archive_sha256=hashes[name],
                    cli_version=PRODUCTION_CLI_VERSION)
    (output / 'hermes-release.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--date', required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.date), indent=2))
