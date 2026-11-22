"""兼容原开发脚本的导入路径。"""

import sys

from backend.core.use_cases.model_benchmark import workflow

sys.modules[__name__] = workflow
