# Upstream release trust

`buildroot-maintainer.asc` is Arnout Vandecappelle's public key, fingerprint
`18C7DF2819C1733D822D599EA500D6EE9CB0E540`. The Buildroot 2025.02.18
[clearsigned release checksum](https://buildroot.org/downloads/buildroot-2025.02.18.tar.xz.sign)
links to the [maintainer's published key](https://gitlab.com/-/snippets/4836881/raw/main/arnout@rnout.be.asc).
That signature was verified against the downloaded archive before pinning it.
Initial key provenance is limited to those HTTPS publications.

The dependency updater pins this fingerprint and rejects a signature/key change
until it is explicitly reviewed. It does not use automatic keyserver lookup or
trust arbitrary keys supplied beside a new archive. Locked builds verify the
archive's reviewed SHA-256 without network key discovery.
