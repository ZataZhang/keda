"""在可枚举进程的本机复验 RV-2，不替换生产占用检查。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tempfile

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import rv_harness as harness

harness.KC_CLI_PATH = Path(sys.executable).with_name('kc')
harness.SCENE_ROOT = Path(tempfile.mkdtemp(prefix='kc-rv2-scenes-'))
record_path = harness.EVIDENCE_ROOT / 'rv-2-marker-migration-current.txt'
record_path.write_text('', encoding='utf-8')
repository_path, scenario_environment = harness._fake_github_scene()
scenario_environment['PYTHONPATH'] = str(harness.PROJECT_ROOT / 'src')
scene_path = repository_path.parent
original_comment = str(harness._read_marker_state(scene_path)['comments'][0]['body'])


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    """运行生产 CLI，并在当前场景的独立证据中保存输出。"""
    return harness._run_process(
        record_path,
        [str(harness.KC_CLI_PATH), *arguments, '--repo', str(repository_path)],
        cwd_path=repository_path,
        environment=scenario_environment,
    )


negative_result = run_cli('run', '--issue', '17', '--dry-run', '--output', 'json')
assert negative_result.returncode == 10
assert 'actively claimed' not in (negative_result.stdout + negative_result.stderr).lower()
harness._record_line(record_path, 'EXPECTED_FAIL: unmigrated legacy claim is not recognized as actively claimed.')

preview_result = run_cli('config', 'migrate')
assert preview_result.returncode == 0, preview_result.stdout + preview_result.stderr
assert 'comment 17001' in preview_result.stdout and 'would update' in preview_result.stdout
assert str(harness._read_marker_state(scene_path)['comments'][0]['body']) == original_comment
harness._record_line(record_path, 'PASS: preview lists the old claim without changing its bytes.')

apply_result = run_cli('config', 'migrate', '--apply')
assert apply_result.returncode == 0, apply_result.stdout + apply_result.stderr
fresh_read = harness._run_process(
    record_path,
    ['gh', 'api', 'repos/rv-owner/rv-repo/issues/17/comments'],
    cwd_path=repository_path,
    environment=scenario_environment,
)
assert fresh_read.returncode == 0
fresh_comment = json.loads(fresh_read.stdout)[0][0]['body']
assert fresh_comment == original_comment.replace('<!-- iar:', '<!-- kc:')
harness._record_line(record_path, 'PASS: fresh gh process reads the migrated claim; all other bytes are unchanged.')

migrated_run = run_cli('run', '--issue', '17', '--dry-run', '--output', 'json')
assert 'actively claimed' in (migrated_run.stdout + migrated_run.stderr).lower(), migrated_run.stdout + migrated_run.stderr
harness._record_line(record_path, 'PASS: fresh kc run process recognizes the migrated active claim.')
repeat_preview = run_cli('config', 'migrate')
assert repeat_preview.returncode == 0, repeat_preview.stdout + repeat_preview.stderr
assert '0 location(s), 0 marker(s)' in repeat_preview.stdout
repeat_apply = run_cli('config', 'migrate', '--apply')
assert repeat_apply.returncode == 0, repeat_apply.stdout + repeat_apply.stderr
assert '0 location(s), 0 marker(s)' in repeat_apply.stdout
harness._record_line(record_path, 'PASS: second preview and apply perform zero marker edits.')

state_before_block = harness._read_marker_state(scene_path)
state_before_block['comments'][0]['body'] = original_comment
harness._write_marker_state(scene_path, state_before_block)
process_registry_path = Path(scenario_environment['HOME']) / '.kedacode' / 'processes.json'
process_registry_path.parent.mkdir(parents=True, exist_ok=True)
active_process = subprocess.Popen(['sleep', '30'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    process_registry_path.write_text(json.dumps({'rv-daemon': {'pid': active_process.pid}}), encoding='utf-8')
    blocked_apply = run_cli('config', 'migrate', '--apply')
    assert blocked_apply.returncode != 0
    assert 'still running' in blocked_apply.stdout + blocked_apply.stderr
    assert str(harness._read_marker_state(scene_path)['comments'][0]['body']) == original_comment
    harness._record_line(record_path, 'PASS: registered live runner PID blocks apply without editing the pending old claim.')
finally:
    active_process.terminate()
    active_process.wait(timeout=5)
    process_registry_path.unlink(missing_ok=True)

fake_gh_path = scene_path / 'bin' / 'gh'
fake_gh_source = fake_gh_path.read_text(encoding='utf-8')
fake_gh_path.write_text(
    fake_gh_source.replace(
        'print(json.dumps([[state["issue"]]], ensure_ascii=False))',
        'print(json.dumps([[]], ensure_ascii=False))',
    ),
    encoding='utf-8',
)
daemon_log_path = harness.EVIDENCE_ROOT / 'rv-2-active-daemon-current.txt'
with daemon_log_path.open('w', encoding='utf-8') as daemon_log:
    active_daemon = subprocess.Popen(
        [str(harness.KC_CLI_PATH), 'daemon', 'run', '--interval', '60', '--repo', str(repository_path)],
        cwd=repository_path,
        env=scenario_environment,
        stdout=daemon_log,
        stderr=daemon_log,
    )
    try:
        time.sleep(2)
        assert active_daemon.poll() is None, daemon_log_path.read_text(encoding='utf-8')
        daemon_blocked_apply = run_cli('config', 'migrate', '--apply')
        assert daemon_blocked_apply.returncode != 0
        assert str(active_daemon.pid) in daemon_blocked_apply.stdout + daemon_blocked_apply.stderr
        assert str(harness._read_marker_state(scene_path)['comments'][0]['body']) == original_comment
        harness._record_line(record_path, 'PASS: actual kc daemon process blocks apply through the real command scanner without changing the old claim.')
    finally:
        active_daemon.terminate()
        active_daemon.wait(timeout=10)

assert scenario_environment.get('GH_TOKEN') is None
assert scenario_environment.get('GITHUB_TOKEN') is None
print(f'PASS: RV-2 production CLI + real process safety + file-backed gh; evidence={record_path}')
