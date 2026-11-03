from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from feeds.feed_recovery import EngineCursor, RecoveryFrame
from feeds.local_pipeline import DeliveryBudgets, PipelineBudgets
from market.hot_path import HotPathStore
from market.local_engine import EngineBudgets, LocalMarketEngine, _observation_noop
from runtime.local_config import LocalRuntimePlan
from runtime.settings import (ArchiveSettings, NatsSettings, ParquetSettings,
    RuntimeSettings, StorageGuardSettings)
import runtime.settings as runtime_settings
from runtime.local_transport import TransportBudgets
from storage.market_spool import MarketSpool, SpoolReader
from core.models import AssetClass, InstrumentId, TradeEvent, Venue
import runtime.data_only_runtime as assembly
from runtime.data_only_runtime import _producer_checkpoint


PRODUCER = "public-feed"
INCARNATION = "trial-1"
PROVIDER = "arcus-perp"
EMPTY = hashlib.sha256(b"").hexdigest()


def document(tmp_path, *, provider=PROVIDER, symbols="@crypto", delivery=True):
    evidence = tmp_path / "measurement.json"
    identity = {"producer": PRODUCER, "provider": provider, "symbols": symbols}
    evidence.write_text(json.dumps({"scope": "production_encoded_ingress", "sample_seconds": 1801,
        "peak_records_per_second": 1.0, "peak_encoded_bytes_per_second": 2000.0,
        "identity": identity}), encoding="utf-8")
    entry = {"producer": PRODUCER, "provider": provider, "incarnation": INCARNATION,
        "symbols": symbols, "spool_root": str(tmp_path / "data" / "spool"),
        "spool": dict(max_disk_bytes=16*1024*1024, max_records=10000, max_frame_bytes=8192,
            max_header_bytes=512, segment_max_bytes=65536, segment_max_records=128,
            segment_max_age_seconds=60),
        "pipeline": asdict(PipelineBudgets(32, 262144, 4096, 16, 1)),
        "capacity": dict(outage_seconds=1801, peak_records_per_second=1.0,
            peak_encoded_bytes_per_second=2000.0, headroom=1.0, evidence_file=str(evidence),
            evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest())}
    if delivery:
        entry["delivery"] = asdict(DeliveryBudgets(8, 32768, .01, .1, 1.0, 4))
    return {"schema_version": 1, "endpoint": str(tmp_path / "data" / "engine.sock"),
        "allowed_uid": 0, "disk_reserve_bytes": 1024,
        "transport": asdict(TransportBudgets(8192, 1024, 32, 262144, 5, 128, 262144, 1, 5)),
        "engine": asdict(EngineBudgets(1, 8, 8192, 32, 262144, 5_000_000_000,
            32, 1, 20_000_000_000, .01)), "producers": [entry], "etf_enabled": False,
        "observation": None,
        "storage": dict(batch_records=32, batch_bytes=262144, max_payload_bytes=8192,
            max_writer_buffer_bytes=1048576, max_fact_rows=10000, max_fact_db_bytes=1048576,
            poll_interval_ms=10),
        "events": dict(max_records=100, max_bytes=262144, max_frame_bytes=8192,
            max_consumers=4, poll_seconds=.01, query_timeout_seconds=1, max_nak_seconds=1,
            max_checkpoint_bytes=131072),
        "snapshot": dict(max_bytes=262144, max_instruments=8, book_depth=10,
            interval_seconds=.01, max_age_seconds=1)}


def setup(tmp_path, *, durable=0):
    tmp_path.mkdir(parents=True, exist_ok=True)
    plan = LocalRuntimePlan.from_document(document(tmp_path))
    state = tmp_path / "data" / "state"
    state.mkdir(parents=True)
    settings = RuntimeSettings(env={"VIBETRADING_ENFORCE_MOUNTS": "true"},
        project_root=tmp_path, data_root=tmp_path / "data", state_dir=state,
        log_dir=tmp_path / "data" / "logs", external_config_dir=tmp_path,
        nats=NatsSettings(), parquet=ParquetSettings(data_root=tmp_path / "data" / "parquet"),
        archive=ArchiveSettings(), storage_guard=StorageGuardSettings(), providers={},
        max_strategy_jobs=1, live_order_enabled=False, timefolio_live_submit_enabled=False,
        telegram_user_id=None)
    item = plan.producers[0]
    spool = MarketSpool(item.spool_root, item.producer, item.incarnation, **asdict(item.limits))
    if durable:
        for seq in range(1, durable + 1):
            spool.append(event(seq).model_dump_json().encode())
        spool.flush()
    budgets = EngineBudgets(**plan.engine)
    engine = LocalMarketEngine(producer_providers={PRODUCER: PROVIDER}, budgets=budgets,
        on_invalidate=_observation_noop, hot_path=HotPathStore(), observation_only=True)
    return plan, settings, spool, engine


def event(seq):
    return TradeEvent(instrument=InstrumentId(venue=Venue.ARCUS,
        asset_class=AssetClass.CRYPTO_PERP, raw_symbol="BTC-PERP", std_symbol="BTC-PERP"),
        venue=Venue.ARCUS, provider=PROVIDER,
        source_ts_ns=1_800_000_000_000_000_000 + seq,
        receive_ts_ns=1_800_000_000_000_000_000 + seq,
        wallclock_ts_ns=1_800_000_000_000_000_000 + seq,
        price=Decimal("123.45"), quantity=Decimal("2"), event_id=f"event-{seq}", sequence=seq)


def capture_callback(monkeypatch, plan, settings):
    captured = {}
    monkeypatch.setattr(assembly, "validate_runtime", lambda *_: object())
    monkeypatch.setattr(RuntimeSettings, "ensure_dirs", lambda _: None)
    async def fake_run_engine(_plan, _settings, *, recovery_readiness, observation_only):
        assert observation_only is True
        captured["callback"] = recovery_readiness
        return recovery_readiness
    monkeypatch.setattr(assembly, "run_engine", fake_run_engine)
    asyncio.run(assembly.run_public_data_only(plan, settings))
    return captured["callback"]


@pytest.fixture(autouse=True)
def restore_settings_dirs(monkeypatch):
    # Prevent any test from creating runtime paths through RuntimeSettings.
    monkeypatch.setattr(RuntimeSettings, "ensure_dirs", lambda _: None)


def test_two_frame_state_only_replay_exact_high_commit_and_bounded_proof_slot(tmp_path, monkeypatch):
    plan, settings, spool, engine = setup(tmp_path)
    try:
        item = plan.producers[0]
        spool.append(event(1).model_dump_json().encode())
        spool.append(event(2).model_dump_json().encode())
        high = EngineCursor(PRODUCER, INCARNATION, 2)
        spool.flush()
        engine.begin_recovery(EngineCursor(PRODUCER, INCARNATION, 0), high,
                              verified_checkpoint=True)
        reader = SpoolReader(item.spool_root, PRODUCER, INCARNATION, **item.limits.reader_kwargs)
        frames = reader.read_batch(max_records=2, max_bytes=item.pipeline.max_bytes)
        assert len(frames) == 2
        for frame in frames:
            result = engine.apply_replay(RecoveryFrame(
                EngineCursor(PRODUCER, INCARNATION, frame.cursor.sequence), frame.payload))
        spool.append(event(3).model_dump_json().encode())
        spool.flush()  # Published D advances to 3; the engine's captured H remains 2.
        callback = capture_callback(monkeypatch, plan, settings)
        assert callback(engine, high, result.digest) is True
        assert engine.stats["feeds"][PRODUCER]["applied_sequence"] == 2
        assert engine.stats["feeds"][PRODUCER]["mode"] == "WARMING"
        assert engine.stats["queued_records"] == engine.stats["queued_bytes"] == 0
        assert engine.hot_path.apply_seq == 2
        assert engine.commit_recovery(high, result.digest, ready=True)
        assert engine.stats["feeds"][PRODUCER]["mode"] == "LIVE"
        assert engine.hot_path.apply_seq == 2
        proof_dir = settings.state_dir / "data-only-trials"
        records = list(proof_dir.glob("*.json"))
        assert len(records) == 1
        assert len(list(proof_dir.iterdir())) == 1
        proof = json.loads(records[0].read_text())
        assert proof["mode"] == "DATA_ONLY" and proof["sequence"] == 2
        assert proof["digest"] == result.digest and proof["producer"] == PRODUCER
    finally:
        spool.close()
        asyncio.run(engine.close())


def test_published_durable_checkpoint_cannot_substitute_a_higher_engine_h(tmp_path, monkeypatch):
    plan, settings, spool, engine = setup(tmp_path)
    try:
        item = plan.producers[0]
        for seq in (1, 2):
            spool.append(event(seq).model_dump_json().encode())
            spool.flush()
        high = EngineCursor(PRODUCER, INCARNATION, 1)
        engine.begin_recovery(EngineCursor(PRODUCER, INCARNATION, 0), high, verified_checkpoint=True)
        reader = SpoolReader(item.spool_root, PRODUCER, INCARNATION, **item.limits.reader_kwargs)
        frame = reader.read_batch(max_records=1, max_bytes=item.pipeline.max_bytes)[0]
        applied = engine.apply_replay(RecoveryFrame(high, frame.payload))
        callback = capture_callback(monkeypatch, plan, settings)
        assert _producer_checkpoint(item, high)  # Durable D=2 covers, but cannot replace H=1.
        assert callback(engine, EngineCursor(PRODUCER, INCARNATION, 2), applied.digest) is False
        assert callback(engine, high, applied.digest) is True
        assert engine.stats["feeds"][PRODUCER]["applied_sequence"] == 1
    finally:
        spool.close()
        asyncio.run(engine.close())


def test_unpublished_spool_tail_cannot_authorize_higher_engine_high(tmp_path, monkeypatch):
    plan, settings, spool, engine = setup(tmp_path)
    try:
        item = plan.producers[0]
        spool.append(event(1).model_dump_json().encode())
        spool.flush()
        spool.append(event(2).model_dump_json().encode())  # Appended, not in published D.
        high = EngineCursor(PRODUCER, INCARNATION, 2)
        engine.begin_recovery(EngineCursor(PRODUCER, INCARNATION, 0), high, verified_checkpoint=True)
        callback = capture_callback(monkeypatch, plan, settings)
        assert not _producer_checkpoint(item, high)
        assert callback(engine, high, EMPTY) is False
        assert engine.stats["feeds"][PRODUCER]["mode"] == "REPLAY"
    finally:
        spool.close()
        asyncio.run(engine.close())


def test_high_zero_is_valid_with_exact_verified_state_and_published_checkpoint(tmp_path, monkeypatch):
    plan, settings, spool, engine = setup(tmp_path)
    try:
        item = plan.producers[0]
        spool.flush()
        high = EngineCursor(PRODUCER, INCARNATION, 0)
        engine.begin_recovery(high, high, verified_checkpoint=True)
        callback = capture_callback(monkeypatch, plan, settings)
        assert callback(engine, high, EMPTY) is True
        assert engine.commit_recovery(high, EMPTY, ready=True)
        assert engine.stats["feeds"][PRODUCER]["applied_sequence"] == 0
    finally:
        spool.close()
        asyncio.run(engine.close())


@pytest.mark.parametrize("fault", ["wrong_producer", "wrong_incarnation", "wrong_digest"])
def test_exact_cursor_proof_rejects_identity_or_digest_mismatch(tmp_path, monkeypatch, fault):
    plan, settings, spool, engine = setup(tmp_path, durable=1)
    try:
        high = EngineCursor(PRODUCER, INCARNATION, 1)
        engine.begin_recovery(EngineCursor(PRODUCER, INCARNATION, 0), high, verified_checkpoint=True)
        reader = SpoolReader(plan.producers[0].spool_root, PRODUCER, INCARNATION,
            **plan.producers[0].limits.reader_kwargs)
        record = reader.read_batch(max_records=1, max_bytes=plan.producers[0].pipeline.max_bytes)[0]
        applied = engine.apply_replay(RecoveryFrame(high, record.payload))
        callback = capture_callback(monkeypatch, plan, settings)
        if fault == "wrong_producer": candidate = EngineCursor("other", INCARNATION, 1); digest = applied.digest
        elif fault == "wrong_incarnation": candidate = EngineCursor(PRODUCER, "other", 1); digest = applied.digest
        else: candidate = high; digest = "x" * 64
        assert callback(engine, candidate, digest) is False
        assert engine.stats["feeds"][PRODUCER]["mode"] == "WARMING"
    finally:
        spool.close()
        asyncio.run(engine.close())


def test_poison_unverified_closed_and_mount_profile_guards_fail_closed(tmp_path, monkeypatch):
    plan, settings, spool, engine = setup(tmp_path, durable=1)
    try:
        high = EngineCursor(PRODUCER, INCARNATION, 1)
        callback = capture_callback(monkeypatch, plan, settings)
        poisoned = LocalMarketEngine(producer_providers={PRODUCER: PROVIDER},
            budgets=EngineBudgets(**plan.engine), on_invalidate=lambda *_: None,
            state_callbacks=(lambda _event, _phase: False,), trading_filter=lambda _: False)
        poisoned.begin_recovery(EngineCursor(PRODUCER, INCARNATION, 0), high,
                                verified_checkpoint=True)
        reader = SpoolReader(plan.producers[0].spool_root, PRODUCER, INCARNATION,
            **plan.producers[0].limits.reader_kwargs)
        record = reader.read_batch(max_records=1, max_bytes=plan.producers[0].pipeline.max_bytes)[0]
        with pytest.raises(Exception):
            poisoned.apply_replay(RecoveryFrame(high, record.payload))
        assert callback(poisoned, high, EMPTY) is False
        assert poisoned.stats["feeds"][PRODUCER]["state_poisoned"] is True
        asyncio.run(poisoned.close())

        unverified = LocalMarketEngine(producer_providers={PRODUCER: PROVIDER},
            budgets=EngineBudgets(**plan.engine), on_invalidate=lambda *_: None,
            hot_path=HotPathStore(), trading_filter=lambda _: False)
        assert callback(unverified, EngineCursor(PRODUCER, INCARNATION, 0), EMPTY) is False
        asyncio.run(unverified.close())
    finally:
        spool.close()
        asyncio.run(engine.close())

    plan, settings, spool, engine = setup(tmp_path / "unverified")
    try:
        callback = capture_callback(monkeypatch, plan, settings)
        assert callback(engine, EngineCursor(PRODUCER, INCARNATION, 0), EMPTY) is False
        asyncio.run(engine.close())
        assert callback(engine, EngineCursor(PRODUCER, INCARNATION, 0), EMPTY) is False
    finally:
        spool.close()

    # Real runtime validator reaches mount verification before the directory creator.
    plan, settings, spool, engine = setup(tmp_path / "mount")
    try:
        import runtime.runtime_paths as runtime_paths
        import runtime.local_runtime as local_runtime
        monkeypatch.setattr(assembly, "validate_runtime", local_runtime.validate_runtime)
        monkeypatch.setattr(runtime_paths, "validate_runtime_paths",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("mount mismatch")))
        created = []
        monkeypatch.setattr(RuntimeSettings, "ensure_dirs", lambda _: created.append(True))
        with pytest.raises(RuntimeError, match="mount mismatch"):
            asyncio.run(assembly.run_public_data_only(plan, settings))
        assert created == []
    finally:
        spool.close()
        asyncio.run(engine.close())


@pytest.mark.parametrize("profile", ["etf", "observation", "selections", "live", "timefolio", "provider", "symbol", "no_delivery"])
def test_economic_or_unreviewed_profile_is_rejected_before_launcher(tmp_path, monkeypatch, profile):
    plan, settings, spool, engine = setup(tmp_path)
    try:
        if profile == "etf": plan = replace(plan, etf_enabled=True)
        elif profile == "observation": plan = replace(plan, observation=object())
        elif profile == "selections": plan = replace(plan, strategy_selections=(object(),))
        elif profile == "live": settings = replace(settings, live_order_enabled=True)
        elif profile == "timefolio": settings = replace(settings, timefolio_live_submit_enabled=True)
        elif profile == "provider":
            plan = replace(plan, producers=(replace(plan.producers[0], provider="kiwoom"),))
        elif profile == "symbol":
            plan = replace(plan, producers=(replace(plan.producers[0], symbols="BTCUSDT"),))
        else: plan = replace(plan, producers=(replace(plan.producers[0], delivery=None),))
        called = []
        monkeypatch.setattr(assembly, "validate_runtime", lambda *_: called.append(True))
        with pytest.raises((ValueError, RuntimeError)):
            asyncio.run(assembly.run_public_data_only(plan, settings))
        assert called == []
    finally:
        spool.close()
        asyncio.run(engine.close())


def test_readiness_scope_rejects_unvalidated_fake_plan(tmp_path):
    with pytest.raises(TypeError):
        assembly._readiness(object())


def test_public_settings_allowlist_never_opens_dotenv_or_secret_file(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    dotenv = project / ".env"
    dotenv.write_text("ARCHIVE_SECRET_KEY_FILE=/unopened/secret\n")
    secret = tmp_path / "secret-key"
    secret.write_text("sentinel-secret")
    monkeypatch.setattr(assembly, "PROJECT_ROOT", project)
    monkeypatch.setattr(assembly, "DEFAULT_DATA_ROOT", project / "runtime_data")
    monkeypatch.setattr(runtime_settings, "dotenv_values",
        lambda *_args, **_kwargs: pytest.fail("public settings loaded dotenv"))
    original_read_text = Path.read_text
    def reject_secret_read(path, *args, **kwargs):
        if path == secret:
            pytest.fail("public settings opened archive secret file")
        return original_read_text(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", reject_secret_read)

    data_root = tmp_path / "data-root"
    telemetry = tmp_path / "telemetry"
    settings = assembly._public_settings_from_env({
        "VIBETRADING_DATA_ROOT": str(data_root),
        "VIBETRADING_STATE_DIR": str(tmp_path / "state"),
        "VIBETRADING_LOG_DIR": str(tmp_path / "logs"),
        "PARQUET_DATA_ROOT": str(tmp_path / "parquet"),
        "VIBETRADING_ENFORCE_MOUNTS": "1",
        "VIBETRADING_STORAGE_MOUNTS_FILE": str(tmp_path / "mounts.json"),
        "DASHBOARD_TELEMETRY_DIR": str(telemetry),
        "DASHBOARD_TELEMETRY_INTERVAL_SECONDS": "3.5",
        "LIVE_ORDER_ENABLED": "false",
        "TIMEFOLIO_LIVE_SUBMIT_ENABLED": "0",
        "ARCHIVE_SECRET_KEY_FILE": str(secret),
        "ARCUS_API_KEY": "must-not-enter-settings",
    })
    assert settings.data_root == data_root
    assert settings.state_dir == tmp_path / "state"
    assert settings.log_dir == tmp_path / "logs"
    assert settings.parquet.data_root == tmp_path / "parquet"
    assert settings.env["VIBETRADING_ENFORCE_MOUNTS"] == "true"
    assert settings.env["DASHBOARD_TELEMETRY_DIR"] == str(telemetry)
    assert settings.env["DASHBOARD_TELEMETRY_INTERVAL_SECONDS"] == "3.5"
    assert set(settings.env) <= assembly._PUBLIC_ENV_KEYS | {"VIBETRADING_RUNTIME"}
    assert settings.env["VIBETRADING_RUNTIME"] == "local"
    assert settings.archive.access_key is settings.archive.secret_key is None
    assert settings.providers == {} and settings.telegram_user_id is None
    assert settings.live_order_enabled is False and settings.timefolio_live_submit_enabled is False


@pytest.mark.parametrize("flag", ["LIVE_ORDER_ENABLED", "TIMEFOLIO_LIVE_SUBMIT_ENABLED"])
def test_public_settings_preserve_and_reject_requested_economic_activation(tmp_path, flag):
    plan, _, spool, engine = setup(tmp_path)
    try:
        settings = assembly._public_settings_from_env({flag: "true"})
        assert getattr(settings, "live_order_enabled" if flag == "LIVE_ORDER_ENABLED"
            else "timefolio_live_submit_enabled") is True
        with pytest.raises(ValueError, match="DATA_ONLY"):
            assembly.validate_public_data_only(plan, settings)
    finally:
        spool.close()
        asyncio.run(engine.close())


@pytest.mark.parametrize("name", ["VIBETRADING_DATA_ROOT", "DASHBOARD_TELEMETRY_DIR"])
def test_public_settings_reject_relative_paths(name):
    with pytest.raises(ValueError, match="absolute path"):
        assembly._public_settings_from_env({name: "relative/path"})


def test_readiness_directory_fsync_failure_leaves_no_ready_slot(tmp_path, monkeypatch):
    plan, settings, spool, engine = setup(tmp_path)
    try:
        spool.flush()
        high = EngineCursor(PRODUCER, INCARNATION, 0)
        engine.begin_recovery(high, high, verified_checkpoint=True)
        callback = capture_callback(monkeypatch, plan, settings)
        real_fsync = assembly.os.fsync
        def fail_directory_fsync(fd):
            import stat
            if stat.S_ISDIR(assembly.os.fstat(fd).st_mode):
                raise OSError("synthetic directory fsync failure")
            return real_fsync(fd)
        with monkeypatch.context() as local:
            local.setattr(assembly.os, "fsync", fail_directory_fsync)
            assert callback(engine, high, EMPTY) is False
        proof_dir = settings.state_dir / "data-only-trials"
        assert proof_dir.is_dir()
        assert list(proof_dir.glob("proof-*.json")) == []
        assert list(proof_dir.iterdir()) == []
    finally:
        spool.close()
        asyncio.run(engine.close())


def test_cli_help_exits_before_settings_or_plan_load(monkeypatch):
    import runtime.data_only_runtime as module
    monkeypatch.setattr(module, "_public_settings_from_env", lambda: pytest.fail("help read environment"))
    monkeypatch.setattr(module.LocalRuntimePlan, "load", lambda *_: pytest.fail("help read plan"))
    with pytest.raises(SystemExit) as outcome:
        module.main(["--help"])
    assert outcome.value.code == 0


def test_cli_check_validates_without_ensure_dirs_or_engine_start(tmp_path, monkeypatch, capsys):
    plan, settings, spool, engine = setup(tmp_path)
    seen = []
    monkeypatch.setattr(assembly.LocalRuntimePlan, "load", lambda *_: plan)
    monkeypatch.setattr(assembly, "_public_settings_from_env", lambda: settings)
    monkeypatch.setattr(assembly, "validate_runtime", lambda p, s: seen.append((p, s)))
    monkeypatch.setattr(assembly.RuntimeSettings, "ensure_dirs", lambda *_: pytest.fail("check created data dirs"))
    monkeypatch.setattr(assembly, "launch_public_data_only", lambda *_: pytest.fail("check launched engine"))
    monkeypatch.setenv("VIBETRADING_RUNTIME", "preserve-this-value")
    try:
        assert assembly.main(["--config", str(tmp_path / "operator-plan.json"), "--check"]) == 0
        assert seen == [(plan, settings)]
        assert __import__("os").environ["VIBETRADING_RUNTIME"] == "preserve-this-value"
        assert json.loads(capsys.readouterr().out)["economic_activation"] is False
    finally:
        spool.close()
        asyncio.run(engine.close())


def test_cli_check_requires_mount_enforcement_before_runtime_validation(tmp_path, monkeypatch):
    plan, settings, spool, engine = setup(tmp_path)
    settings = replace(settings, env={"VIBETRADING_ENFORCE_MOUNTS": "false"})
    monkeypatch.setattr(assembly.LocalRuntimePlan, "load", lambda *_: plan)
    monkeypatch.setattr(assembly, "_public_settings_from_env", lambda: settings)
    monkeypatch.setattr(assembly, "validate_runtime",
        lambda *_: pytest.fail("runtime validation ran without enforced mounts"))
    monkeypatch.setattr(assembly.RuntimeSettings, "ensure_dirs",
        lambda *_: pytest.fail("check created runtime directories"))
    try:
        with pytest.raises(RuntimeError, match="enforced reviewed storage mounts"):
            assembly.main(["--config", str(tmp_path / "operator-plan.json"), "--check"])
    finally:
        spool.close()
        asyncio.run(engine.close())


@pytest.mark.parametrize("keyword", ["etf_factory", "paper_runtime", "paper_feed_dependencies",
    "selected_action_builders", "native_selection_bindings", "checkpoint_path", "checkpoint_codec",
    "checkpoint_engine_incarnation", "checkpoint_readiness", "checkpoint_apply_order",
    "checkpoint_journal", "runtime_checkpoint", "on_command", "timefolio_config",
    "timefolio_producers", "timefolio_duration"])
def test_observation_runtime_rejects_economic_inputs_before_import_or_activation(tmp_path, monkeypatch, keyword):
    import builtins
    from runtime.local_runtime import run_engine
    plan, settings, spool, engine = setup(tmp_path)
    original_import = builtins.__import__
    def guard_import(name, *args, **kwargs):
        if name.endswith("selected_execution"):
            pytest.fail("economic assembly imported before input rejection")
        return original_import(name, *args, **kwargs)
    callback = assembly._readiness(assembly._ValidatedScope(plan, settings))
    monkeypatch.setattr(builtins, "__import__", guard_import)
    try:
        with pytest.raises(ValueError, match="economic assembly inputs"):
            asyncio.run(run_engine(plan, settings, observation_only=True,
                recovery_readiness=callback, **{keyword: object()}))
    finally:
        spool.close()
        asyncio.run(engine.close())


def test_public_readiness_rejects_ordinary_engine_and_scope_substitution(tmp_path):
    from runtime.local_runtime import run_engine
    plan, settings, spool, engine = setup(tmp_path)
    ordinary = LocalMarketEngine(producer_providers={PRODUCER: PROVIDER},
        budgets=EngineBudgets(**plan.engine), on_invalidate=lambda *_: None,
        hot_path=HotPathStore(), trading_filter=lambda _: False)
    try:
        spool.flush()
        high = EngineCursor(PRODUCER, INCARNATION, 0)
        ordinary.begin_recovery(high, high, verified_checkpoint=True)
        callback = assembly._readiness(assembly._ValidatedScope(plan, settings))
        assert callback(ordinary, high, EMPTY) is False
        with pytest.raises(ValueError, match="launcher-bound"):
            asyncio.run(run_engine(replace(plan), settings, observation_only=True,
                recovery_readiness=callback))
        with pytest.raises(TypeError, match="strict boolean"):
            asyncio.run(run_engine(plan, settings, observation_only=1))
        with pytest.raises((AttributeError, TypeError)):
            engine.observation_only = False
    finally:
        spool.close()
        asyncio.run(engine.close())
        asyncio.run(ordinary.close())


def test_module_entrypoint_preserves_canonical_readiness_identity(tmp_path, monkeypatch):
    import runpy
    import sys
    import warnings
    import runtime.local_runtime as runtime
    plan, settings, spool, engine = setup(tmp_path)
    calls = []
    async def checked_engine(p, s, *, recovery_readiness, observation_only):
        assert observation_only is True
        assembly._validate_run_engine_observation(p, s, recovery_readiness)
        calls.append((p, s))
    monkeypatch.setattr(assembly.LocalRuntimePlan, "load", lambda *_: plan)
    monkeypatch.setattr(assembly.RuntimeSettings, "ensure_dirs", lambda *_: None)
    monkeypatch.setattr(runtime, "validate_runtime", lambda *_: None)
    monkeypatch.setattr(assembly, "validate_runtime", lambda *_: None)
    monkeypatch.setattr(runtime, "run_engine", checked_engine)
    monkeypatch.setattr(assembly, "run_engine", checked_engine)
    for key, value in {"VIBETRADING_ENFORCE_MOUNTS": "true",
            "VIBETRADING_DATA_ROOT": str(settings.data_root),
            "VIBETRADING_STATE_DIR": str(settings.state_dir),
            "LIVE_ORDER_ENABLED": "false", "TIMEFOLIO_LIVE_SUBMIT_ENABLED": "false"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sys, "argv", ["runtime.data_only_runtime", "--config", str(tmp_path / "operator.json")])
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with pytest.raises(SystemExit) as result:
                runpy.run_module("runtime.data_only_runtime", run_name="__main__")
        assert result.value.code == 0 and len(calls) == 1
    finally:
        spool.close()
        asyncio.run(engine.close())
