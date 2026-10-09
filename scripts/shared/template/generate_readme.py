#!/usr/bin/env python3
"""为新项目生成中英文 README 文件。"""

import sys
from pathlib import Path


def generate_readme(project_name: str, output_path: Path) -> None:
    """为新项目生成英文 README 和对应的简体中文版。

    Args:
        project_name: 新项目名称。
        output_path: 英文 README.md 的输出路径；中文文件写入同一目录。
    """
    readme_content_english = f"""# {project_name}

English | [简体中文](README.zh.md)

> Add a brief description of this project.

## Quick Start

```bash
just sync dev
just run
```

`just sync dev` installs development dependencies and pre-commit hooks. `just run` starts the
backend and both frontends.

## Requirements

- Python `>=3.11` and [uv](https://docs.astral.sh/uv/)
- [just](https://github.com/casey/just)
- Node.js 22 and pnpm 11 to run the frontends
- PostgreSQL and Redis, or the services configured for this project

## Configuration

Put non-sensitive defaults in `config.toml` and local environment variables or secrets in
`.env.local`. See [`.env.example`](.env.example) and the
[configuration guide](docs/guides/configuration.md).

## Common Commands

| Command | Purpose |
| --- | --- |
| `just sync dev` | Install development dependencies and pre-commit hooks |
| `just run` | Start the backend and both frontends |
| `just test` | Run the local test suite |
| `just docs-serve` | Preview the documentation site |

## Documentation

- [Getting Started](docs/getting-started.md)
- [Configuration](docs/guides/configuration.md)
- [System Architecture](docs/architecture/system-design.md)
- [Documentation Index](docs/index.md)
"""

    readme_content_chinese = f"""# {project_name}

[English](README.md) | 简体中文

> 请在此处添加项目简介。

## 快速开始

```bash
just sync dev
just run
```

`just sync dev` 会安装开发依赖和 pre-commit hooks；`just run` 会启动后端及两个前端。

## 环境要求

- Python `>=3.11` 和 [uv](https://docs.astral.sh/uv/)
- [just](https://github.com/casey/just)
- 运行前端需要 Node.js 22 和 pnpm 11
- PostgreSQL、Redis，或本项目配置使用的其他服务

## 配置

非敏感默认值写入 `config.toml`；本地环境变量和密钥写入 `.env.local`。参阅
[`.env.example`](.env.example) 和[配置指南](docs/guides/configuration.md)。

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `just sync dev` | 安装开发依赖和 pre-commit hooks |
| `just run` | 启动后端及两个前端 |
| `just test` | 运行本地测试集 |
| `just docs-serve` | 预览文档站点 |

## 文档

- [快速开始](docs/getting-started.md)
- [配置说明](docs/guides/configuration.md)
- [系统架构](docs/architecture/system-design.md)
- [文档目录](docs/index.md)
"""

    localized_output_path: Path = output_path.with_name(
        f"{output_path.stem}.zh{output_path.suffix}"
    )
    output_path.write_text(readme_content_english, encoding="utf-8")
    localized_output_path.write_text(readme_content_chinese, encoding="utf-8")
    print(f"Created bilingual README files for {project_name}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python generate_readme.py <project_name> <output_path>")
        sys.exit(1)

    project_name = sys.argv[1]
    output_path = Path(sys.argv[2])
    generate_readme(project_name, output_path)
