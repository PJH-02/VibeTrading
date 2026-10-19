"""Compose current source by ACTIVE module owner, not by an arbitrary image.

An ETF image may contain an old, unused Telegram/collector copy. Selecting
that copy as the source of truth would regress the actual owning service.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil


ROOT=Path(__file__).resolve().parents[2]
OWNERS={
    'vibetrading/etf_csv_sync.py':'vibetrading-etf-csv-sync-1',
    'vibetrading/providers/kis.py':'vibetrading-collectors-1',
    'vibetrading/providers/kiwoom.py':'vibetrading-collectors-1',
    'vibetrading/universe_config.py':'vibetrading-collectors-1',
    'vibetrading/runtime.py':'vibetrading-collectors-1',
    'vibetrading/telegram.py':'vibetrading-telegram-1',
    'vibetrading/telegram_trade.py':'vibetrading-telegram-1',
    'vibetrading/trade_reporting.py':'vibetrading-telegram-1',
    'vibetrading/etf_journal.py':'vibetrading-etf-mock-1',
    'vibetrading/etf_trading.py':'vibetrading-etf-mock-1',
    'vibetrading/kis_execution.py':'vibetrading-etf-mock-1',
    # AST comparison proves the only crypto/domestic difference is the
    # domestic supplement branch, which is false for CRYPTO_PERP input.
    'vibetrading/bus.py':'vibetrading-collectors-1',
    'vibetrading/market_policy.py':'vibetrading-collectors-1',
}


def build(inventory,output):
    roles={item['name']:item for item in inventory['containers'] if item.get('files')}
    primary=roles['vibetrading-etf-mock-1']
    names=set(primary['files'])|set(OWNERS)
    if not output.is_relative_to(ROOT/'tmp/refactoring') or output.exists():
        raise ValueError('choose a new source directory under tmp/refactoring')
    variants={}
    for item in roles.values():
        for name,entry in item['files'].items():variants.setdefault(name,set()).add(entry['sha256'])
    unresolved=[name for name,digests in variants.items() if len(digests)>1 and name not in OWNERS]
    if unresolved:raise ValueError('active ownership requires review: '+', '.join(sorted(unresolved)))
    output.mkdir(parents=True)
    proof={}
    for name in sorted(names):
        owner=roles[OWNERS[name]] if name in OWNERS else primary
        # Use the verified local export path recorded by the read-only collector.
        base=Path(owner['local_source_root']).resolve()
        if not base.is_relative_to(ROOT/'tmp/refactoring/server-source-20261019'):
            raise ValueError('unexpected export directory')
        source=(base/name).resolve()
        if not source.is_relative_to(base) or source.is_symlink() or source.suffix!='.py':
            raise ValueError('unexpected exported source')
        raw=source.read_bytes()
        digest=hashlib.sha256(raw).hexdigest()
        if digest!=owner['files'][name]['sha256']:raise ValueError('source export changed')
        ast.parse(raw,filename=name)
        target=output/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        proof[name]={'owner_role':owner['name'],'image_id':owner['image_id'],'sha256':digest,
            'selection':'ACTIVE_OWNER_OVERRIDE' if name in OWNERS else 'ETF_OWNER_OR_IDENTICAL_SHARED_MODULE'}
    return {'basis':'CURRENT_ACTIVE_MODULE_OWNER','source':str(output),'files':proof,
        'remote_observed_utc':inventory['remote_metadata_observed_utc'],
        'warning':'Inactive copies in other role images do not override the active owner. No server writes.'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--inventory',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    report=args.report.resolve()
    if not report.is_relative_to(ROOT/'docs/refactoring'):raise ValueError('report path must stay under docs/refactoring')
    result=build(json.loads(args.inventory.read_text(encoding='utf-8')),args.output.resolve())
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'files':len(result['files']),'source':result['source'],'basis':result['basis']}))


if __name__=='__main__':main()
