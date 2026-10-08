"""Shared release version, hashes and native metadata; stdlib only."""
import hashlib
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from chem_suite import __version__  # noqa: E402
from chem_suite.updates.protocol import installer_name as installer_name, version_tuple  # noqa: E402


def validate_version(tag=None):
    version_tuple(__version__)
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    if project != __version__ or (tag is not None and tag != 'v' + __version__):
        raise ValueError('Release tag, Python package and project version must match')
    if any(v > 65535 for v in version_tuple(__version__)):
        raise ValueError('Version exceeds native Windows field limits')
    return __version__


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024*1024):
            digest.update(chunk)
    return digest.hexdigest()


def windows_version(version):
    parts = version_tuple(version) + (0,)
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={parts}, prodvers={parts}, mask=0x3f,
                    flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)),
  kids=[StringFileInfo([StringTable('040904B0',[
    StringStruct('CompanyName','ChemSuite'),
    StringStruct('FileDescription','Chem Suite'),
    StringStruct('FileVersion','{version}'),
    StringStruct('InternalName','ChemSuite'),
    StringStruct('LegalCopyright','Chem Suite contributors; GNU GPL v3 or later'),
    StringStruct('OriginalFilename','ChemSuite.exe'),
    StringStruct('ProductName','Chem Suite'),
    StringStruct('ProductVersion','{version}')])]),
    VarFileInfo([VarStruct('Translation',[1033,1200])])])
"""


if __name__ == '__main__':
    print(validate_version(sys.argv[1] if len(sys.argv) > 1 else None))
