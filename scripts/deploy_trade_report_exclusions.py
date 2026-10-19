"""Deploy tested reporting changes to Telegram only; keep collectors running."""
import json
import subprocess
import time
from pathlib import Path

release=Path('/opt/vibetrading/releases/telegram-recovery-20261014')

def run(*args):
    return subprocess.check_output(args,text=True).strip()

subprocess.check_call(['python3','/tmp/restore_vibetrading_server.py','prepare'])
build=json.loads((release/'build.json').read_text()); image=build['image']
tests=run('docker','run','--rm','--network=none','--memory=768m',
    '-v','/root/vibetrading/tests:/app/tests:ro',image,'python','-m','pytest','-p','no:cacheprovider',
    '/app/tests/test_trade_exclusions.py','/app/tests/test_telegram_commands.py','/app/tests/test_telegram_status.py',
    '/app/tests/test_publish_and_mock_notifications.py','/app/tests/test_dual_mock_and_assumed.py','/app/tests/test_etf_mock_trading.py','-q')
assert 'passed' in tests and 'failed' not in tests
data='/srv/vibetrading-data/runtime_data'
base=['docker','run','--rm','--network=none','--memory=256m','-e','VIBETRADING_DATA_ROOT=/data','-v',data+':/data']
policy=json.loads(run(*base,'-v','/tmp/exclude_mock_tests_20261014.py:/app/exclude_tests.py:ro',image,'python','/app/exclude_tests.py'))
report=run(*base,image,'python','-c','from vibetrading.settings import get_settings; from vibetrading.telegram_trade import trade_report; print(trade_report(get_settings(),"2026-10-14"))')
assert '실제 매수' not in report and '실제 매도' not in report and '실제 검증' not in report
assert '가정 매수 385주' in report and '가정 매수 10,863주' in report
assert '-882,574.47원' in report and '17,419.47원' in report
assert policy['raw_broker_trade_count']==6 and policy['excluded_count']==6
subprocess.check_call(['docker','stop','--timeout','5','vibetrading-telegram-1'])
subprocess.check_call(['python3','/tmp/activate_telegram_commands.py','telegram'])
subprocess.check_call(['docker','cp','/tmp/verify_filtered_trade_reply.py','vibetrading-telegram-1:/tmp/verify_filtered_trade_reply.py'])
delivery=json.loads(run('docker','exec','-e','PYTHONPATH=/app','vibetrading-telegram-1','python','/tmp/verify_filtered_trade_reply.py'))
assert delivery['trade_reply_delivered']
receipt={'verified_ns':time.time_ns(),'image':image,'revision':build['revision'],'tests':tests,
    'excluded_tests':policy,'trade_report':report,'telegram':delivery,'restarted_services':['telegram']}
(release/'test-exclusion-verification.json').write_text(json.dumps(receipt,indent=2,ensure_ascii=False))
print(json.dumps(receipt,ensure_ascii=False))
