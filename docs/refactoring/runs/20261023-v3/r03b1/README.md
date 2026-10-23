# R03B1 acceptance checkpoint

R03B1 is ACCEPTED at source `bf75b1c5d259433fc0aa2d9b43764855837b3f68`. The parent R03 relocation is incomplete. This session resumed and completed R03B1 only, as requested.

The five packaging files add setuptools metadata, preserve legacy namespaces and pytest live-data exclusion, and make core Docker images import the canonical src package. Both images remove stale flat packages inside the build image. Product modules (all 140), requirements, historical goldens and the protected rollback script remain unchanged.

Independent verification: four focused pytest checks; wheel build/install and editable install; six imports/help checks outside the repository; three local Docker image variants (fresh app, stale-base app reuse, stale-base crypto), nine isolated container imports/help checks. Actual commands and exits are in packaging-checks.json and docker-checks.json. The extra Docker build creates only a poisoned legacy fixture base. No service, account, deployment, push or merge was run.

Initial GLM timeout and the MiMo adapter failure are preserved. The latter is an existing baseline-validator rejection of unchanged .env.example. Actual file differences were reviewed independently; the adapter failure was not relabeled success. The final bounded DeepSeek repair changed only Dockerfile and its focused test. Raw model/build/install artifacts remain private at the path recorded in acceptance.json.

Full product regression after the entire src migration, resource anchors and release/path-map consumers remain pending in parent R03. R03B2 and R03C remain PLANNED. Both canonical execution controls stop after R03B1 and prohibit starting another ticket until a new user instruction.
