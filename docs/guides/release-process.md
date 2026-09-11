# 发布流程（PyPI / Homebrew）

本文描述 `kedacode` 包（命令名 `iar`）的两步走发布流程：PyPI 上传由 GitHub OIDC Trusted Publishing 完成，Homebrew tap formula 由发布 CI 自动更新。对应 workflow 是 `.github/workflows/release.yml`。

## 总览：为什么是两步走

PyPI 的版本号一旦上传就**永久占用、不能重用**，因此上传动作绝不能挂在 `v*` tag 推送上——tag 一推就发、没有反悔窗口。实际流程把"构建"与"上传"拆开：

```text
1. 推送 v* tag
   └─ job: build：uv build → 产物断言（含 console 静态产物、不含 skills）→ SHA256SUMS
      → 创建 DRAFT GitHub Release
      ↳ 此时 PyPI 上什么都没发生（可反悔窗口）

2. 人工检查 draft release 产物后，把该 release 正式发布（release: published）

3. 正式发布事件触发两个 job：
   ├─ publish-pypi：OIDC(id-token: write) → Trusted Publishing → 上传（skip-existing 幂等）
   └─ update-tap（needs: publish-pypi）：重新生成 formula resources → 更新 tap 仓库
```

即使链路完全失控把错误内容发上去，最坏结果也只是烧掉一个版本号（下个版本号盖过去），前提是错误在 draft 窗口没被发现。所以第 2 步的人工检查不可跳过。

## 一次性配置（Trusted Publishing）

以下登记在 PyPI 账号侧完成一次即可；`kedacode` 项目尚不存在时走 **pending publisher** 路径（账号侧栏 Publishing，不是项目侧栏）：

| 登记项 | 值 |
| --- | --- |
| Platform | GitHub Actions |
| Owner | `ZataZhang`（当前 GitHub 登录名；曾用名无效，OIDC claim 用当前名） |
| Repository name | `keda` |
| Workflow name | `release.yml`（只写文件名，不带 `.github/workflows/` 前缀） |
| Environment name | `pypi` |
| PyPI Project Name | `kedacode` |

注意事项：

- workflow 里的 `environment: pypi` 必须与登记**逐字一致**。GitHub 对 workflow 引用的不存在 environment 会自动创建（无保护规则），拼写错误不会被 GitHub 拦下，最终由 PyPI 校验 environment claim 时报 `invalid-publisher`。
- 改动 `release.yml` 的**文件名**或 `environment:` 后，必须回 PyPI 同步更新登记信息，否则下次发布认证失败。
- pending publisher 不预留包名：正式发布前若 `kedacode` 被他人抢注，登记即失效。
- 仓库内**不存任何长期 PyPI token**；发布凭据完全来自 OIDC 短期交换。Trusted Publishing 下 `gh-action-pypi-publish` 默认生成 PEP 740 attestation（token 发布拿不到）。

Homebrew tap 侧的一次性配置：`ZataZhang/homebrew-tap` 必须是 public 仓库（`brew tap` 走匿名 clone），formula 路径为 `Formula/kedacode.rb`；`update-tap` job 使用的凭据必须是**仅对 tap 仓库 contents 有写权限**的细粒度 PAT，禁止复用具备本仓库写权限的 token。tap 仓库为空（无 `main` 分支）时首次推送由 job 自行建分支，无需手工初始化。

## 日常发布步骤

1. **本地产物预检**（打 tag 之前，必做）：`uv build` 构建前端静态产物后打包，`uvx twine check dist/*` 全部 PASSED（twine 不在 dev 依赖里，用 `uvx` 拉起，`uv run twine` 会报 `Failed to spawn`），再把 `dist/*.whl` 装进一个干净 venv，跑 `iar --version`、`kedacode --version` 与 `iar console --no-browser` 确认可用。
2. 推送 `v*` tag，等 `build` job 全绿，确认 draft release 已带 sdist、wheel 与 `SHA256SUMS`。
3. 检查 draft release：确认产物文件名是 `kedacode-*`、console 产物在包内、版本号正确。
4. 在 GitHub 上把该 draft release 正式发布。
5. 等 `publish-pypi` 与 `update-tap` 全绿后，**不要只看 workflow 日志**：核对 `https://pypi.org/pypi/kedacode/json` 已返回新版本，`iar console` 从公共索引可装，tap 仓库的 `Formula/kedacode.rb` 已更新。

## 误发与幂等

- 同一版本重复触发发布时，`skip-existing: true` 使上传 job 幂等成功而不是失败；已存在的文件不会被覆盖。
- 版本号被烧（内容错误但已上传）后，PyPI 上该版本无法删除，只能发下一个版本号修正；已上传版本可以 yank（从默认解析中摘除）但文件仍占名。
- 包名 `kedacode` 与 `keda-code` 是两个不同的名字（PEP 503 归一化后不相等）：`kedacode` 是正式分发名，安装命令与文档只能出现它；`keda-code` 是 PyPI 上的防守型转发包，源码在 `packaging/keda-code/`，发布方式见下方「转发包 keda-code」。

## 转发包 keda-code

`packaging/keda-code/` 是一个独立的最小包：只有元数据和一条 `dependencies = ["kedacode"]`，没有任何 Python 模块，也没有 console script。它的职责是占住与 `kedacode` 混淆度最高的名字（PEP 503 归一化后 `keda-code` / `keda_code` / `keda.code` 是同一个），避免被第三方注册成投毒包。

它**不进 `release.yml`**：版本号与主包脱钩、依赖不 pin，因此只需一次性手动上传，主包此后发版不必带着它动。

```bash
uv build --project packaging/keda-code --out-dir packaging/keda-code/dist
uvx twine check packaging/keda-code/dist/*
uvx twine upload packaging/keda-code/dist/*
```

上传用一次性 API token 即可，不要给它配 Trusted Publisher——多一套 OIDC 登记就多一处要跟着用户名/仓库名变更维护的配置，而这个包一辈子只发一次。

## 命令入口与 tap formula

wheel 提供两个完全等价的 console script，都指向 `backend.api.cli:main`：主命令 `iar`，以及与安装名对齐的别名 `kedacode`。**刻意不提供 `keda` 入口**——CNCF KEDA 目前只发 YAML manifest、homebrew-core 里也还没有 `keda` formula，但它一旦发 CLI，名字必然是 `keda` 且会进 homebrew-core，届时本项目 formula 的 `bin/keda` 符号链接会与之硬冲突，用户 `brew install` 直接失败。

新增或改名 console script 时，除了 `pyproject.toml` 的 `[project.scripts]`，还要同步这几处：

1. `scripts/install/install.sh` 的 `TOOL_BIN_NAMES`（卸载逐个清理 `~/.local/bin/`，漏掉会留死链接）。
2. `.github/workflows/install-smoke.yml` 的 5 个安装验证步骤（editable / wheel×2 / PyPI×2）。
3. `.github/workflows/release.yml` 里的 formula skeleton。
4. **tap 仓库里已存在的 formula——必须手工改。** skeleton 那段外面套着 `if [ ! -f .../Formula/kedacode.rb ]`，只在 formula 缺失时才 bootstrap；`ZataZhang/homebrew-tap` 里的 formula 早已存在，而 `brew update-python-resources` 只重写 resource 块。也就是说新增的 `bin.install_symlink` 行不会被任何自动化补上，得去 tap 仓库手动加，否则 brew 装出来的用户永远拿不到新命令，而 CI 全绿。

第 4 步有**顺序要求**：formula 的 `url` 指向某个具体版本的 sdist，而 `bin.install_symlink libexec/"bin/<新命令>"` 在目标文件不存在时会让 `brew install` 直接失败。所以必须先发出包含该 console script 的新版本、等 `update-tap` 把 formula 的 url/sha 刷到这个版本之后，再去 tap 加符号链接行——顺序反了会让所有 brew 用户装不上。

## 排错

- `invalid-publisher: valid token, but no corresponding publisher`：OIDC 令牌本身有效，但与 PyPI 登记信息对不上——逐字核对 Owner / Repository / Workflow / Environment 五项，最常见是 environment 名拼写或 Owner 用了旧用户名。
- `publish-pypi` 报 OIDC 令牌拒签：检查 job 是否声明 `permissions: id-token: write` 与 `environment: pypi`。
- `update-tap` 首推失败：确认 tap 仓库为 public、凭据为仅限该仓库 contents 写入的细粒度 PAT，且 job 使用 `git push -u origin main` 建分支（空仓库 clone 后没有可提交分支）。
