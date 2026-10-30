"""Maker prices, asymmetric inventory control and explicit experimental state risk."""
from decimal import Decimal as D, ROUND_FLOOR, ROUND_CEILING
from strategies.legacy.fair_value.contracts import Intent


def risk_state(signal):
    f = signal.metadata.get("features", {})
    if not signal.ready:
        return "INVALID", 0., "DATA_NOT_READY"
    r, rv = f.get("liquidity_seconds"), f.get("rv_bps", 0)
    c = signal.metadata.get("risk_thresholds", {})
    thin = r is not None and r < float(c.get("r_low", .25))
    spread_wide = f.get("spread_bps",0) > float(c.get("spread_high", 5))
    gap = max(f.get("bid_gap_ticks") or 0, f.get("ask_gap_ticks") or 0)
    jump = abs(f.get("obi",0))>.85 and abs(f.get("ofi",0))>1.5 and (gap>=3 or spread_wide)
    if jump:
        return "JUMP_RISK", .25, "JUMP_QUEUE_AND_SPREAD"
    if thin or spread_wide or rv>float(c.get("rv_high", 4)):
        return "THIN_BOOK", .5, "LIQUIDITY_STRESSED"
    # Zero trades are inactive, never infinite liquidity or an automatic calm state.
    if r is None:
        return "NORMAL", .5, "TRADE_ACTIVITY_UNKNOWN_OR_INACTIVE"
    return "NORMAL", 1., "LIQUIDITY_NORMAL"


def state_quote_intents(signal, quote, *, cost, tick, lot, unit, inventory,
                        max_inventory, new_exposure=True, force_reduce=False,
                        position_context=None, inventory_skew=True, **kwargs):
    if not quote.valid() or tick<=0 or lot<=0:
        return []
    regime, size_scale, reason = risk_state(signal)
    inv = inventory/max_inventory if inventory_skew and max_inventory>0 else D(0)
    alpha = tick*signal.strength*D(signal.direction)
    # Fees are charged in the ledger and the S2 economic gate. Inventing a fee-
    # sized 'FV edge' here would place every tight-spread quote outside observed
    # depth and hide fill quality. Normal quotes start at BBO / inside spread.
    half = max(quote.spread/2, tick/2) * (D(2) if regime!="NORMAL" else D(1))
    center = quote.mid+alpha-tick*D(2)*max(D(-1),min(D(1),inv))
    bid = ((center-half)/tick).to_integral_value(rounding=ROUND_FLOOR)*tick
    ask = ((center+half)/tick).to_integral_value(rounding=ROUND_CEILING)*tick
    # Clamp only against the opposite side: inside-spread maker quotes are legal.
    bid = min(bid, ((quote.ask/tick).to_integral_value(rounding=ROUND_CEILING)-1)*tick)
    ask = max(ask, ((quote.bid/tick).to_integral_value(rounding=ROUND_FLOOR)+1)*tick)
    result = []
    if inventory:
        quantity = (abs(inventory)/lot).to_integral_value(rounding=ROUND_FLOOR)*lot
        # Existing inventory is managed asymmetrically; risk-off never merely
        # widens both sides and calls that liquidation.
        if inventory>0:
            if inventory_skew or force_reduce:ask = max(quote.bid+tick, min(ask, quote.ask))
            result.append(Intent("Sell",ask,quantity,"MAKER_UNWIND",True))
        else:
            if inventory_skew or force_reduce:bid = min(quote.ask-tick, max(bid, quote.bid))
            result.append(Intent("Buy",bid,quantity,"MAKER_UNWIND",True))
    if not new_exposure or force_reduce or not signal.ready or regime in ("INVALID","JUMP_RISK"):
        return [i for i in result if i.quantity>0 and i.price>0]
    # Directional modules only quote the favored side; neutral doesn't imply alpha.
    if signal.direction:
        side = "Buy" if signal.direction>0 else "Sell"
        room = max_inventory-inventory if side=="Buy" else max_inventory+inventory
        qty = min(unit*D(str(size_scale)), max(D(0),room))
        qty = (qty/lot).to_integral_value(rounding=ROUND_FLOOR)*lot
        if qty>0 and bid>0:
            result.append(Intent(side,bid if side=="Buy" else ask,qty,"STATE_QUOTE",False))
    return result
