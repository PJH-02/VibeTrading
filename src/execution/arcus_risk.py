"""Pure, bounded proof for Arcus USD-quoted / USDG-settled perp risk facts.

This module only decodes already fetched public snapshots. It never performs IO.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from collections.abc import Mapping
import re

DOC_ACCOUNT = "https://docs.arcus.xyz/api-reference/public/get-account"
DOC_MARKETS = "https://docs.arcus.xyz/api-reference/public/get-markets"
DOC_LEVERAGES = "https://docs.arcus.xyz/api-reference/public/get-leverage-and-margin-mode-for-an-account-across-all-markets"


def dec(value, name):
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(name + " requires exact decimal text")
    try:
        d = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(name + " is not decimal") from exc
    parts = d.as_tuple()
    if (not d.is_finite() or len(str(value)) > 128 or len(parts.digits) > 38
            or d.adjusted() > 37 or parts.exponent < -38):
        raise ValueError(name + " is invalid or unbounded")
    return d


def _bounded_mapping(obj, name):
    if not isinstance(obj, Mapping) or len(obj) > 4096:
        raise ValueError(name + " missing or over bound")
    return obj


def _bounded_tree(*roots):
    nodes, size = 0, 0
    stack = [(root, 0) for root in roots]
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > 65536 or depth > 8:
            raise ValueError("Arcus native snapshot exceeds its structural bound")
        if isinstance(value, Mapping):
            if len(value) > 4096:
                raise ValueError("Arcus native snapshot mapping exceeds its bound")
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ValueError("Arcus native snapshot keys must be strings")
                size += len(key.encode("utf-8"))
                stack.append((child, depth + 1))
        elif isinstance(value, (list, tuple)):
            if len(value) > 4096:
                raise ValueError("Arcus native snapshot sequence exceeds its bound")
            stack.extend((child, depth + 1) for child in value)
        elif isinstance(value, str):
            size += len(value.encode("utf-8"))
        elif value is not None and not isinstance(value, (int, bool)):
            raise ValueError("Arcus native snapshot contains an unsupported value")
        if size > 1_048_576:
            raise ValueError("Arcus native snapshot exceeds its byte bound")


def decode_arcus_quote_proof(account, markets, leverages, *, address, account_index, symbol,
                             local_ledger_version):
    """Validate one complete REST cut and return USD risk with explicit lineage.

    Callers must read account before and after the market/leverage reads and pass
    the unchanged account snapshot only after comparing its economic cut.
    """
    with localcontext() as ctx:
        # Each input token is capped at 128 characters. This precision exceeds
        # the exact product bounds and is independent of the caller's context.
        ctx.prec = 300
        a = _bounded_mapping(account, "Arcus account")
        _bounded_tree(a, markets, leverages)
        if (type(account_index) is not int or not 0 <= account_index <= 9
                or type(a.get("accountIndex")) is not int or a.get("accountIndex") != account_index
                or type(address) is not str or not re.fullmatch(r"(?:0[xX])?[0-9a-fA-F]{40}", address)
                or type(a.get("address")) is not str
                or not re.fullmatch(r"(?:0[xX])?[0-9a-fA-F]{40}", a["address"])
                or a["address"].casefold() != address.casefold() or len(a) > 128):
            raise ValueError("Arcus account identity mismatch")
        if type(a.get("sequenceNumber")) is not int or not 0 <= a["sequenceNumber"] < 2**64:
            raise ValueError("Arcus account sequence missing")
        if type(local_ledger_version) is not int or local_ledger_version < 0:
            raise ValueError("Explicit nonnegative local ledger version is required")
        if "marginState" in a and a.get("marginState") != "healthy":
            raise ValueError("Arcus reported margin state is not healthy")
        # This bounded quote-only proof deliberately excludes spot collateral,
        # loans and pending/unsettled cash from capacity.
        if any(key in a for key in ("borrowHeadroom", "loanBalance", "unsettledInterest",
                                     "pendingInterest", "interestAccrued", "spotPositions")):
            raise ValueError("Arcus lending footprint unsupported")
        assets = a.get("netDepositsByAsset")
        if assets is not None and (not isinstance(assets, (list, tuple)) or len(assets) > 64):
            raise ValueError("Arcus collateral asset list malformed or over bound")
        assets = assets or ()
        if any(type(x.get("spotAssetId")) is not int or x.get("spotAssetId") != 0
               for x in assets if isinstance(x, Mapping)):
            raise ValueError("Arcus spot collateral unsupported")
        if any(not isinstance(x, Mapping) for x in assets):
            raise ValueError("Arcus collateral row malformed")
        for key in ("pendingDeposits", "pendingWithdrawals"):
            if key not in a or dec(a[key], key) != 0:
                raise ValueError("Arcus unsettled account cash unsupported")
        equity, free = dec(a.get("equity"), "equity"), dec(a.get("freeCollateral"), "freeCollateral")
        if equity < 0 or free < 0 or free > equity:
            raise ValueError("Arcus equity/freeCollateral outside supported healthy bounds")
        positions = a.get("positions")
        if not isinstance(positions, Mapping) or len(positions) > 128:
            raise ValueError("Arcus complete account positions missing or over bound")
        if not isinstance(markets, (list, tuple)) or not isinstance(leverages, (list, tuple)):
            raise ValueError("Arcus complete market and leverage arrays missing")
        mkts, levs = markets, leverages
        if len(mkts) > 512 or len(levs) > 512:
            raise ValueError("Arcus market set over bound")
        by_id = {}
        for m in mkts:
            if not isinstance(m, Mapping):
                raise ValueError("Arcus market row malformed")
            mid = m.get("marketId")
            if type(mid) is not int or not 0 <= mid <= 65535 or mid in by_id:
                raise ValueError("Arcus market ID missing or duplicate")
            if type(m.get("marketDisplayName")) is not str or not 1 <= len(m["marketDisplayName"]) <= 128:
                raise ValueError("Arcus market display name missing or unbounded")
            by_id[mid] = m
        lev_by_id = {}
        for l in levs:
            if not isinstance(l, Mapping):
                raise ValueError("Arcus leverage row malformed")
            mid = l.get("marketId")
            if type(mid) is not int or not 0 <= mid <= 65535 or mid in lev_by_id:
                raise ValueError("Arcus leverage market ID missing or duplicate")
            lev_by_id[mid] = l
        if set(by_id) != set(lev_by_id):
            raise ValueError("Arcus market/leverage coverage is incomplete")
        for mid, m in by_id.items():
            l = lev_by_id[mid]
            if (l.get("marketDisplayName") != m["marketDisplayName"]
                    or type(l.get("isolated")) is not bool
                    or l.get("marginMode") not in {"CROSS", "ISOLATED"}
                    or l["isolated"] != (l["marginMode"] == "ISOLATED")
                    or type(l.get("leverage")) is not int or not 1 <= l["leverage"] <= 2**31 - 1):
                raise ValueError("Arcus native leverage identity/mode/value is invalid")

        def native_market(m, l):
            if m.get("quoteAsset") != "USD" or m.get("status") != "ONLINE":
                raise ValueError("Arcus native market quote/status unsupported")
            mmf = dec(m.get("maintenanceMarginFraction"), "maintenance fraction")
            imf = dec(m.get("offHoursInitialMarginFraction"), "off-hours initial fraction")
            normal_imf = dec(m.get("initialMarginFraction"), "initial fraction")
            if not (Decimal(0) < mmf <= 1 and Decimal(0) < normal_imf <= 1
                    and Decimal(0) < imf <= 1 and mmf <= min(normal_imf, imf)):
                raise ValueError("Arcus native margin fractions incoherent")
            for field in ("baseAsset", "tickSize", "stepSize", "minOrderSize", "minOrderNotional"):
                if field not in m:
                    raise ValueError("Arcus native market contract field missing: " + field)
            if type(m.get("baseAsset")) is not str or not m["baseAsset"]:
                raise ValueError("Arcus native base asset missing")
            tick = dec(m["tickSize"], "native tick size")
            step = dec(m["stepSize"], "native size step")
            minimum = dec(m["minOrderSize"], "native minimum size")
            notional_min = dec(m["minOrderNotional"], "native minimum notional")
            if min(tick, step, minimum) <= 0 or notional_min < 0:
                raise ValueError("Arcus native market contract increments invalid")
            return {"margin_mode": l["marginMode"].lower(), "leverage": Decimal(l["leverage"]),
                    "maintenance_fraction": mmf, "initial_fraction": normal_imf,
                    "offhours_initial_fraction": imf, "tick_size": tick, "step_size": step,
                    "min_quantity": minimum, "min_notional": notional_min, "base_asset": m["baseAsset"]}
        mm_total = Decimal(0)
        selected_mode = None
        selected_market = None
        position_rows = []
        for key, p in positions.items():
            if not isinstance(p, Mapping) or len(p) > 64:
                raise ValueError("Arcus position row malformed")
            if (type(p.get("address")) is not str or p["address"].casefold() != address.casefold()
                    or type(p.get("accountIndex")) is not int or p["accountIndex"] != account_index):
                raise ValueError("Arcus position identity mismatch")
            mid = p.get("marketId")
            if type(mid) is not int or not 0 <= mid <= 65535:
                raise ValueError("Arcus position market ID invalid")
            m, l = by_id.get(mid), lev_by_id.get(mid)
            if (m is None or l is None or type(key) is not str or key != str(mid)
                    or m.get("marketDisplayName") != p.get("marketDisplayName")):
                raise ValueError("Arcus position market identity incomplete")
            contract = native_market(m, l)
            if p.get("marginMode") not in {"CROSS", "ISOLATED"} or l.get("marginMode") != p.get("marginMode"):
                raise ValueError("Arcus native position margin mode mismatch")
            if p.get("side") not in {"LONG", "SHORT"}:
                raise ValueError("Arcus native position side missing")
            if p.get("marketDisplayName") == symbol:
                selected_mode, selected_market = p["marginMode"].lower(), m
            size = dec(p.get("size"), "position size")
            notional = dec(p.get("positionValueNotional"), "position notional")
            mark = dec(p.get("markPx"), "markPx")
            if mark <= 0 or dec(p.get("averageEntryPrice"), "entry price") <= 0:
                raise ValueError("Arcus position mark/entry unavailable")
            if size == 0:
                if notional != 0:
                    raise ValueError("Arcus zero position has nonzero notional")
                continue
            if notional != size * mark:
                raise ValueError("Arcus native notional/size/mark consistency failed")
            if (size > 0) != (p["side"] == "LONG"):
                raise ValueError("Arcus native position side/sign mismatch")
            mmf, imf, normal_imf = (contract["maintenance_fraction"], contract["offhours_initial_fraction"],
                                    contract["initial_fraction"])
            mm_total += abs(notional) * mmf
            position_rows.append({"marketId": mid, "market": p["marketDisplayName"], "size": size,
                                  "notionalUSD": notional, "markUSD": mark,
                                  "initialMarginFraction": normal_imf,
                                  "offHoursInitialMarginFraction": imf,
                                  "maintenanceFraction": mmf, "maintenanceUSD": abs(notional) * mmf,
                                  "marginMode": p["marginMode"], "leverage": dec(l["leverage"], "leverage")})
        # Validate selected product and effective account mode even when flat.
        selected_market = next((m for m in by_id.values() if m.get("marketDisplayName") == symbol), None)
        if selected_market is None:
            raise ValueError("Arcus selected market missing")
        selected_leverage = lev_by_id[selected_market["marketId"]]
        selected_contract = native_market(selected_market, selected_leverage)
        selected_mode = selected_contract["margin_mode"]
        if any(p.get("marketDisplayName") == symbol and p.get("marginMode", "").lower() != selected_mode
               for p in positions.values()):
            raise ValueError("Arcus selected native mode conflicts with open position")
        if mm_total > equity or equity - free < 0 or mm_total > equity - free:
            raise ValueError("Arcus derived maintenance exceeds native initial/equity bounds")
        initial = equity - free
        return {"proof_type": "ARCUS_NATIVE_QUOTE_USD_V1", "docs_revision": "openapi-2026.10.27",
                "sources": (DOC_ACCOUNT, DOC_MARKETS, DOC_LEVERAGES), "address": address.lower(),
                "account_index": account_index, "sequence": a["sequenceNumber"],
                "quote_currency": "USD", "settlement_currency": "USDG", "collateral_currency": "USDG",
                "equityUSD": equity, "availableUSD": free, "initialMarginUSD": initial,
                "initial_margin_basis": "DOCUMENTED_NATIVE_EQUITY_MINUS_FREE_COLLATERAL",
                "maintenanceMarginUSD": mm_total, "maintenance_margin_basis": "DOCUMENTED_DERIVED_NATIVE_REQUIREMENT",
                "position_mode": "net", "margin_mode": selected_mode, "margin_state": a.get("marginState"),
                "local_ledger_version": local_ledger_version,
                "positions": tuple(position_rows), "selected_market_id": selected_market["marketId"],
                "raw_account": dict(a), "raw_markets": tuple(dict(x) for x in mkts),
                "raw_leverages": tuple(dict(x) for x in levs)}
