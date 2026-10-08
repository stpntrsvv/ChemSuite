import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
import prepare_release  # noqa: E402
import publish_release  # noqa: E402
from release_metadata import validate_version, windows_version  # noqa: E402
from chem_suite import __version__  # noqa: E402
from chem_suite.updates.protocol import REPOSITORY, installer_name, parse_manifest  # noqa: E402


def bundles(folder):
    for target, artifact in [('macos-arm64','ChemSuite-macOS-arm64'), ('windows-x64','ChemSuite-Windows-x64')]:
        dest = folder / artifact
        dest.mkdir(parents=True)
        (dest/'build-info.json').write_text(json.dumps({'version':__version__,'commit':'abc123'}))
        report = {'version':__version__, 'status':'passed', 'frozen':True}
        (dest/'acceptance.json').write_text(json.dumps(report))
        (dest/'update-feed-acceptance.json').write_text(json.dumps({'status':'passed','frozen':True,'tls':True,
            'application_version':__version__, 'target':target}))
        if target == 'windows-x64':
            (dest/'installed-acceptance.json').write_text(json.dumps(report))
        (dest/installer_name(__version__,target)).write_bytes(b'installer-'+target.encode())
        (dest/f'ChemSuite-{__version__}-{target}-source.zip').write_bytes(b'corresponding-source')
        (dest/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(dest.iterdir())))


def test_release_combines_both_verified_platforms_and_source(tmp_path):
    bundles(tmp_path/'inputs')
    output = tmp_path/'release'
    manifest = prepare_release.prepare(tmp_path/'inputs', output, 'v'+__version__, 'abc123')
    assert set(manifest['artifacts']) == {'macos-arm64', 'windows-x64'}
    assert len(list(output.glob('*-source.zip'))) == 2
    raw = (output/'ChemSuite-update.json').read_bytes()
    assets = [{'name':p.name, 'size':p.stat().st_size, 'state':'uploaded',
               'digest':'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest(),
               'browser_download_url':f'https://github.com/{REPOSITORY}/releases/download/v{__version__}/{p.name}'}
              for p in output.iterdir()]
    release = {'tag_name':'v'+__version__, 'assets':assets, 'body':'Release notes'}
    for target in manifest['artifacts']:
        assert parse_manifest(raw,release,target).version == __version__
    for line in (output/'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ',1)
        assert digest == hashlib.sha256((output/name).read_bytes()).hexdigest()


@pytest.mark.parametrize('fault', ['commit','version','failed','unfrozen','installed','missing-source','checksum','traversal','feed'])
def test_release_rejects_incomplete_or_mismatched_builds(tmp_path, fault):
    bundles(tmp_path/'inputs')
    folder = tmp_path/'inputs/ChemSuite-Windows-x64'
    if fault in {'commit','version'}:
        info = json.loads((folder/'build-info.json').read_text())
        info[fault] = 'wrong'
        (folder/'build-info.json').write_text(json.dumps(info))
    elif fault in {'failed','unfrozen','installed'}:
        name = 'installed-acceptance.json' if fault == 'installed' else 'acceptance.json'
        info = json.loads((folder/name).read_text())
        info['status' if fault != 'unfrozen' else 'frozen'] = 'failed' if fault != 'unfrozen' else False
        (folder/name).write_text(json.dumps(info))
    elif fault == 'missing-source':
        next(folder.glob('*-source.zip')).unlink()
    elif fault == 'checksum':
        (folder/installer_name(__version__,'windows-x64')).write_bytes(b'corrupt')
    elif fault == 'feed':
        (folder/'update-feed-acceptance.json').write_text('{}')
    elif fault == 'traversal':
        (folder/'SHA256SUMS').write_text('0'*64+'  ../escape.exe')
    with pytest.raises(ValueError):
        prepare_release.prepare(tmp_path/'inputs', tmp_path/'release', 'v'+__version__, 'abc123')


def test_version_resources_and_tag_must_agree():
    assert validate_version('v'+__version__) == __version__
    with pytest.raises(ValueError):
        validate_version('v9.9.9')
    native = windows_version(__version__)
    assert f"StringStruct('ProductVersion','{__version__}')" in native


@pytest.fixture
def publication(tmp_path, monkeypatch):
    (tmp_path/'ChemSuite-update.json').write_text('{}')
    (tmp_path/'installer.exe').write_bytes(b'installer')
    commands = []
    monkeypatch.setattr(publish_release, 'gh', lambda *args: commands.append(args) or '')
    return tmp_path, commands


def test_new_release_publishes_only_after_upload(publication, monkeypatch):
    folder, commands = publication
    monkeypatch.setattr(publish_release.subprocess, 'run', lambda *a,**k: subprocess.CompletedProcess(a,1,'','HTTP 404'))
    publish_release.publish(REPOSITORY, 'v'+__version__, folder)
    assert [c[:2] for c in commands] == [('release','create'),('release','upload'),('release','edit')]
    assert '--draft' in commands[0]
    assert '--draft=false' in commands[-1]
    assert '--verify-tag' in commands[0]


def test_failed_upload_leaves_unpublished_draft(publication, monkeypatch):
    folder, commands = publication
    monkeypatch.setattr(publish_release.subprocess, 'run', lambda *a,**k: subprocess.CompletedProcess(a,0,'{"draft":true}',''))
    def fail(*args):
        commands.append(args)
        raise subprocess.CalledProcessError(1,args)
    monkeypatch.setattr(publish_release,'gh',fail)
    with pytest.raises(subprocess.CalledProcessError):
        publish_release.publish(REPOSITORY,'v'+__version__,folder)
    assert [c[:2] for c in commands] == [('release','upload')]


@pytest.mark.parametrize('error', ['published','offline','auth'])
def test_never_overwrites_published_or_confuses_network_failure_with_404(publication, monkeypatch, error):
    folder, commands = publication
    result = subprocess.CompletedProcess([],0,'{"draft":false}','') if error == 'published' else subprocess.CompletedProcess([],1,'','HTTP 401' if error == 'auth' else 'offline')
    monkeypatch.setattr(publish_release.subprocess, 'run', lambda *a,**k: result)
    with pytest.raises((ValueError,RuntimeError)):
        publish_release.publish(REPOSITORY,'v'+__version__,folder)
    assert not commands
