// 管理终端「有新版本」检查：当前版本来自后端，最新版本来自 PyPI。
//
// 当前版本必须取后端运行时值（`GET /agent-runner/console/version`）：构建期注入
// 会与实际安装的包漂移。最新版本直接由浏览器查 PyPI（其 JSON API 允许跨域），
// 因此后端不必引入对外网络调用；离线 / 超时 / 被墙一律静默降级。

import { get } from "./client";

/** PyPI 上的发行版名，与后端 `--version` 查询用同一个名字。 */
const PYPI_PACKAGE_NAME = "kedacode";

/** PyPI JSON API：`info.version` 即最新版本。 */
const PYPI_JSON_URL = `https://pypi.org/pypi/${PYPI_PACKAGE_NAME}/json`;

/**
 * 取后端当前运行的 KedaCode 版本。
 *
 * @returns 后端上报的版本字符串。
 */
export async function fetchConsoleVersion(): Promise<string> {
  const response = await get<{ version: string }>("/v1/agent-runner/console/version");
  return response.version;
}

/**
 * 查 PyPI 上 kedacode 的最新版本。
 *
 * 只读 `info.version`；网络失败、HTTP 非 2xx、超时（由调用方通过
 * ``AbortSignal`` 控制）或响应结构异常都返回 ``null``，由调用方静默处理。
 *
 * @param signal - 可选：用于超时 / 卸载时中止请求。
 * @returns 最新版本号；取不到时返回 null。
 */
export async function fetchLatestPyPiVersion(signal?: AbortSignal): Promise<string | null> {
  const response = await fetch(PYPI_JSON_URL, { signal });
  if (!response.ok) {
    return null;
  }
  const payload = (await response.json()) as { info?: { version?: string } };
  return payload.info?.version ?? null;
}

/**
 * 把版本号解析成用于比较的数字段。
 *
 * 只取主版本段的数字前缀，忽略预发布 / 本地版本后缀（``0.2.1-rc1``、
 * ``0.2.1+local`` 都按 ``0.2.1`` 处理）。任一主版本段不是纯数字时返回 ``null``，
 * 表示这个版本号不可比较。
 *
 * @param version - 版本字符串。
 * @returns 数字段数组；不可解析时返回 null。
 */
function parseVersionNumbers(version: string): number[] | null {
  const coreVersion = version.split(/[-+]/, 1)[0];
  const segments = coreVersion.split(".");
  const numbers: number[] = [];
  for (const segment of segments) {
    if (!/^\d+$/.test(segment)) {
      return null;
    }
    numbers.push(Number(segment));
  }
  return numbers.length > 0 ? numbers : null;
}

/**
 * 判断 ``latest`` 是否比 ``current`` 更新。
 *
 * 逐段数值比较，缺位补 0（``0.2`` 与 ``0.2.0`` 视为相同）。任一侧不可解析、
 * 或 ``latest`` 不更新时返回 ``false``。
 *
 * @param current - 当前版本。
 * @param latest - 候选的新版本。
 * @returns latest 严格大于 current 时为 true。
 */
export function isNewerVersion(current: string, latest: string): boolean {
  const currentNumbers = parseVersionNumbers(current);
  const latestNumbers = parseVersionNumbers(latest);
  if (!currentNumbers || !latestNumbers) {
    return false;
  }
  const length = Math.max(currentNumbers.length, latestNumbers.length);
  for (let index = 0; index < length; index += 1) {
    const currentValue = currentNumbers[index] ?? 0;
    const latestValue = latestNumbers[index] ?? 0;
    if (currentValue !== latestValue) {
      return latestValue > currentValue;
    }
  }
  return false;
}
