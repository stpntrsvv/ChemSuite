"""Create a complete release directory only from verified CI bundles."""
import argparse
import json
from pathlib import Path
import shutil
from release_metadata import validate_version, sha256, installer_name


def prepare(inputs, destination, tag, commit):
    version = validate_version(tag)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError('Release destination must be empty')
    manifest = {'schema_version': 1, 'repository': 'stpntrsvv/ChemSuite', 'version': version, 'artifacts': {}}
    for target, artifact in (('macos-arm64','ChemSuite-macOS-arm64'), ('windows-x64','ChemSuite-Windows-x64')):
        source = Path(inputs) / artifact
        info = json.loads((source / 'build-info.json').read_text(encoding='utf-8'))
        if info['version'] != version or info['commit'] != commit:
            raise ValueError('CI build version or commit mismatch')
        reports = ['acceptance.json'] + (['installed-acceptance.json'] if target == 'windows-x64' else [])
        for report_name in reports:
            report = json.loads((source / report_name).read_text(encoding='utf-8'))
            if report.get('status') != 'passed' or report.get('frozen') is not True or report.get('version') != version:
                raise ValueError('Packaged acceptance did not pass')
            shutil.copy2(source / report_name, destination / (target + '-' + report_name))
        expected_hashes = {}
        for line in (source / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
            checksum, name = line.split('  ',1)
            if Path(name).name != name:
                raise ValueError('Invalid artifact path')
            expected_hashes[name] = checksum
        name = installer_name(version, target)
        paths = list(source.glob('ChemSuite-' + version + '-*'))
        if not paths or not (source / name).is_file() or not (source / f'ChemSuite-{version}-{target}-source.zip').is_file():
            raise ValueError('Missing installer or corresponding source')
        for path in paths:
            digest = sha256(path)
            if expected_hashes.get(path.name) != digest:
                raise ValueError('CI artifact checksum mismatch: ' + path.name)
            shutil.copy2(path, destination / path.name)
        manifest['artifacts'][target] = {'name': name, 'size': (source / name).stat().st_size, 'sha256': sha256(source / name)}
        shutil.copy2(source / 'build-info.json', destination / (target + '-build-info.json'))
    (destination / 'ChemSuite-update.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    hashes = ''.join(sha256(p) + '  ' + p.name + '\n' for p in sorted(destination.iterdir()) if p.is_file())
    (destination / 'SHA256SUMS').write_text(hashes, encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.inputs, args.destination, args.tag, args.commit)))
