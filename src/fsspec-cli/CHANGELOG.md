# Changelog

## Unreleased

### Features

* **fsspec-cli:** add source-free `basename string` lexical command (#123)
* **fsspec-cli:** add optional `basename` suffix operand ([#124](https://github.com/shinybrar/vosfs/issues/124))
* **fsspec-cli:** add source-free `dirname string` lexical command (#125)
* Add mapped-file `cat` with whole-object `_get_file` staging and bounded
  binary stdout.
* **fsspec-cli:** add binary stdin and `-` sequencing for `cat`
  ([#127](https://github.com/shinybrar/vosfs/issues/127))
* **fsspec-cli:** add base `mkdir` without parent creation ([#128](https://github.com/shinybrar/vosfs/issues/128)).
  Passing results claim only source-default creation semantics, not POSIX mode
  or umask behavior.
* **fsspec-cli:** add parent-creating `mkdir -p` through `_makedirs` ([#129](https://github.com/shinybrar/vosfs/issues/129)).
  Passing results claim only source-default creation semantics, not POSIX mode
  or umask behavior.
* **fsspec-cli:** add base empty-directory `rmdir` ([#130](https://github.com/shinybrar/vosfs/issues/130))
* **fsspec-cli:** add XSI `unlink` for one mapped file ([#131](https://github.com/shinybrar/vosfs/issues/131))
* **fsspec-cli:** add base file-only `rm` ([#132](https://github.com/shinybrar/vosfs/issues/132)).
  Reuses the unlink confirmed `_rm_file` absence boundary for one or more files;
  force, directory, recursion, verbose, and interactive options remain unsupported.

* **fsspec-cli:** add verified same-source two-operand `cp` ([#137](https://github.com/shinybrar/vosfs/issues/137)).
  Passing rows prove target resolution, replacement, bytes, diagnostics, cleanup,
  and partial state only — not POSIX mode, ownership, link identity, or timestamps.

### Changed

* Parameterize private command orchestration with concrete command labels
  without adding a supported command or public runner.

## [0.1.1](https://github.com/shinybrar/vosfs/compare/fsspec-cli-v0.1.0...fsspec-cli-v0.1.1) (2026-07-17)


### Features

* **fsspec-cli:** activate independent release automation ([#148](https://github.com/shinybrar/vosfs/issues/148)) ([0a9f730](https://github.com/shinybrar/vosfs/commit/0a9f7302dcc700fcd46ccc170b47ba7640cc83ac)), closes [#107](https://github.com/shinybrar/vosfs/issues/107) [#108](https://github.com/shinybrar/vosfs/issues/108)
* **fsspec-cli:** add async Typer boundary ([#111](https://github.com/shinybrar/vosfs/issues/111)) ([adfef30](https://github.com/shinybrar/vosfs/commit/adfef30158ab1d2a72dd5f9d924d3dd65b362415)), closes [#100](https://github.com/shinybrar/vosfs/issues/100)
* **fsspec-cli:** add deterministic ls renderer ([#115](https://github.com/shinybrar/vosfs/issues/115)) ([460d9fc](https://github.com/shinybrar/vosfs/commit/460d9fc37e54d7ca4672e5131a7e102a1a28a749)), closes [#103](https://github.com/shinybrar/vosfs/issues/103)
* **fsspec-cli:** add directory listing engine ([#117](https://github.com/shinybrar/vosfs/issues/117)) ([3efeb52](https://github.com/shinybrar/vosfs/commit/3efeb52e40f700aeb6983364dfae13de75e44c72)), closes [#102](https://github.com/shinybrar/vosfs/issues/102)
* **fsspec-cli:** manage source lifecycle ([#116](https://github.com/shinybrar/vosfs/issues/116)) ([1660dbe](https://github.com/shinybrar/vosfs/commit/1660dbeff0b34083de74af210dcd17f306bd2320)), closes [#101](https://github.com/shinybrar/vosfs/issues/101)


### Bug Fixes

* **ci:** accept generated release changelogs ([308d45d](https://github.com/shinybrar/vosfs/commit/308d45d60da2b68686e6161f920c6616f5d066bd))

## 0.1.0 (2026-07-17)


### Features

* **fsspec-cli:** activate independent release automation ([#148](https://github.com/shinybrar/vosfs/issues/148)) ([0a9f730](https://github.com/shinybrar/vosfs/commit/0a9f7302dcc700fcd46ccc170b47ba7640cc83ac)), closes [#107](https://github.com/shinybrar/vosfs/issues/107) [#108](https://github.com/shinybrar/vosfs/issues/108)
* **fsspec-cli:** add async Typer boundary ([#111](https://github.com/shinybrar/vosfs/issues/111)) ([adfef30](https://github.com/shinybrar/vosfs/commit/adfef30158ab1d2a72dd5f9d924d3dd65b362415)), closes [#100](https://github.com/shinybrar/vosfs/issues/100)
* **fsspec-cli:** add deterministic ls renderer ([#115](https://github.com/shinybrar/vosfs/issues/115)) ([460d9fc](https://github.com/shinybrar/vosfs/commit/460d9fc37e54d7ca4672e5131a7e102a1a28a749)), closes [#103](https://github.com/shinybrar/vosfs/issues/103)
* **fsspec-cli:** add directory listing engine ([#117](https://github.com/shinybrar/vosfs/issues/117)) ([3efeb52](https://github.com/shinybrar/vosfs/commit/3efeb52e40f700aeb6983364dfae13de75e44c72)), closes [#102](https://github.com/shinybrar/vosfs/issues/102)
* **fsspec-cli:** manage source lifecycle ([#116](https://github.com/shinybrar/vosfs/issues/116)) ([1660dbe](https://github.com/shinybrar/vosfs/commit/1660dbeff0b34083de74af210dcd17f306bd2320)), closes [#101](https://github.com/shinybrar/vosfs/issues/101)


### Bug Fixes

* **ci:** accept generated release changelogs ([308d45d](https://github.com/shinybrar/vosfs/commit/308d45d60da2b68686e6161f920c6616f5d066bd))

## 0.0.0 (unreleased bootstrap)

No GitHub Release or tag was published for this bootstrap marker. Release
Please owns the first real `0.1.0` entry and every later version.
