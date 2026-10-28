"""Explicit, measured resource contracts for the NATS-free runtime.

No production capacity is inferred from Sunday traffic or test fixtures.
Config parsing does not create paths or contact a provider.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path

from feeds.local_pipeline import DeliveryBudgets, PipelineBudgets
from .local_transport import TransportBudgets


ACTIVE_PROVIDERS=frozenset({'kis','kiwoom','toss','binance-perp','arcus-perp','grvt-perp',
    'ls-kospi200-foreign','ls-overseas-delayed'})


def positive(value, name):
    if type(value) is not int or value<=0:raise ValueError(name+' requires a positive integer')
    return value


def absolute(value, name):
    if not isinstance(value,str) or not Path(value).is_absolute():raise ValueError(name+' requires an absolute path')
    return Path(value)


@dataclass(frozen=True)
class SpoolLimits:
    max_disk_bytes:int
    max_records:int
    max_frame_bytes:int
    max_header_bytes:int
    segment_max_bytes:int
    segment_max_records:int
    segment_max_age_seconds:float

    def __post_init__(self):
        for key,value in asdict(self).items():
            if key!='segment_max_age_seconds':positive(value,key)
        age=self.segment_max_age_seconds
        if isinstance(age,bool) or not isinstance(age,(int,float)) or not math.isfinite(age) or age<=0:
            raise ValueError('finite positive segment age required')
        if self.max_header_bytes+48>=self.max_frame_bytes or self.segment_max_bytes<self.max_frame_bytes:
            raise ValueError('inconsistent spool frame/header/segment limits')
        if self.max_disk_bytes<self.max_frame_bytes+2*(self.max_header_bytes+512)+1:
            raise ValueError('spool cannot hold one frame and checkpoint staging')

    @property
    def reader_kwargs(self):
        return {key:value for key,value in asdict(self).items() if not key.startswith('segment_')}


@dataclass(frozen=True)
class CapacityContract:
    outage_seconds:int
    peak_records_per_second:float
    peak_encoded_bytes_per_second:float
    headroom:float
    evidence_file:Path
    evidence_sha256:str

    def __post_init__(self):
        if positive(self.outage_seconds,'outage_seconds')<=1800:
            raise ValueError('independent operation must exceed 30 minutes')
        for value in (self.peak_records_per_second,self.peak_encoded_bytes_per_second,self.headroom):
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
                raise ValueError('measured positive finite capacity values required')
        if self.headroom<1:raise ValueError('capacity headroom cannot be below one')
        if not isinstance(self.evidence_sha256,str) or len(self.evidence_sha256)!=64 or any(
                char not in '0123456789abcdef' for char in self.evidence_sha256):
            raise ValueError('capacity evidence SHA256 required')

    def required(self, limits:SpoolLimits):
        records=math.ceil(self.outage_seconds*self.peak_records_per_second*self.headroom)
        rate=self.peak_encoded_bytes_per_second+self.peak_records_per_second*(48+limits.max_header_bytes)
        size=math.ceil(self.outage_seconds*rate*self.headroom)+2*(limits.max_header_bytes+512)+1
        return records,size

    def verify_evidence(self, identity):
        # Bound this metadata artifact independently of any dataset.
        with self.evidence_file.open('rb') as handle:raw=handle.read(1024*1024+1)
        if len(raw)>1024*1024 or hashlib.sha256(raw).hexdigest()!=self.evidence_sha256:
            raise ValueError('capacity measurement evidence is missing, changed or too large')
        document=json.loads(raw)
        if (document.get('scope')!='production_encoded_ingress'
                or document.get('identity')!=identity
                or document.get('peak_records_per_second')!=self.peak_records_per_second
                or document.get('peak_encoded_bytes_per_second')!=self.peak_encoded_bytes_per_second
                or type(document.get('sample_seconds')) not in (int,float)
                or not math.isfinite(document['sample_seconds'])
                or document['sample_seconds']<=1800):
            raise ValueError('capacity contract does not match production encoded-ingress measurement')


@dataclass(frozen=True)
class ProducerPlan:
    producer:str
    provider:str
    incarnation:str
    symbols:str
    spool_root:Path
    limits:SpoolLimits
    pipeline:PipelineBudgets
    capacity:CapacityContract
    delivery:DeliveryBudgets|None=None

    def __post_init__(self):
        for value in (self.producer,self.incarnation):
            if not isinstance(value,str) or not value or len(value.encode('utf-8'))>128 or any(
                    not (char.isascii() and (char.isalnum() or char in '-_')) for char in value):
                raise ValueError('producer/incarnation must be bounded path-safe identifiers')
        if self.provider not in ACTIVE_PROVIDERS:raise ValueError('provider is outside the active runtime scope')
        if not isinstance(self.symbols,str) or len(self.symbols)>4096:raise ValueError('invalid symbol selection')
        if self.pipeline.max_frame_bytes+48+self.limits.max_header_bytes>self.limits.max_frame_bytes:
            raise ValueError('spool frame must include payload plus header overhead')
        if self.limits.max_frame_bytes>self.pipeline.max_bytes:
            raise ValueError('one replay wire frame must fit the explicit ownership byte budget')
        if self.delivery is not None and (
                self.delivery.max_batch_records>self.pipeline.max_records
                or not self.limits.max_frame_bytes<=self.delivery.max_batch_bytes<=self.pipeline.max_bytes):
            raise ValueError('delivery bounds must fit the producer ownership capacity')
        records,size=self.capacity.required(self.limits)
        if records>self.limits.max_records or size>self.limits.max_disk_bytes:
            raise ValueError('spool cannot cover the measured independent operation window')

    def check_remaining(self, scan):
        records,size=self.capacity.required(self.limits)
        if (scan.uncertain or self.limits.max_records-scan.last_sequence<records
                or self.limits.max_disk_bytes-scan.disk_bytes<size):
            raise RuntimeError('remaining spool capacity cannot cover the independent operation window')


@dataclass(frozen=True)
class ObservationPlan:
    producer:str
    incarnation:str
    spool_root:Path
    limits:SpoolLimits
    pipeline:PipelineBudgets
    capacity:CapacityContract

    def check_remaining(self,scan):
        records,size=self.capacity.required(self.limits)
        if (scan.uncertain or self.limits.max_records-scan.last_sequence<records
                or self.limits.max_disk_bytes-scan.disk_bytes<size):
            raise RuntimeError('remaining observation capacity cannot cover the independent operation window')

    @classmethod
    def parse(cls,entry):
        capacity=dict(entry['capacity'])
        capacity['evidence_file']=absolute(capacity['evidence_file'],'observation capacity evidence')
        item=cls(entry['producer'],entry['incarnation'],absolute(entry['spool_root'],'observation spool'),
            SpoolLimits(**entry['spool']),PipelineBudgets(**entry['pipeline']),CapacityContract(**capacity))
        # Apply the same identity, frame-overhead and capacity checks.
        ProducerPlan(item.producer,'kis',item.incarnation,'',item.spool_root,item.limits,item.pipeline,item.capacity)
        return item


@dataclass(frozen=True)
class LocalRuntimePlan:
    endpoint:Path
    allowed_uid:int
    disk_reserve_bytes:int
    transport:TransportBudgets
    engine:dict
    producers:tuple[ProducerPlan,...]
    etf_enabled:bool
    observation:ObservationPlan|None
    storage:dict
    events:dict
    snapshot:dict

    @classmethod
    def load(cls,path:Path):
        with Path(path).open('rb') as handle:raw=handle.read(65537)
        if len(raw)>65536:raise ValueError('local runtime config exceeds 64 KiB')
        def pairs(values):
            result={}
            for key,value in values:
                if key in result:raise ValueError('duplicate config key')
                result[key]=value
            return result
        document=json.loads(raw,object_pairs_hook=pairs)
        return cls.from_document(document)

    @classmethod
    def from_document(cls,document):
        expected={'schema_version','endpoint','allowed_uid','disk_reserve_bytes','transport','engine',
                  'producers','etf_enabled','observation','storage','events','snapshot'}
        if set(document)!=expected or type(document['schema_version']) is not int or document['schema_version']!=1:
            raise ValueError('invalid NATS-free runtime config fields/version')
        endpoint=absolute(document['endpoint'],'endpoint')
        uid=document['allowed_uid']
        if type(uid) is not int or uid<0:raise ValueError('explicit Linux peer UID required')
        entries=document['producers']
        if not isinstance(entries,list) or not 0<len(entries)<=16:raise ValueError('bounded explicit producers required')
        producers=[]
        for entry in entries:
            expected_entry={'producer','provider','incarnation','symbols','spool_root','spool','pipeline','capacity'}
            if set(entry)-expected_entry-{'delivery'} or not expected_entry<=set(entry):
                raise ValueError('invalid producer config fields')
            capacity=dict(entry['capacity'])
            capacity['evidence_file']=absolute(capacity['evidence_file'],'capacity evidence')
            producers.append(ProducerPlan(entry['producer'],entry['provider'],entry['incarnation'],entry['symbols'],
                absolute(entry['spool_root'],'spool_root'),SpoolLimits(**entry['spool']),
                PipelineBudgets(**entry['pipeline']),CapacityContract(**capacity),
                DeliveryBudgets(**entry['delivery']) if entry.get('delivery') is not None else None))
        if len({p.producer for p in producers})!=len(producers) or len({p.spool_root.resolve() for p in producers})!=len(producers):
            raise ValueError('duplicate producer or spool root')
        transport=TransportBudgets(**document['transport'])
        if transport.max_sessions<len(producers):raise ValueError('transport session budget cannot cover producers')
        if any(p.pipeline.max_frame_bytes>transport.max_frame_bytes for p in producers):
            raise ValueError('transport frame budget is smaller than ingress payload budget')
        if type(document['etf_enabled']) is not bool:raise ValueError('etf_enabled requires an explicit boolean')
        observation=document['observation']
        if document['etf_enabled'] and not isinstance(observation,dict):raise ValueError('ETF requires local observation storage')
        if not isinstance(document['engine'],dict):raise ValueError('explicit engine budgets required')
        if not isinstance(document['storage'],dict):raise ValueError('explicit storage budgets required')
        if not isinstance(document['events'],dict) or not isinstance(document['snapshot'],dict):
            raise ValueError('explicit local event/snapshot budgets required')
        observed=ObservationPlan.parse(observation) if observation is not None else None
        if observed is not None and (observed.producer in {p.producer for p in producers}
                or observed.spool_root.resolve() in {p.spool_root.resolve() for p in producers}):
            raise ValueError('observation must have its own producer and spool directory')
        return cls(endpoint,uid,positive(document['disk_reserve_bytes'],'disk_reserve_bytes'),transport,
                   document['engine'],tuple(producers),document['etf_enabled'],observed,document['storage'],
                   document['events'],document['snapshot'])


def parse_execution_aliases(document, registry):
    """Parse a strict bounded strategy/alias map against an explicit registry.

    This is an added declarative parser.  It does not alter LocalRuntimePlan,
    import a venue client/SDK/feed/auth module, load Python module names from
    config, or auto-enroll Timefolio.  Validation completes before any alias is
    installed in the registry.
    """
    from collections.abc import Mapping
    from execution.execution_ledger import AccountScope
    from execution.venues.registry import MAX_ALIAS_DOCUMENT_BYTES, AliasBinding

    def text(value, name, limit=256):
        if not isinstance(value, str) or not value.strip() or len(value.encode('utf-8')) > limit:
            raise ValueError(name + ' requires a bounded explicit string')
        return value

    if isinstance(document, (str, bytes, bytearray)):
        raw = document.encode('utf-8') if isinstance(document, str) else bytes(document)
        if len(raw) > MAX_ALIAS_DOCUMENT_BYTES:
            raise ValueError('execution alias config exceeds its explicit byte limit')

        def pairs(values):
            result = {}
            for key, value in values:
                if key in result:
                    raise ValueError('duplicate execution alias/config key')
                result[key] = value
            return result

        document = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(document, Mapping):
        raise ValueError('execution aliases require a strict mapping')
    if len(document) > registry.max_aliases:
        raise ValueError('execution alias document exceeds max_aliases')
    try:
        raw_document = json.dumps(document, sort_keys=True, separators=(',', ':'),
                                  ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError) as error:
        raise ValueError('execution alias config is not bounded canonical JSON') from error
    if len(raw_document) > MAX_ALIAS_DOCUMENT_BYTES:
        raise ValueError('execution alias config exceeds its explicit byte limit')

    required = {'route_id', 'symbol', 'market_segment', 'product', 'account_scope', 'environment'}
    optional = {'required_feeds', 'execution_enabled'}
    bindings = []
    for strategy_id, aliases in document.items():
        text(strategy_id, 'strategy_id', 128)
        if not isinstance(aliases, Mapping) or not aliases:
            raise ValueError('each strategy requires an explicit alias mapping')
        for alias, spec in aliases.items():
            text(alias, 'alias', 128)
            if not isinstance(spec, Mapping):
                raise ValueError('each alias requires an explicit mapping')
            keys = set(spec)
            if not required <= keys or keys - required - optional:
                raise ValueError('invalid execution alias fields')
            route = registry.route(text(spec['route_id'], 'route_id', 128))
            symbol = text(spec['symbol'], 'symbol', 128)
            segment = text(spec['market_segment'], 'market_segment', 128)
            product = text(spec['product'], 'product', 128)
            environment = text(spec['environment'], 'environment', 128)
            scope_document = spec['account_scope']
            if isinstance(scope_document, AccountScope):
                scope = scope_document
            elif isinstance(scope_document, Mapping):
                if set(scope_document) != {'venue', 'account', 'product', 'environment'}:
                    raise ValueError('account_scope requires exactly venue/account/product/environment')
                scope = AccountScope(
                    text(scope_document['venue'], 'account_scope.venue', 200),
                    text(scope_document['account'], 'account_scope.account', 200),
                    text(scope_document['product'], 'account_scope.product', 200),
                    text(scope_document['environment'], 'account_scope.environment', 200))
            else:
                raise ValueError('account_scope requires an explicit bounded mapping')
            if (segment != route.market_segment or product != route.product
                    or environment != route.environment or scope.key != route.account_scope.key):
                raise ValueError('alias does not match the registered exact route tuple')
            feeds = spec.get('required_feeds', ())
            if not isinstance(feeds, (list, tuple)) or len(feeds) > 16:
                raise ValueError('required_feeds requires a bounded explicit list')
            feeds = tuple(text(feed, 'required_feed', 128) for feed in feeds)
            if len(set(feeds)) != len(feeds):
                raise ValueError('required_feeds must be unique')
            execution_enabled = spec.get('execution_enabled', True)
            if type(execution_enabled) is not bool:
                raise ValueError('execution_enabled requires an explicit boolean')
            bindings.append(AliasBinding(
                strategy_id=strategy_id, alias=alias, route_id=route.route_id,
                symbol=symbol, market_segment=segment, product=product,
                environment=environment, account_scope=scope,
                required_feeds=feeds, execution_enabled=execution_enabled))
    return tuple(registry.register_aliases(bindings))


# Account fee configuration remains opt-in and is never loaded by LocalRuntimePlan.
from execution.account_fees import parse_account_fee_policies, load_account_fee_policies
