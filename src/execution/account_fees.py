"""Explicit user-supplied account fee costs for bounded CASH simulation."""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from types import MappingProxyType

from execution.execution_ledger import AccountScope, exact_product, positive_decimal
from execution.trading_contracts import ProductKind, ProductSpec


@dataclass(frozen=True)
class AccountFeePolicy:
    scope: AccountScope
    rate: Decimal

    def __post_init__(self):
        if not isinstance(self.scope, AccountScope):
            raise ValueError("Account fee scope required")
        if not isinstance(self.rate, Decimal):
            raise ValueError("Account fee rate must be an explicit Decimal")
        rate = positive_decimal(self.rate, "account fee rate", zero=True)
        if rate > 1:
            raise ValueError("Account fee rate must be within [0,1]")
        from execution.trading_contracts import bounded_identity
        bounded_identity(_marker(self.scope, rate), "fee_provenance")

    def calculate_fee(self, quantity: Decimal, price: Decimal, multiplier: Decimal) -> Decimal:
        return exact_product(exact_product(quantity, price), exact_product(multiplier, self.rate))

    def bind_product(self, scope: AccountScope, rules: ProductSpec) -> ProductSpec:
        if scope != self.scope or scope.venue.casefold() == "toss":
            raise ValueError("Account fee policy scope mismatch or unsupported venue")
        if rules.kind != ProductKind.CASH or rules.instrument.venue.value not in ({"krx", "kiwoom"} if scope.venue == "kiwoom" else {scope.venue}):
            raise ValueError("Account fee policy supports matching CASH products only")
        marker = _marker(self.scope, self.rate)
        if rules.fee_provenance.startswith("ACCOUNT_CONFIG:"):
            recovered = recover_account_fee_policy(rules, scope)
            if recovered != self:
                raise ValueError("Different account fee policy already bound")
            return rules
        from execution.trading_contracts import bounded_identity
        bounded_identity(marker, "fee_provenance")
        return replace(rules, maker_fee_rate=self.rate, taker_fee_rate=self.rate,
                       fee_provenance=marker)


def _marker(scope, rate):
    return "ACCOUNT_CONFIG:" + json.dumps({"scope": scope.key, "rate": str(rate)},
                                          sort_keys=True, separators=(",", ":"))


def recover_account_fee_policy(rules: ProductSpec, scope: AccountScope) -> AccountFeePolicy | None:
    marker = rules.fee_provenance
    if not marker.startswith("ACCOUNT_CONFIG:"):
        return None
    from execution.trading_contracts import bounded_identity
    bounded_identity(marker, "fee_provenance")
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("Duplicate account fee marker key")
            out[key] = value
        return out
    try:
        encoded = marker[len("ACCOUNT_CONFIG:"):]
        value = json.loads(encoded, object_pairs_hook=pairs)
        if (not isinstance(value, dict) or set(value) != {"scope", "rate"}
                or not isinstance(value["scope"], str) or not isinstance(value["rate"], str)
                or json.dumps(value, sort_keys=True, separators=(",", ":")) != encoded
                or value["scope"] != scope.key):
            raise ValueError
        rate = Decimal(value["rate"])
        if str(rate) != value["rate"]:
            raise ValueError
        policy = AccountFeePolicy(scope, rate)
    except (ValueError, TypeError, InvalidOperation, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed or mismatched ACCOUNT_CONFIG policy") from exc
    allowed_venues = {"krx", "kiwoom"} if scope.venue == "kiwoom" else {scope.venue}
    if (rules.maker_fee_rate != rate or rules.taker_fee_rate != rate
            or rules.kind != ProductKind.CASH or rules.instrument.venue.value not in allowed_venues):
        raise ValueError("ACCOUNT_CONFIG rules have inconsistent scope, product or fee rates")
    return policy


def parse_account_fee_policies(document):
    if not isinstance(document, dict) or set(document) != {"schema_version", "accounts"} or type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("Invalid account fee config fields/version")
    if len(json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 65536:
        raise ValueError("Account fee config exceeds 64 KiB")
    entries = document["accounts"]
    if not isinstance(entries, list) or len(entries) > 128:
        raise ValueError("Account fee config requires at most 128 accounts")
    result = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"account_scope", "rate"}:
            raise ValueError("Invalid account fee entry")
        raw_scope = entry["account_scope"]
        if not isinstance(raw_scope, dict) or set(raw_scope) != {"venue", "account", "product", "environment"}:
            raise ValueError("Invalid account fee scope")
        scope = AccountScope(**raw_scope)
        raw = entry["rate"]
        if not isinstance(raw, str):
            raise ValueError("Account fee rate must be a decimal string")
        try:
            rate = Decimal(raw)
        except InvalidOperation as exc:
            raise ValueError("Invalid account fee rate") from exc
        policy = AccountFeePolicy(scope, rate)
        if scope.key in result:
            raise ValueError("Duplicate account fee scope")
        result[scope.key] = policy
    return MappingProxyType(result)


def load_account_fee_policies(path):
    with Path(path).open("rb") as handle:
        raw = handle.read(65537)
    if len(raw) > 65536:
        raise ValueError("Account fee config exceeds 64 KiB")
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("Duplicate account fee JSON key")
            out[key] = value
        return out
    document = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Invalid JSON number")))
    return parse_account_fee_policies(document)
