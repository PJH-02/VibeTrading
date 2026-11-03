"""Explicit DATA_ONLY local engine assembly with external replay readiness.

This is a public observation assembly, not a general engine factory. Library
callers supply parsed plan/settings; the explicit CLI loads runtime settings.
Module imports perform no environment loading or account I/O.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from feeds.feed_recovery import EngineCursor
from market.local_engine import LocalMarketEngine
from runtime.local_config import LocalRuntimePlan
from runtime.local_runtime import run_engine, validate_runtime
from runtime.settings import RuntimeSettings


_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PUBLIC_PROVIDERS = frozenset({"binance-perp", "arcus-perp", "grvt-perp"})


class _ValidatedScope:
    """Identity-bound evidence that the public profile passed launcher checks."""
    __slots__ = ("plan", "settings")

    def __init__(self, plan, settings):
        self.plan, self.settings = plan, settings


def validate_public_data_only(plan: LocalRuntimePlan, settings: RuntimeSettings) -> None:
    """Reject every strategy/economic profile before runtime validation or mkdir."""
    if type(plan) is not LocalRuntimePlan or type(settings) is not RuntimeSettings:
        raise TypeError("exact local plan and settings objects are required")
    if (plan.etf_enabled is not False or plan.observation is not None
            or plan.strategy_selections != () or settings.live_order_enabled is not False
            or settings.timefolio_live_submit_enabled is not False):
        raise ValueError("only the explicit public DATA_ONLY observation profile is supported")
    if not plan.producers or any(item.delivery is None for item in plan.producers):
        raise ValueError("DATA_ONLY recovery requires explicit bounded delivery producers")
    if any(item.provider not in _PUBLIC_PROVIDERS or item.symbols != "@crypto"
           for item in plan.producers):
        raise ValueError("DATA_ONLY recovery is limited to the reviewed public crypto feeds")


def _producer_checkpoint(item, high: EngineCursor) -> bool:
    """Read only the bounded published checkpoint metadata; never scan the log."""
    from storage.market_spool import _checkpoint

    path = Path(item.spool_root) / "checkpoint.json"
    if path.is_symlink() or not path.is_file():
        return False
    durable = _checkpoint(path.parent, item.producer, item.incarnation,
                          item.limits.max_header_bytes)
    return type(durable) is int and durable >= high.sequence


def _readiness(scope: _ValidatedScope, *, clock=time.time_ns):
    if type(scope) is not _ValidatedScope:
        raise TypeError("readiness is available only from the validated DATA_ONLY launcher")
    plan, settings = scope.plan, scope.settings
    validate_public_data_only(plan, settings)
    inventory = {item.producer: item for item in plan.producers}
    proof_directory = "data-only-trials"

    def prove(engine, high_water, digest):
        try:
            if (type(engine) is not LocalMarketEngine or type(high_water) is not EngineCursor
                    or type(high_water.sequence) is not int or high_water.sequence < 0
                    or type(digest) is not str or _HEX64.fullmatch(digest) is None
                    or high_water.producer not in inventory):
                return False
            item = inventory[high_water.producer]
            if (high_water.producer != item.producer or high_water.incarnation != item.incarnation):
                return False
            receipt = engine.query_cursor(item.producer)
            if type(receipt.cursor) is not EngineCursor or receipt.cursor != high_water or receipt.digest != digest:
                return False
            stats = engine.stats
            feed = stats["feeds"][item.producer]
            if (stats["closed"] is not False or stats["queued_records"] != 0
                    or stats["queued_bytes"] != 0 or feed["state_poisoned"] is not False
                    or feed["provider"] != item.provider or feed["incarnation"] != item.incarnation
                    or feed["applied_sequence"] != high_water.sequence):
                return False
            if not _producer_checkpoint(item, high_water):
                return False
            stamp = clock()
            document = {"mode": "DATA_ONLY", "producer": item.producer,
                        "incarnation": item.incarnation, "sequence": high_water.sequence,
                        "digest": digest, "verified_at_utc": datetime.fromtimestamp(
                            stamp / 1_000_000_000, timezone.utc).isoformat(), "result": "READY"}
            payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
            if len(payload) > 1024:
                return False
            root_fd = os.open(settings.state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            proof_fd = None
            try:
                try:
                    os.mkdir(proof_directory, mode=0o700, dir_fd=root_fd)
                except FileExistsError:
                    pass
                proof_fd = os.open(proof_directory,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                directory_stat = os.fstat(proof_fd)
                if (directory_stat.st_uid != os.geteuid()
                        or directory_stat.st_mode & 0o077):
                    return False
                producer_hash = hashlib.sha256(item.producer.encode("utf-8")).hexdigest()
                filename = "proof-" + producer_hash + ".json"
                temporary = ".tmp-" + producer_hash
                try:
                    info = os.stat(filename, dir_fd=proof_fd, follow_symlinks=False)
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                            or info.st_mode & 0o077):
                        return False
                except FileNotFoundError:
                    pass
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=proof_fd)
                try:
                    view = memoryview(payload)
                    while view:
                        count = os.write(fd, view)
                        if count <= 0:
                            raise OSError("proof write made no progress")
                        view = view[count:]
                    os.fsync(fd)
                except BaseException:
                    os.unlink(temporary, dir_fd=proof_fd)
                    raise
                finally:
                    os.close(fd)
                try:
                    os.replace(temporary, filename, src_dir_fd=proof_fd, dst_dir_fd=proof_fd)
                    os.fsync(proof_fd)
                except BaseException:
                    try:
                        os.unlink(temporary, dir_fd=proof_fd)
                    except FileNotFoundError:
                        pass
                    raise
            finally:
                if proof_fd is not None:
                    os.close(proof_fd)
                os.close(root_fd)
            return True
        except (Exception,):
            return False

    return prove


async def run_public_data_only(plan: LocalRuntimePlan, settings: RuntimeSettings):
    """Validate reviewed mounts and runtime budgets before creating any path."""
    validate_public_data_only(plan, settings)
    if settings.env.get("VIBETRADING_ENFORCE_MOUNTS", "").lower() != "true":
        raise RuntimeError("DATA_ONLY requires enforced reviewed storage mounts")
    validate_runtime(plan, settings)
    # RuntimeSettings.ensure_dirs repeats mount validation immediately before mkdir.
    settings.ensure_dirs()
    callback = _readiness(_ValidatedScope(plan, settings))
    return await run_engine(plan, settings, recovery_readiness=callback)


def launch_public_data_only(plan: LocalRuntimePlan, settings: RuntimeSettings):
    """Synchronous bounded launcher for an explicitly assembled public plan."""
    return asyncio.run(run_public_data_only(plan, settings))


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Explicit public DATA_ONLY engine; no economic activation")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    os.environ["VIBETRADING_RUNTIME"] = "local"
    plan = LocalRuntimePlan.load(args.config)
    settings = RuntimeSettings.from_env()
    validate_public_data_only(plan, settings)
    if args.check:
        validate_runtime(plan, settings)
        print(json.dumps({"mode": "DATA_ONLY", "economic_activation": False,
                          "mounts_capacity_windows_verified": True}))
        return 0
    launch_public_data_only(plan, settings)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
