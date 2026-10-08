"""Strict, versioned protocol for the public ChemSuite release feed."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import platform
import re
import sys
from urllib.parse import urlsplit, unquote
from uuid import uuid4

REPOSITORY = "stpntrsvv/ChemSuite"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
MANIFEST_NAME = "ChemSuite-update.json"
MAX_FILE_BYTES = 2_000_000_000


class UpdateError(ValueError):
    pass


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"v?(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})", value):
        raise UpdateError("Invalid release version")
    return tuple(map(int, value.removeprefix("v").split(".")))


def platform_key(system=None, machine=None):
    system = system or sys.platform
    machine = (machine or platform.machine()).lower()
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if system == "win32" and machine in {"amd64", "x86_64"}:
        return "windows-x64"
    return None


def installer_name(version, target):
    version_tuple(version)
    suffix = {"macos-arm64": ".dmg", "windows-x64": "-setup.exe"}.get(target)
    if suffix is None:
        raise UpdateError("Unsupported update platform")
    return f"ChemSuite-{version.removeprefix('v')}-{target}{suffix}"


def digest_value(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise UpdateError("Invalid update checksum")
    return value


def trusted_url(url, *, cdn=False):
    if not isinstance(url, str):
        return False
    try:
        u = urlsplit(url)
        hosts = {"api.github.com", "github.com"}
        if cdn:
            hosts |= {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}
        return u.scheme == "https" and u.hostname in hosts and not u.username and not u.password and u.port in {None, 443} and not u.fragment
    except ValueError:
        return False


def asset(release, name):
    matches = [v for v in release.get("assets", []) if isinstance(v, dict) and v.get("name") == name]
    if len(matches) != 1:
        raise UpdateError("Release asset is missing or ambiguous")
    value = matches[0]
    size = value.get("size")
    if type(size) is not int or not 0 < size <= MAX_FILE_BYTES or value.get("state") != "uploaded":
        raise UpdateError("Invalid release asset size or state")
    url = value.get("browser_download_url")
    expected = f"/{REPOSITORY}/releases/download/{release['tag_name']}/{name}"
    if not trusted_url(url) or urlsplit(url).hostname != "github.com" or unquote(urlsplit(url).path) != expected or urlsplit(url).query:
        raise UpdateError("Invalid release asset URL")
    digest = value.get("digest")
    if digest is not None:
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise UpdateError("Invalid release asset checksum")
        digest_value(digest[7:])
    return value


def parse_release(data, current_version):
    if not isinstance(data, dict) or data.get("draft") is not False or data.get("prerelease") is not False:
        raise UpdateError("Not a stable published release")
    version = data.get("tag_name")
    parsed = version_tuple(version)
    if version != "v" + version.removeprefix("v"):
        raise UpdateError("Release tags must start with v")
    if parsed <= version_tuple(current_version):
        return None
    values = data.get("assets")
    if not isinstance(values, list) or len(values) > 100:
        raise UpdateError("Invalid release assets")
    info = asset(data, MANIFEST_NAME)
    if info['size'] > 64 * 1024:
        raise UpdateError("Update manifest is too large")
    return data


@dataclass(frozen=True)
class UpdateOffer:
    version: str
    target: str
    name: str
    url: str
    size: int
    sha256: str
    release_url: str
    notes: str


def parse_manifest(raw, release, target):
    metadata = asset(release, MANIFEST_NAME)
    if len(raw) != metadata['size']:
        raise UpdateError("Update manifest size differs from release metadata")
    if metadata.get('digest') and hashlib.sha256(raw).hexdigest() != metadata['digest'][7:]:
        raise UpdateError("Update manifest checksum differs from release metadata")
    try:
        data = json.loads(raw.decode("utf-8"))
        version = release['tag_name'][1:]
        if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != 1 or data.get('repository') != REPOSITORY or data.get('version') != version:
            raise UpdateError("Invalid update manifest")
        artifact = data['artifacts'][target]
        name = installer_name(version, target)
        remote = asset(release, name)
        checksum = digest_value(artifact['sha256'])
        if artifact['name'] != name or type(artifact['size']) is not int or artifact['size'] != remote['size']:
            raise UpdateError("Update artifact differs from release metadata")
        if remote.get('digest') and remote['digest'][7:] != checksum:
            raise UpdateError("Update checksum differs from release metadata")
        notes = release.get('body') or ""
        if not isinstance(notes, str):
            raise UpdateError("Invalid release notes")
        return UpdateOffer(version, target, name, remote['browser_download_url'], remote['size'], checksum,
                           f"{RELEASES_URL}/tag/{release['tag_name']}", notes[:64*1024])
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateError("Invalid update manifest") from exc


class DownloadSink:
    """Publish a downloaded installer only after complete size and SHA256 verification."""
    def __init__(self, folder, offer):
        self.offer = offer
        self.folder = Path(folder) / offer.version
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.partial = self.folder / (uuid4().hex + ".part")
        self.file = self.partial.open("xb")
        self.digest = hashlib.sha256()
        self.received = 0
        self.cancelled = False

    def write(self, data):
        if self.cancelled or self.file.closed:
            raise UpdateError("Download is not active")
        if self.received + len(data) > self.offer.size:
            raise UpdateError("Update download exceeded its expected size")
        self.file.write(data)
        self.digest.update(data)
        self.received += len(data)

    def finish(self):
        if self.cancelled:
            raise UpdateError("Download was cancelled")
        if self.received != self.offer.size or self.digest.hexdigest() != self.offer.sha256:
            self.abort()
            raise UpdateError("Update download failed integrity verification")
        self.file.close()
        destination = self.folder / self.offer.name
        self.partial.replace(destination)
        return destination

    def abort(self):
        self.cancelled = True
        self.file.close()
        self.partial.unlink(missing_ok=True)
