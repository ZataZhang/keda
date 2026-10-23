# Documentation Standards

## Docs Are Part Of The Product

`docs/` 和 `mkdocs.yml` 是代码库的一部分。文档错误按代码错误对待。

## Synchronization Rules

出现以下变化时，必须同步更新文档：

- 业务逻辑变化
- 配置项变化
- 公共函数签名变化
- 新增模块、流程或开发规范

## 第三方行为的断言

文档和注释里描述依赖库行为的句子——子进程会继承哪些环境变量、传空值与省略是否等价、会不会展开 `${VAR}` 或渲染模板——必须对照 lockfile 锁定版本的源码核实，写明依据的模块或符号；拿不准就实测一次。这类断言在 review 里很少被质疑，却最容易随依赖升级悄悄失效，错了还会直接误导安全判断：曾有一条注释写着"传空字典会清空子进程继承的环境"，而当时锁定的 MCP SDK 实际只透传一组默认变量，空字典与省略的结果完全相同。

## Navigation Maintenance

新增长期文档页时：

1. 创建对应的 Markdown 文件
2. 更新 `mkdocs.yml` 的 `nav`
3. 确保页面在文档站点中可发现

## API Documentation

不要在 Markdown 中手工复制 Python 函数说明。优先使用 `mkdocstrings`：

```markdown
::: backend.core.agent.KnowledgeRetrievalAgent
    handler: python
    options:
      members:
        - execute_task
```

## Markdown Encoding

- 所有 Markdown 文件必须使用 UTF-8 编码
- PowerShell 中读取 Markdown 时使用 `-Encoding utf8`

## Standards Hub Maintenance

如果规范变化与 AI 工作方式相关，优先更新 `docs/ai-standards/`，再同步各工具入口文件。
