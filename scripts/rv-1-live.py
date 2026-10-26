"""在用户授权的私有沙箱中验证创建、读回及依赖状态，不使用接口替身。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import tempfile

from backend.core.shared.models.agent_runner import LabelConfig
from backend.core.use_cases.agent_runner_dependencies import (
    evaluate_dependencies,
    format_dependency_marker,
    parse_dependency_marker,
)
from backend.infrastructure.github_client import GitHubCliClient

PROJECT_ROOT = Path(__file__).resolve().parents[4]
REVIEW_ROOT = Path(tempfile.mkdtemp(prefix='kc-rv1-state-'))
SANDBOX_ROOT = Path(os.environ['KC_RV_SANDBOX_REPO']).resolve()
EVIDENCE_ROOT = Path(__file__).resolve().parents[1]
RECORD_PATH = EVIDENCE_ROOT / 'rv-1-live-github-current.txt'
KC_PATH = Path(sys.executable).with_name('kc')
(REVIEW_ROOT / '318-sandbox-config.toml').write_text('[agent_runner.generated_content]\nenabled = false\n', encoding='utf-8')
RECORD_PATH.write_text('', encoding='utf-8')
token_result = subprocess.run(['gh', 'auth', 'token'], check=True, capture_output=True, text=True, encoding='utf-8')
scenario_environment = {
    **os.environ,
    'GH_TOKEN': token_result.stdout.strip(),
    'PYTHONPATH': str(PROJECT_ROOT / 'src'),
    'KC_RV_SANDBOX_REPO': str(SANDBOX_ROOT),
    'KEDACODE_CONFIG': str(REVIEW_ROOT / '318-sandbox-config.toml'),
    'AGENT_RUNNER_CONSOLE': json.dumps({'history_db_path': str(REVIEW_ROOT / '318-live-console.db')}),
}
created_issue_urls = []


def run_command(command: list[str], environment: dict[str, str], expected_exit_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[str]:
    """执行真实命令并保留结果，环境凭据不进入日志。"""
    result = subprocess.run(command, cwd=SANDBOX_ROOT, env=environment, check=False, capture_output=True, text=True, encoding='utf-8')
    with RECORD_PATH.open('a', encoding='utf-8') as record_file:
        record_file.write(f'$ {command!r}\nexit_code={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}\n')
    assert result.returncode in expected_exit_codes, result.stdout + result.stderr
    return result


def create_issue(title: str, environment: dict[str, str], dependency: int | None = None) -> str:
    """通过生产 CLI 创建非 ready 的测试 Issue。"""
    command = [str(KC_PATH), 'issue', 'create', '--from-prompt', title, '--no-ready', '--repo', str(SANDBOX_ROOT), '--output', 'json']
    if dependency is not None:
        command.extend(['--depends-on', str(dependency)])
    result = run_command(command, environment)
    issue_url = json.loads(result.stdout)['created'][0]['issue_url']
    created_issue_urls.append(issue_url)
    return issue_url


try:
    run_command([str(KC_PATH), 'labels', 'sync', '--repo', str(SANDBOX_ROOT)], scenario_environment)
    unique_suffix = uuid.uuid4().hex[:10]
    upstream_url = create_issue(f'RV-1 dependency fixture {unique_suffix}', scenario_environment)
    upstream_number = int(upstream_url.rsplit('/', 1)[1])
    baseline_environment = {**scenario_environment, 'PYTHONPATH': os.environ['KC_RV_BASELINE_SOURCE']}
    old_issue_url = create_issue(f'RV-1 pre-refactor negative control {unique_suffix}', baseline_environment, upstream_number)
    old_readback = json.loads(run_command(['gh', 'issue', 'view', old_issue_url, '--json', 'body,labels,state'], scenario_environment).stdout)
    assert 'iar:depends-on' in old_readback['body']
    assert format_dependency_marker(issue_numbers=(upstream_number,)) not in old_readback['body']

    current_issue_url = create_issue(f'RV-1 kc marker acceptance {unique_suffix}', scenario_environment, upstream_number)
    readback = json.loads(run_command(['gh', 'issue', 'view', current_issue_url, '--json', 'body,labels,state'], scenario_environment).stdout)
    expected_marker = format_dependency_marker(issue_numbers=(upstream_number,))
    assert expected_marker in readback['body'] and 'iar:' not in readback['body']
    assert all(label['name'] != 'agent/ready' for label in readback['labels'])
    declaration = parse_dependency_marker(readback['body'])
    assert declaration is not None and declaration.issue_numbers == (upstream_number,)
    open_verdict = evaluate_dependencies(declaration, GitHubCliClient(SANDBOX_ROOT), LabelConfig())
    assert not open_verdict.satisfied and open_verdict.blockers[0].target == str(upstream_number)
    run_command([str(KC_PATH), 'run', '--issue', current_issue_url.rsplit('/', 1)[1], '--dry-run', '--repo', str(SANDBOX_ROOT), '--output', 'json'], scenario_environment, (0, 10))
    run_command(['gh', 'issue', 'close', upstream_url, '--reason', 'completed'], scenario_environment)
    closed_verdict = evaluate_dependencies(declaration, GitHubCliClient(SANDBOX_ROOT), LabelConfig())
    assert closed_verdict.satisfied
    with RECORD_PATH.open('a', encoding='utf-8') as record_file:
        record_file.write(f'PASS: old-tree negative control emits old marker; current CLI writes {expected_marker}.\nPASS: fresh gh readback retained new marker, no old marker, and no ready label.\nPASS: fresh production GitHub client blocks open dependency and accepts it after close.\nCreated issue URLs: {created_issue_urls!r}\n')
finally:
    for issue_url in created_issue_urls:
        subprocess.run(['gh', 'issue', 'close', issue_url, '--reason', 'completed'], env=scenario_environment, check=False, capture_output=True, text=True, encoding='utf-8')

print(f'PASS: RV-1 live GitHub creation/readback and dependency gate; evidence={RECORD_PATH}')
