"""Upload while draft, publish last, never rewrite a published release."""
import argparse
import json
from pathlib import Path
import subprocess
from release_metadata import validate_version


def gh(*args):
    return subprocess.run(['gh', *map(str,args)], check=True, capture_output=True, text=True).stdout


def publish(repository, tag, folder):
    version = validate_version(tag)
    files = sorted(p for p in Path(folder).iterdir() if p.is_file())
    if not files or not (Path(folder)/"ChemSuite-update.json").is_file():
        raise ValueError("Release manifest missing")
    # A 404 means create; auth/network/rate-limit failures must never be mistaken for absence.
    existing = subprocess.run(['gh','api',f'repos/{repository}/releases/tags/{tag}'], capture_output=True,text=True)
    if existing.returncode == 0:
        if not json.loads(existing.stdout)['draft']:
            raise ValueError('Refusing to change a published release')
    elif 'HTTP 404' in existing.stderr:
        notes = Path(__file__).resolve().parents[1] / 'docs/releases' / (tag + '.md')
        gh('release','create',tag,'--repo',repository,'--verify-tag','--draft','--title',f'Chem Suite {version}',
           '--notes-file',notes)
    else:
        raise RuntimeError(existing.stderr)
    gh('release','upload',tag,*files,'--repo',repository,'--clobber')
    gh('release','edit',tag,'--repo',repository,'--draft=false','--latest')
    print(f'https://github.com/{repository}/releases/tag/{tag}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--directory', required=True)
    args = parser.parse_args()
    publish(args.repository,args.tag,args.directory)
