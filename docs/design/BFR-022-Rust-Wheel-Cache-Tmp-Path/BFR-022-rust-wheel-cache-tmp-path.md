# BFR-022: Rust wheel cache staged in /tmp fails tar restore, forcing a rebuild in every job

**Status**: Fixed
**Date**: 2026-07-07
**Severity**: Medium
**Affected**: `.github/actions/install-rust-wheel/action.yml`, `.github/workflows/build-rust-extension.yml`
**Related**: [PR #253](https://github.com/TongWu/JAVDB_AutoSpider_CICD/pull/253), [PR #202](https://github.com/TongWu/JAVDB_AutoSpider_CICD/pull/202) (self-hosted CI migration)

---

## Symptom

On the self-hosted fleet, every job that runs `setup-python-env` (setup, run-pipeline,
cleanup-on-failure, email-notification) rebuilt the Rust wheel from source with `maturin`,
even when a matching cache entry existed. The `rust-wheel-*` cache key reported a **hit**,
downloaded the ~1.5 MB archive to 100%, then failed to unpack:

```
Cache hit for: rust-wheel-Linux-ARM64-abi3-<hash>
Cache Size: ~2 MB (1607260 B)
/usr/bin/tar -xf .../cache.tzst -P -C /home/ghrunner/.../JAVDB_AutoSpider_CICD --use-compress-program unzstd
/usr/bin/tar: ../../../../../tmp: Cannot mkdir: Permission denied
/usr/bin/tar: ../../../../../tmp/rust-wheel: Cannot mkdir: No such file or directory
/usr/bin/tar: Exiting with failure status due to previous errors
##[warning]Failed to restore: "/usr/bin/tar" failed with error: The process '/usr/bin/tar' failed with exit code 2
Cache not found for input keys: rust-wheel-Linux-ARM64-abi3-<hash>
```

The `pip` (`~/.cache/pip`) and `cargo` (`~/.cargo`, `javdb/rust_core/target`) caches in the
same job restored fine — only the wheel cache failed.

## Root Cause

`install-rust-wheel` staged the built wheel into `/tmp/rust-wheel`, a path **outside the
workspace**. `actions/cache` archives paths relative to the workspace root and extracts with
`tar -C <workspace>`. A path above the workspace is stored as a long `../` climb
(`../../../../../tmp/rust-wheel`), so on restore `tar` tried to `mkdir` the root-owned `/tmp`
directory. On the persistent self-hosted runners the `ghrunner` user cannot create/write the
root `/tmp`, so `tar` died with `exit code 2`.

`actions/cache` treats any restore/extract failure as a cache **miss** (`cache-hit != 'true'`),
so the action fell through to its build branch and recompiled. The subsequent success-gated
save re-stored the same `/tmp`-relative layout, so the next run repeated the cycle:
hit → tar fails → rebuild → save → hit → tar fails … The cache never actually served a wheel.

`pip` and `cargo` were immune because their paths live inside `$HOME` / the workspace, which
never triggers the out-of-workspace `../` climb.

## Fix

1. Move the staging dir from `/tmp/rust-wheel` to `${RUNNER_TEMP}/rust-wheel` in
   `install-rust-wheel/action.yml` (6 path sites: clean, restore `path`, build rm+mkdir,
   `maturin build --out`, both install globs, save `path`) and in the 4 `upload-artifact`
   paths in `build-rust-extension.yml`. `$RUNNER_TEMP` (`_work/_temp`) is runner-owned and
   inside the workspace tree, so `tar` only mkdirs a writable child dir.
2. Bump the cache key `rust-wheel-*` → `rust-wheel-v2-*`. **This is required, not cosmetic:**
   GitHub cache keys are immutable, and the pre-existing archives still encode the
   `/tmp`-relative layout. Without a new key, a hit would keep extracting toward `/tmp` and
   failing, while the save is refused because the key already exists — a deadlock on the
   poisoned archive. The salt forces one clean rebuild under the new path, after which
   restores hit and unpack correctly.
3. Align the stale `test_build_rust_extension_jobs_pin_runner_arch` test (red on `main`
   independently of this bug — it still asserted the literal GitHub-hosted `runs-on` values
   from before PR #202's self-hosted migration) to assert the `[self-hosted, <arch>]` array
   form, matching `test_unit_tests_jobs_run_self_hosted`.

## Side Effects

After merge, the first `setup` on each runner rebuilds the wheel once to warm the `-v2` key
(a one-time cold start); subsequent jobs hit `Install from cached wheel` and skip the
`maturin` build. Old `rust-wheel-*` (non-v2) entries age out via GitHub's 7-day/LRU eviction;
no manual cleanup is required.

## Follow-Up

- [x] Move wheel staging to `${RUNNER_TEMP}/rust-wheel` in the action and the build workflow
- [x] Bump the wheel cache key to `rust-wheel-v2-*`
- [x] Fix the stale runner-arch contract test
- [ ] Runtime confirmation on a self-hosted runner: run `Build Rust Extension` (or any
      ingestion workflow) and verify the second run hits `Install from cached wheel` without
      rebuilding

## Lesson

Any `actions/cache` `path:` must live inside a writable base directory — the workspace,
`$HOME`, or `$RUNNER_TEMP` — never a top-level `/tmp`. `tar` extraction is workspace-relative,
so an out-of-workspace absolute path becomes a `../` climb that dies on persistent runners
where the target directory is not user-writable. A tar-restore failure is silently downgraded
to a cache miss, so the symptom (needless rebuilds) is one step removed from the cause (the
path escaped the workspace).
