"""Bounded, latched operational circuits. A halt forbids entries, not reductions."""
from collections import deque
import math


class RiskGuard:
    def __init__(self, *, max_ack_seconds=5., window=60., max_cancels=180,
                 minimum_orders=40, rejection_fraction=.5, adverse_bps=5., minimum_markouts=20):
        self.max_ack_seconds,self.window=max_ack_seconds,window
        self.max_cancels,self.minimum_orders=max_cancels,minimum_orders
        self.rejection_fraction,self.adverse_bps,self.minimum_markouts=rejection_fraction,adverse_bps,minimum_markouts
        self.events,self.marks,self.halted={}, {}, {}

    def event(self, venue, kind, now):
        q=self.events.setdefault(venue,deque(maxlen=4096));q.append((now,kind))
        while q and q[0][0]<now-self.window:q.popleft()
        sent=sum(k=='SEND' for _,k in q);rejects=sum(k=='REJECT' for _,k in q)
        cancels=sum(k=='CANCEL' for _,k in q)
        if cancels>self.max_cancels:self.halted.setdefault(venue,'CANCEL_RATE_LIMIT')
        if sent>=self.minimum_orders and rejects/sent>=self.rejection_fraction:
            self.halted.setdefault(venue,'REJECTION_RATE_LIMIT')
        return self.halted.get(venue)

    def acknowledgement(self, venue, sent, acknowledged):
        if acknowledged-sent>self.max_ack_seconds:self.halted.setdefault(venue,'ACK_LATENCY_LIMIT')
        return self.halted.get(venue)

    def markout(self, row):
        # One common horizon; partial fills remain diagnostics, not independent
        # significance observations. The circuit is deliberately not a p-test.
        _,venue,asset,t,h,observed,qty,price,mid,mo,fee,status=row
        if h!=1 or status!='OBSERVED' or mo is None or not math.isfinite(mo):return
        q=self.marks.setdefault(venue,deque(maxlen=100));q.append(mo-fee)
        if len(q)>=self.minimum_markouts and sum(q)/len(q)<-self.adverse_bps:
            self.halted.setdefault(venue,'ADVERSE_MAKER_MARKOUT')


def validate_latency_profile(profile, venue, require_provenance=True):
    for kind in ('entry_seconds','cancel_seconds'):
        value=profile.get(kind)
        if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or not .001<=value<=30:
            raise ValueError('Invalid latency budget')
    if profile.get('source_venue') not in (None,venue) or profile.get('proxy_venue') not in (None,venue):
        raise ValueError('Cross-venue latency substitution is forbidden')
    if require_provenance and (profile.get('source_venue')!=venue or profile.get('provenance') not in
                              ('configured_sensitivity_unmeasured','measured_testnet','measured_mainnet')):
        raise ValueError('State pilot needs explicit same-venue latency provenance')
