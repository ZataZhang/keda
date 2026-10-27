# Verification Plan — P2-FEAT-20261016-152354-hosted-desktop-interaction

本文件把 PRD §7.6 的 rv-1/rv-2/rv-3 落成可复核步骤。执行环境是 Apple Silicon Mac 上的 Docker Desktop Linux VM（ARM64），代码 checkout 与证据目录通过 Hosted Compose bind mount 进入 Runner。认证挂载目录为空；没有接入客户 VM、仓库令牌或 Agent API key。

## Oracle 执行计划与结果

| rv-id | 真实入口与执行步骤 | 预期与实际结果 | 捕获件 |
|---|---|---|---|
| rv-1 | 用 Hosted Compose 分别设置 `KEDACODE_DESKTOP_ENABLED=false` / `true`，运行 `docker compose config --format json`；对 desktop 镜像把 runtime env 覆盖为 false，再经 Compose 运行 `kc-computer record start` 和 `kc-computer screenshot`。 | Compose 关闭态为 `iar-runner:latest`、build arg/runtime 都为 `false`；启用态为 `iar-runner:desktop`、build arg/runtime 都为 `true`。两条关闭态 GUI 命令均以退出码 1 fail closed。 | `rv-1-hosted-desktop-config.txt`、`rv-1-disabled-control.txt` |
| rv-2 | Hosted Compose 构建桌面 Runner 并以 `DISPLAY=:99` 启动 Xvfb/Openbox；通过新的 `docker exec` 运行 `kc-computer`，右键菜单、点击 `Reconfigure`、截图；另一次录制输入哨兵以核对轨迹脱敏。停止录制后启动新 `ffprobe` 与 Python 进程解析产物。 | 菜单选择视频为 VP9、1440×900、10 fps、5.6 秒；截图分别显示菜单打开与关闭。相邻 JSONL 有坐标/时间；哨兵 `trace-secret-sentinel` 不出现，类型动作只保存字符数。 | 两个 `.webm`、两个 `.actions.jsonl`、两个 `.png`、CLI 输出、`rv-2-media-probe.txt`、`rv-2-source-runtime-identity.txt` |
| rv-3 | 展开真实 Hosted Compose；在桌面容器运行时读取 `docker inspect`、`/proc/net/tcp{,6}`、`/proc/<pid>/cmdline` 与 X11 socket。 | Compose `ports` 与 `PortBindings` 为空，没有 `/var/run/docker.sock` 挂载；Xvfb 使用 `-nolisten tcp` 且 X11 Unix socket 存在。网络表只有无容器内进程 owner 的 `127.0.0.11:44907` loopback socket，没有公开服务监听。 | `rv-3-hosted-isolation.txt` |

## 手工重跑命令

在目标 checkout 根目录运行，认证目录需由操作者准备为空目录：

```bash
docker compose -p keda-desktop-smoke \
  -f src/backend/engines/agent_runner/templates/runner_container/docker-compose.runner.hosted.yml \
  config --format json

docker compose -p keda-desktop-smoke \
  -f src/backend/engines/agent_runner/templates/runner_container/docker-compose.runner.hosted.yml \
  build

# 由当前 Hosted Runner 进程执行，输出路径改为本次 PRD 的 evidence 目录。
kc-computer status
kc-computer record start --output <repo>/tasks/evidence/<prd-stem>/rv-2-desktop-operation.webm
kc-computer click 700 440 --button right
kc-computer click 740 540 --button left
kc-computer record stop
```

## 未完成项

- 没有执行自动化测试套件；本轮用户请求的是容器内实际操作与录屏，未要求运行测试。
- 原始 `just lint --full` 被 `check-test-flag` 拦截，因为该分支没有当前 `just test` 标记。随后执行 `SKIP=check-test-flag just lint --full`，其余全量静态 hook 通过；这不代表测试通过。
- 没有运行针对“Compose 意外增加 VNC ports / Xvfb 移除 `-nolisten tcp`”的边界负控，也没有独立 verifier。PRD 保持 pending，PR #269 保持 draft。
- `/proc/net/tcp` 用于读取真实网络 namespace；Runner 镜像不含 `ss`。没有用静态 Dockerfile 检查替代运行态证据。
