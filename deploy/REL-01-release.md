# REL-01 offline release workflow

This packet builds only from a clean committed Linux source tree. The candidate
manifest hashes tracked release inputs; `.deploy-release.json` and generated
release environment files are outputs and are not candidate inputs. Credentials,
runtime data and databases stay outside the context.

```sh
python3 scripts/build_release_images.py plan
python3 scripts/build_release_images.py freeze --wheel dist/vibetrading.whl --wheel-sha256 <sha256>
python3 scripts/build_release_images.py build --wheel dist/vibetrading.whl
python3 scripts/build_release_images.py verify --wheel dist/vibetrading.whl
```

`freeze` accepts a JSON `--images` receipt from `build` when verifying a completed
image set. The builder requires cached base reference and full ID pairs for app
and crypto (`VIBETRADING_APP_BASE_REF/ID`, `VIBETRADING_CRYPTO_BASE_REF/ID`). It
builds app and crypto first, ledger from the freshly built crypto image ID, then
browser from the freshly built app image ID. Docker builds use
`--pull=false --network=none` and a temporary context from the frozen allowlist.
Local build references use an ID-derived cache tag, inspected against the fixed
daemon image ID before and after the build. This avoids BuildKit interpreting a
bare `sha256:<image-id>` FROM argument as a registry image name. Cache tag
collisions fail; existing unrelated tags are never overwritten. See Docker's
[tag-by-ID documentation](https://docs.docker.com/reference/cli/docker/image/tag/).
Source-overlay images remove inherited editable installation metadata for this
project before copying the canonical source. Runtime imports use `/app/src`;
wheel identity is verified separately from the actual wheel archive.
Missing cached images/dependencies are hard failures; this workflow does not
fetch or install them. Review
`release-role-inventory.json` before choosing a profile. Root Compose, historical
crypto and release overlays, and the current server inventory remain blocked or
rollback-only until independently verified. Timefolio submission stays disabled.
The manifest digest detects accidental or adversarial edits but is not a signature
or authorization token. Verification requires reopening the wheel archive.

Never start a role as part of a build. Compose image configuration checks and
Linux image import/entrypoint checks are a separate lead-owned verification step.
