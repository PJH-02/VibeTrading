"""Compare cached metadata against the frozen pre-refactor schema oracle."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pytest

from vibetrading import parquet_store as store


ORACLE_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs/refactoring/runs/20261018-stage-assignment/schema_oracle.json"
)
ORACLE_SHA256 = "028d9166533d077aacf03cd9c74adb563a2601e0aeb1333029c7fcb8a7e0625c"
EVENT_TYPES = (
    "trade", "order_book", "bar_1m", "etf_nav", "funding_rate",
    "open_interest", "participant_flow", "index_quote",
)


@pytest.fixture(scope="module")
def oracle_cases():
    payload = ORACLE_PATH.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == ORACLE_SHA256
    cases = json.loads(payload)["cases"]
    assert {case["event_type"] for case in cases} == set(EVENT_TYPES)
    return {case["event_type"]: case for case in cases}


@pytest.fixture(autouse=True)
def isolated_metadata_caches():
    store._schema_names.cache_clear()
    store._schema.cache_clear()
    yield
    store._schema_names.cache_clear()
    store._schema.cache_clear()


def baseline_schema(case):
    encoded = base64.b64decode(case["schema_ipc_base64"])
    return pa.ipc.read_schema(pa.BufferReader(encoded))


def baseline_row(value):
    if isinstance(value, dict):
        if set(value) == {"decimal"}:
            return Decimal(value["decimal"])
        return {key: baseline_row(item) for key, item in value.items()}
    if isinstance(value, list):
        return [baseline_row(item) for item in value]
    return value


@pytest.mark.parametrize("event_type", EVENT_TYPES)
def test_schema_and_normalized_values_match_frozen_baseline(event_type, oracle_cases):
    case = oracle_cases[event_type]
    expected_schema = baseline_schema(case)
    actual_schema = store._schema(event_type)
    assert actual_schema.equals(expected_schema, check_metadata=True)
    assert [
        {"name": field.name, "type": str(field.type), "nullable": field.nullable}
        for field in actual_schema
    ] == case["schema_fields"]

    original = deepcopy(case["input_flattened_row"])
    actual = store._normalize(original, event_type)
    assert original == case["input_flattened_row"]
    assert actual == baseline_row(case["expected_typed_normalized"])
    assert list(actual) == expected_schema.names
    assert store._canon(actual).decode("utf-8") == case["canonical_json"]
    assert store.logical_hash_rows([actual]) == case["logical_sha256"]
    assert "unknown_field" not in actual


@pytest.mark.parametrize("event_type", EVENT_TYPES)
def test_materialized_arrow_rows_match_baseline_without_a_database(event_type, oracle_cases):
    case = oracle_cases[event_type]
    expected_schema = baseline_schema(case)
    expected_row = baseline_row(case["expected_typed_normalized"])
    expected = pa.RecordBatch.from_pylist([expected_row], schema=expected_schema)
    row = store._normalize(deepcopy(case["input_flattened_row"]), event_type)
    key = (event_type, "2026-10-18", "fixture-provider", "fixture-venue")
    buffer = store._PartitionBuffer(rows=[row], row_count=1, staged_bytes=len(store._canon(row)))
    writer = object.__new__(store.ParquetBatchWriter)
    writer._buffers = {key: buffer}

    writer._materialize(key)

    assert len(buffer.batches) == 1
    assert buffer.batches[0].schema.equals(expected_schema, check_metadata=True)
    assert buffer.batches[0].equals(expected)
    assert buffer.batches[0].to_pylist() == expected.to_pylist()
    assert buffer.row_count == 1
    assert buffer.rows == []
    assert buffer.staged_bytes == 0


def test_repeated_and_concurrent_types_keep_metadata_bounded_and_isolated(oracle_cases):
    def lookup(event_type):
        names = store._schema_names(event_type)
        assert isinstance(names, tuple)
        assert names == tuple(baseline_schema(oracle_cases[event_type]).names)
        assert store._schema(event_type).equals(
            baseline_schema(oracle_cases[event_type]), check_metadata=True
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lookup, EVENT_TYPES * 4))

    assert store._schema.cache_info().maxsize == len(EVENT_TYPES)
    assert store._schema.cache_info().currsize <= len(EVENT_TYPES)
    assert store._schema_names.cache_info().maxsize == len(EVENT_TYPES)
    assert store._schema_names.cache_info().currsize <= len(EVENT_TYPES)
    schema = store._schema("trade")
    names = store._schema_names("trade")
    assert store._schema("trade") is schema
    assert store._schema_names("trade") is names
    schema.names.clear()
    assert store._schema_names("trade") == tuple(baseline_schema(oracle_cases["trade"]).names)


def test_normalized_rows_do_not_share_mutable_book_levels(oracle_cases):
    case = oracle_cases["order_book"]
    original = deepcopy(case["input_flattened_row"])
    first = store._normalize(original, "order_book")
    second = store._normalize(original, "order_book")
    first["bids"].append({"price": Decimal("1"), "quantity": Decimal("1"), "order_count": None})
    first["asks"][0]["quantity"] = Decimal("99")
    first["raw_payload"] = "changed"
    assert second == baseline_row(case["expected_typed_normalized"])
    assert original == case["input_flattened_row"]
    assert store.logical_hash_rows([second]) == case["logical_sha256"]


@pytest.mark.parametrize("event_type", ("", "data_gap", "unknown-schema"))
def test_unsupported_type_keeps_value_error_and_is_not_cached(event_type):
    for operation in (store._schema, store._schema_names):
        with pytest.raises(ValueError) as exc:
            operation(event_type)
        assert str(exc.value) == f"Unsupported parquet event_type={event_type!r}"
        assert operation.cache_info().currsize == 0


@pytest.mark.parametrize("price", ("0.0000000000000000001", "100000000000000000000"))
def test_unrepresentable_decimal_still_fails_before_buffer_commit(price):
    row = store._normalize({"event_type": "trade", "price": price}, "trade")
    key = ("trade", "2026-10-18", "fixture-provider", "fixture-venue")
    buffer = store._PartitionBuffer(rows=[row], row_count=1)
    writer = object.__new__(store.ParquetBatchWriter)
    writer._buffers = {key: buffer}

    with pytest.raises(pa.ArrowInvalid):
        writer._materialize(key)

    assert buffer.rows == [row]
    assert buffer.row_count == 1
    assert buffer.batches == []


def test_missing_pyarrow_lookup_failure_is_not_cached(monkeypatch):
    def unavailable():
        raise RuntimeError("pyarrow is required for Parquet storage")

    monkeypatch.setattr(store, "_require_pyarrow", unavailable)
    with pytest.raises(RuntimeError, match="pyarrow is required for Parquet storage"):
        store._schema_names("trade")
    assert store._schema.cache_info().currsize == 0
    assert store._schema_names.cache_info().currsize == 0
