import { apiGet, apiPost, del } from "./client"
import type { UserSession } from "./types"

// 与后端 `src/backend/api/routes/hosted_auth.py`、`hosted_credentials.py` 及
// `core/shared/security/hosted_secrets.py` 的响应结构对齐；这些视图**永不含
// secret 值**（token/api_key 只在写入请求体里出现，绝不回显）。

/** 托管模式探测结果：`enabled` 区分托管/本机，`bootstrapped` 决定是否还需引导。 */
export type HostedStatus = {
  enabled: boolean
  bootstrapped: boolean
}

/** 后端凭据状态视图（不含秘密；时间字段为 ISO 文本或 null）。 */
export type HostedCredentialStatusView = {
  repo_id: string
  owner_repo: string
  kind: "github" | "agent"
  provider: string
  state: "unverified" | "active" | "rejected"
  created_at_utc: string | null
  verified_at_utc: string | null
  expires_at_hint: string
  last_rejection: string | null
  secret_length: number
}

/** GitHub fine-grained PAT 的创建/权限/撤销指引。 */
export type GithubGuidance = {
  kind: string
  provider: string
  create_url: string
  revoke_url: string
  docs_url: string
  token_kind: string
  repository_scope: string
  required_permissions: string[]
  billing_prerequisite: string
  accepts_oauth_or_session: boolean
  accepts_auth_file_upload: boolean
}

/** 单个 Agent provider 的来源/计费/撤销/费用提示。 */
export type AgentGuidance = {
  kind: string
  provider: string
  cli_name: string
  display_name: string
  create_url: string
  revoke_url: string
  docs_url: string
  billing_prerequisite: string
  test_cost_warning: string
  accepts_oauth_or_session: boolean
  accepts_custom_base_url: boolean
}

export type CredentialGuidance = {
  github: GithubGuidance
  agents: AgentGuidance[]
}

/** 写入/轮换返回：`kept_previous` 表示校验失败保留了旧值。 */
export type CredentialWriteResult = {
  kept_previous: boolean
  status: HostedCredentialStatusView
}

/** 删除本地副本返回：runner 停止、tmpfs 清除、上游是否已被 KedaCode 撤销（恒 false）。 */
export type CredentialDeleteResult = {
  removed: boolean
  runner_stopped: boolean
  tmpfs_erased: boolean
  upstream_revoked: boolean
  notes: string[]
}

/**
 * 探测当前是否为托管 Console，以及是否已完成首次引导。
 *
 * 本机模式下后端未挂载该路由（返回 404），据此判定 `enabled: false`，让登录页
 * 退回本机 no-op 表单，托管安全边界不被本机路径削弱。
 */
export async function getHostedStatus(): Promise<HostedStatus> {
  try {
    const data = await apiGet<HostedStatus>("/hosted/status")
    return { enabled: data.enabled !== false, bootstrapped: Boolean(data.bootstrapped) }
  } catch {
    return { enabled: false, bootstrapped: false }
  }
}

/** 用一次性引导码设置首个管理员并下发会话 cookie（HttpOnly，前端读不到）。 */
export async function bootstrapHostedAdmin(payload: {
  bootstrap_code: string
  admin_password: string
}): Promise<void> {
  await apiPost<{ user_id: string; authenticated: boolean }>("/hosted/bootstrap", payload)
}

/** 托管管理员口令登录（只需口令，无用户名字段）。 */
export async function hostedLogin(password: string): Promise<UserSession> {
  return apiPost<UserSession>("/auth/login", { password })
}

/** 读取 PAT 与三种 Agent provider 的来源/权限/计费/撤销指引。 */
export async function getCredentialGuidance(): Promise<CredentialGuidance> {
  return apiGet<CredentialGuidance>("/hosted/credentials/guidance")
}

/** 列出全部凭据状态（不含任何 secret）。 */
export async function listCredentials(): Promise<HostedCredentialStatusView[]> {
  const data = await apiGet<{ credentials: HostedCredentialStatusView[] }>(
    "/hosted/credentials"
  )
  return data.credentials
}

/** 保存并只读校验 GitHub fine-grained PAT；失败保留旧值且不启用 runner。 */
export async function saveGithubCredential(payload: {
  repo_id: string
  owner_repo: string
  token: string
}): Promise<CredentialWriteResult> {
  return apiPost<CredentialWriteResult>("/hosted/credentials/github", payload)
}

/** 保存 Agent key（仅格式/provider 校验，不调模型）；状态为未验证。 */
export async function saveAgentCredential(payload: {
  repo_id: string
  owner_repo: string
  provider: string
  api_key: string
}): Promise<CredentialWriteResult> {
  return apiPost<CredentialWriteResult>("/hosted/credentials/agent", payload)
}

/** 仅在 `costConfirmed` 为真时发起一次最小真实模型请求（可能产生 API 费用）。 */
export async function testAgentCredential(payload: {
  repo_id: string
  owner_repo: string
  provider: string
  api_key: string
  cost_confirmed: boolean
}): Promise<CredentialWriteResult> {
  return apiPost<CredentialWriteResult>("/hosted/credentials/agent/test", payload)
}

/** 从受保护 Console 启动 VM 配置的单仓库 runner。 */
export async function startHostedRunner(repoId: string): Promise<{ repo_id: string; started: true }> {
  return apiPost<{ repo_id: string; started: true }>(
    `/hosted/credentials/runner/${encodeURIComponent(repoId)}/start`,
    {},
  )
}

/** 停止 VM 配置的 runner，并清除该仓库 tmpfs 明文认证目录。 */
export async function stopHostedRunner(repoId: string): Promise<{ repo_id: string; stopped: true }> {
  return apiPost<{ repo_id: string; stopped: true }>(
    `/hosted/credentials/runner/${encodeURIComponent(repoId)}/stop`,
    {},
  )
}

/** 删除 KedaCode 本地副本（停 runner→清密文→清 tmpfs）；提示上游 key 仍有效。 */
export async function deleteCredential(
  repoId: string,
  kind: "github" | "agent"
): Promise<CredentialDeleteResult> {
  return del<CredentialDeleteResult>(`/hosted/credentials/${repoId}/${kind}`)
}
