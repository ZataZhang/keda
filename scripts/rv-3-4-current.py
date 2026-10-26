"""用当前 Python 环境的 kc 执行隔离 Git 工作树正负控。"""

from pathlib import Path
import os
import sys
import tempfile

import rv_harness as harness

harness.KC_CLI_PATH = Path(sys.executable).with_name('kc')
harness.SCENE_ROOT = Path(tempfile.mkdtemp(prefix='kc-worktree-rv-'))
os.environ['PYTHONPATH'] = str(harness.PROJECT_ROOT / 'src')
scenario_number = int(sys.argv[1])
assert scenario_number in (3, 4)
harness._run_worktree_oracle(scenario_number, negative=True)
harness._run_worktree_oracle(scenario_number, negative=False)
print(f'PASS: RV-{scenario_number} negative control and real Git/CLI acceptance.')
