import hashlib
import json

import pytest

from chem_suite.updates.protocol import (
    API_URL, DownloadSink, MANIFEST_NAME, REPOSITORY, UpdateError, asset,
    installer_name, parse_manifest, parse_release, platform_key, trusted_url, version_tuple,
)


def release_fixture(version="0.1.2", payload=b"verified installer", target="macos-arm64"):
    name = installer_name(version, target)
    manifest = {"schema_version": 1, "repository": REPOSITORY, "version": version,
                "artifacts": {target: {"name": name, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}}}
    raw = json.dumps(manifest).encode()
    def item(name, content):
        return {"name": name, "size": len(content), "state": "uploaded", "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
                "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/v{version}/{name}"}
    release = {"tag_name": "v"+version, "draft": False, "prerelease": False,
               "body": "Changes <script>never execute</script>", "assets": [item(MANIFEST_NAME, raw), item(name, payload)]}
    return release, raw, payload


def offer_fixture(payload=b"verified installer"):
    release, raw, _ = release_fixture(payload=payload)
    return parse_manifest(raw, release, "macos-arm64")


@pytest.mark.parametrize("value", ["1.0", "1.0.0-beta", "01.0.0", "1.0.-1", "1.2.٣", "1.0.0/../x", None, True])
def test_reject_nonstable_or_invalid_version(value):
    with pytest.raises(UpdateError):
        version_tuple(value)


def test_numerical_version_order_never_downgrades():
    release, _, _ = release_fixture("0.10.0")
    assert parse_release(release, "0.2.9") is release
    assert parse_release(release, "0.10.0") is None
    assert parse_release(release, "1.0.0") is None
    release['prerelease'] = True
    with pytest.raises(UpdateError):
        parse_release(release, "0.1.0")
    release['prerelease'], release['draft'] = False, True
    with pytest.raises(UpdateError):
        parse_release(release, "0.1.0")


@pytest.mark.parametrize("url", ["http://github.com/x", "https://github.com.evil.com/x", "https://github.com@evil.com/x",
                                  "https://user@github.com/x", "https://github.com:444/x", "file:///tmp/x", "https://github.com/x#fragment"])
def test_reject_untrusted_transport(url):
    assert not trusted_url(url, cdn=True)


def test_platform_selection_and_verified_manifest():
    assert platform_key("darwin", "arm64") == "macos-arm64"
    assert platform_key("win32", "AMD64") == "windows-x64"
    assert platform_key("darwin", "x86_64") is None
    assert trusted_url(API_URL)
    assert trusted_url("https://release-assets.githubusercontent.com/file?token=x", cdn=True)
    for target in ('macos-arm64', 'windows-x64'):
        release, raw, payload = release_fixture(target=target)
        offer = parse_manifest(raw, release, target)
        assert offer.size == len(payload)
        assert offer.sha256 == hashlib.sha256(payload).hexdigest()
        assert offer.notes.endswith('</script>')


@pytest.mark.parametrize("change", ["wrong-url", "duplicate", "size", "checksum", "wrong-repo", "wrong-version", "traversal", "boolean-size"])
def test_inconsistent_metadata_cannot_offer_installer(change):
    release, raw, _ = release_fixture()
    remote = release['assets'][1]
    data = json.loads(raw)
    if change == "wrong-url":
        remote['browser_download_url'] = remote['browser_download_url'].replace(REPOSITORY, 'attacker/ChemSuite')
    elif change == "duplicate":
        release['assets'].append(remote.copy())
    elif change == "size":
        remote['size'] += 1
    elif change == "checksum":
        remote['digest'] = 'sha256:' + '0'*64
    elif change == "wrong-repo":
        data['repository'] = 'attacker/ChemSuite'
    elif change == "wrong-version":
        data['version'] = '0.1.3'
    elif change == "traversal":
        data['artifacts']['macos-arm64']['name'] = '../../run.dmg'
    elif change == "boolean-size":
        data['artifacts']['macos-arm64']['size'] = True
    raw = json.dumps(data).encode()
    release['assets'][0]['size'] = len(raw)
    release['assets'][0]['digest'] = 'sha256:' + hashlib.sha256(raw).hexdigest()
    with pytest.raises(UpdateError):
        parse_manifest(raw, release, 'macos-arm64')


def test_manifest_and_asset_bounds():
    release, raw, _ = release_fixture()
    with pytest.raises(UpdateError):
        parse_manifest(raw[:-1], release, 'macos-arm64')
    release['assets'][0]['size'] = 65537
    with pytest.raises(UpdateError):
        parse_release(release, '0.1.0')
    release['assets'][1]['size'] = True
    with pytest.raises(UpdateError):
        asset(release, release['assets'][1]['name'])


def test_partial_cancelled_corrupt_and_oversized_never_publish(tmp_path):
    offer = offer_fixture()
    for payload in (b'partial', b'x'*offer.size):
        sink = DownloadSink(tmp_path, offer)
        sink.write(payload)
        with pytest.raises(UpdateError):
            sink.finish()
        assert not sink.partial.exists()
        assert not (sink.folder / offer.name).exists()
    sink = DownloadSink(tmp_path, offer)
    with pytest.raises(UpdateError):
        sink.write(b'x'*(offer.size+1))
    sink.abort()
    with pytest.raises(UpdateError):
        sink.finish()
    assert not list(tmp_path.rglob('*.part'))


def test_verified_file_published_atomically(tmp_path):
    content = b'0123456789'*100
    offer = offer_fixture(content)
    sink = DownloadSink(tmp_path, offer)
    sink.write(content[:500])
    assert not (sink.folder / offer.name).exists()
    sink.write(content[500:])
    final = sink.finish()
    assert final.read_bytes() == content
    assert not sink.partial.exists()
