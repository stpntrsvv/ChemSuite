# Application updates

The platform owns updates; neither EIS nor cycling imports updater code.
The protocol and streamed SHA256 validation use only the standard library.
The desktop uses asynchronous Qt Network I/O, separately from the calculation queue.

## User flow

Packaged builds check the public `stpntrsvv/ChemSuite` stable GitHub Releases feed
five seconds after startup, at most once per 24 hours. Only a strictly newer stable
`vMAJOR.MINOR.PATCH` is offered; drafts and prereleases are excluded. An automatic
notification appears once per version. Users can disable automatic checks and use
Help → Check for updates at any time. Development source launches check manually.
Offline, TLS errors, rate limits and an empty feed leave analysis usable. Automatic
failures are quiet; explicit checks show an explanation.

The dialog displays release notes as plain text, never executes release HTML, and
lets users download or postpone. Downloads are cancellable and stream to private
`.part` files with bounded memory; partial, oversized or bad-checksum files cannot
become ready installers. The completed file is verified again in an I/O thread
before launch. Installation is disabled while any calculation, queued job, cancellation
or session operation is active, and checks this condition again immediately before launch.

Windows opens the normal per-user Inno Setup wizard with the running executable's
folder as its destination and the current UI language. This also updates a writable
portable folder through the installer. Cancelling the wizard leaves the old installation.
The app closes after a successful installer launch. On macOS the verified DMG opens;
the app closes and the user drags Chem Suite to Applications to confirm replacement.
No Gatekeeper, signature or system permission checks are disabled. A future Sparkle
integration can automate Mac replacement once release signing is configured.

Save the current session before restarting: this version does not automatically restore
open tabs. The workspace, preferences, source data and saved scientific results are
outside application installation directories and are not removed by the updater.

`0.1.0` predates the updater; `0.1.1` has a Qt redirect bug. Install `0.1.2` manually once.
Further versions use this flow.
Automatic checks use no GitHub token and send only a versioned User-Agent, never lab data.
Update preferences and installers live in the user-data `updates` folder alongside,
not inside, the calculation workspace. Cancelling a download removes its partial file.

## Release contract

The latest-release REST response must have a stable tag and unambiguous uploaded assets.
The `ChemSuite-update.json` asset has schema version 1, repository, exact version and
`artifacts.macos-arm64` / `artifacts.windows-x64`, each with name, size and SHA256.
Installer names are bound to the release version/platform and URLs must belong to this
repository. Sizes and any GitHub-computed asset digests must agree with the manifest.
Only HTTPS GitHub and its named release CDN hosts are accepted, including every redirect.
Checksums detect wrong/corrupted files; they are not a substitute for publisher signing.
Trust includes GitHub's TLS, the repository and release-publishing permissions.

## Publishing a version

1. Update `__version__` and `pyproject.toml` to the same version.
2. Add `docs/releases/vX.Y.Z.md` with user-facing release notes.
3. Commit and push; create and push the matching `vX.Y.Z` tag.
4. The tag workflow validates versions, runs native source and packaged acceptance,
   verifies both platform installers and corresponding GPL source, creates the manifest,
   uploads all assets to a draft and publishes only after every upload succeeds.

The publisher refuses to change an already published release. Failed uploads leave a
draft; rerunning can complete that draft. Do not move published tags or replace published
installers: ship a new version. Main/PR builds continue to supply Actions artifacts.
Public Releases assets are the update channel, not expiring Actions artifacts.

The native Mac/Windows version resources are generated from the package version.
Release integrity and tag/platform checks run in CI. Native builds also test the real
public feed and GitHub-to-CDN redirect before publication. `tools/prepare_release.py` and
`tools/publish_release.py` implement the validation and draft-first publication.
