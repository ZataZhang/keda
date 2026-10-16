"use client";

// 托管 Console 运行凭据面板（Issue #265，FR-5）。只展示状态与指引，绝不回显 secret：
// token / api_key 仅出现在写入请求体，保存后本地输入框立即清空，浏览器不留副本。

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Loader2, Plus, ShieldAlert, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PasswordInput } from "@/components/ui/password-input";
import {
  deleteCredential,
  getCredentialGuidance,
  listCredentials,
  saveAgentCredential,
  saveGithubCredential,
  startHostedRunner,
  stopHostedRunner,
  testAgentCredential,
  type AgentGuidance,
  type CredentialGuidance,
  type HostedCredentialStatusView,
} from "@/lib/api/hostedConsole";

/** provider 选择器的取值：GitHub PAT 或三种 runner 预装 Agent key。 */
type ProviderChoice = "github" | "claude" | "codex" | "kimi";

const PROVIDER_OPTIONS: { value: ProviderChoice; label: string }[] = [
  { value: "github", label: "GitHub fine-grained PAT" },
  { value: "claude", label: "Claude Code（Anthropic）" },
  { value: "codex", label: "Codex CLI（OpenAI）" },
  { value: "kimi", label: "Kimi Code CLI（Kimi）" },
];

/** 状态到徽章样式与中文标签的映射。 */
const STATE_BADGE: Record<
  HostedCredentialStatusView["state"],
  { label: string; variant: "ready" | "warning" | "error" }
> = {
  active: { label: "已验证 · runner 可启动", variant: "ready" },
  unverified: { label: "未验证", variant: "warning" },
  rejected: { label: "验证失败", variant: "error" },
};

const OWNER_REPO_PATTERN = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;

/**
 * 托管运行凭据面板：展示来源/权限/计费指引、新增/轮换/测试/删除凭据。
 *
 * @param props.repoIdChoices - 可选的已登记仓库 id，供表单下拉补全（可留空手动输入）。
 */
export function HostedCredentialsPanel({
  repoIdChoices = [],
}: {
  repoIdChoices?: string[];
}) {
  const [guidance, setGuidance] = useState<CredentialGuidance | null>(null);
  const [credentials, setCredentials] = useState<HostedCredentialStatusView[]>([]);
  const [loading, setLoading] = useState(true);

  const [provider, setProvider] = useState<ProviderChoice>("github");
  const [repoId, setRepoId] = useState("");
  const [ownerRepo, setOwnerRepo] = useState("");
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [transitioningRepoId, setTransitioningRepoId] = useState<string | null>(null);

  const [testDialogOpen, setTestDialogOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<HostedCredentialStatusView | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [guidanceResult, credentialResult] = await Promise.all([
        getCredentialGuidance(),
        listCredentials(),
      ]);
      setGuidance(guidanceResult);
      setCredentials(credentialResult);
    } catch (error: unknown) {
      toast.error(error instanceof Error ? error.message : "加载托管凭据信息失败。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    getCredentialGuidance()
      .then(setGuidance)
      .catch((error: unknown) =>
        toast.error(error instanceof Error ? error.message : "加载指引失败。"));
    listCredentials()
      .then((credentialResult) => {
        setCredentials(credentialResult);
        setLoading(false);
      })
      .catch((error: unknown) => {
        setLoading(false);
        toast.error(error instanceof Error ? error.message : "加载凭据列表失败。");
      });
  }, []);

  /** 校验表单必填项，返回错误文案或 null。 */
  function validateForm(): string | null {
    if (!repoId.trim()) return "请填写仓库 id（repo_id）。";
    if (!OWNER_REPO_PATTERN.test(ownerRepo.trim()))
      return "owner_repo 需形如 owner/repo。";
    if (!secret) return "请填写凭据值。";
    return null;
  }

  /** 保存凭据：GitHub 即时只读校验，Agent 仅格式校验、不调模型。 */
  async function handleSave() {
    const error = validateForm();
    if (error) {
      toast.warning(error);
      return;
    }
    setSaving(true);
    try {
      if (provider === "github") {
        const result = await saveGithubCredential({
          repo_id: repoId.trim(),
          owner_repo: ownerRepo.trim(),
          token: secret,
        });
        if (result.status.state === "active") {
          toast.success("GitHub PAT 已通过只读校验，runner 可启用。");
        } else if (result.status.last_rejection === "runner_unavailable") {
          toast.warning("runner 未能安全停止，凭据未替换；请检查 VM 上的 runner 服务。");
        } else {
          toast.warning(
            result.kept_previous
              ? "校验失败，已保留旧凭据；runner 不会启用。"
              : "已保存，但只读校验未通过。",
          );
        }
      } else {
        const result = await saveAgentCredential({
          repo_id: repoId.trim(),
          owner_repo: ownerRepo.trim(),
          provider,
          api_key: secret,
        });
          if (result.kept_previous) {
            toast.warning("当前已验证 key 保持不变；新 key 仍在输入框中，请点“测试连接”验证后替换。");
          } else {
            toast.success("Agent key 已加密保存（未验证）；点“测试连接”经费用确认后启用。");
          }
      }
      if (provider === "github") setSecret("");
      await refresh();
    } catch (saveError: unknown) {
      toast.error(saveError instanceof Error ? saveError.message : "保存凭据失败。");
    } finally {
      setSaving(false);
    }
  }

  /** 打开费用确认对话框（仅 Agent）。测试会发起一次最小真实模型请求。 */
  function openTestDialog() {
    const error = validateForm();
    if (error) {
      toast.warning(error);
      return;
    }
    setTestDialogOpen(true);
  }

  /** 在显式确认费用后发起最小真实模型测试；成功即把凭据置为 active。 */
  async function confirmTest() {
    setSaving(true);
    try {
      const result = await testAgentCredential({
        repo_id: repoId.trim(),
        owner_repo: ownerRepo.trim(),
        provider,
        api_key: secret,
        cost_confirmed: true,
      });
      if (result.status.state === "active") {
        toast.success("模型连通性测试通过，凭据已启用。");
        setTestDialogOpen(false);
        setSecret("");
        await refresh();
      } else {
        toast.warning(
          result.status.last_rejection === "runner_unavailable"
            ? "runner 未能安全停止，凭据未替换；请检查 VM 上的 runner 服务。"
            : result.kept_previous
            ? "测试未通过，已保留旧凭据。"
            : "测试未通过，凭据保持未验证。",
        );
      }
    } catch (error: unknown) {
      toast.error(error instanceof Error ? error.message : "模型测试失败。");
    } finally {
      setSaving(false);
    }
  }

  /** 删除 KedaCode 本地副本（停 runner→清密文→清 tmpfs），并提示上游 key 仍有效。 */
  async function confirmDelete() {
    if (!deleteTarget) return;
    setSaving(true);
    try {
      const result = await deleteCredential(deleteTarget.repo_id, deleteTarget.kind);
      if (result.removed) {
        toast.success(
          `已删除 ${deleteTarget.repo_id} 的 ${deleteTarget.kind} 本地副本。` +
            (result.runner_stopped ? "" : "（对应 runner 停止失败，请检查。）"),
        );
        result.notes.forEach((note) => toast.info(note));
      } else {
        toast.warning("未找到该本地凭据副本。");
      }
      setDeleteTarget(null);
      await refresh();
    } catch (error: unknown) {
      toast.error(error instanceof Error ? error.message : "删除凭据失败。");
    } finally {
      setSaving(false);
    }
  }

  /** 通过受认证 API 启动当前 VM 唯一配置的仓库 runner。 */
  async function handleStartRunner(targetRepoId: string) {
    setTransitioningRepoId(targetRepoId);
    try {
      await startHostedRunner(targetRepoId);
      toast.success("单仓库 runner 已在客户 VM 启动。");
    } catch (error: unknown) {
      toast.error(error instanceof Error ? error.message : "启动 runner 失败。");
    } finally {
      setTransitioningRepoId(null);
    }
  }

  /** 停止当前 VM 配置 runner，并清除 tmpfs 明文认证目录。 */
  async function handleStopRunner(targetRepoId: string) {
    setTransitioningRepoId(targetRepoId);
    try {
      await stopHostedRunner(targetRepoId);
      toast.success("runner 已停止，tmpfs 明文凭据已清除；加密保存的凭据仍保留。");
    } catch (error: unknown) {
      toast.error(error instanceof Error ? error.message : "停止 runner 失败。");
    } finally {
      setTransitioningRepoId(null);
    }
  }

  const selectedProviderGuidance: AgentGuidance | undefined = guidance?.agents.find(
    (agent) => agent.provider === provider,
  );

  return (
    <div className="space-y-6" data-testid="hosted-credentials-panel">
      {loading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          加载托管凭据信息…
        </div>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>凭据来源与权限</CardTitle>
          <CardDescription>
            KedaCode 只接收客户自行创建的单仓库 fine-grained PAT 与三个 runner 预装
            CLI 的 provider API key；不接收 OAuth/session/认证文件，也不收集 Actions
            secrets、项目 DB 凭据、SSH 或代理 TLS 私钥。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {guidance ? (
            <>
              <div className="rounded-lg border p-4">
                <div className="mb-1 font-medium">{guidance.github.token_kind} 指引</div>
                <p className="text-sm text-muted-foreground">{guidance.github.repository_scope}</p>
                <ul className="mt-2 list-inside list-disc text-sm">
                  {guidance.github.required_permissions.map((permission) => (
                    <li key={permission}>{permission}</li>
                  ))}
                </ul>
                <p className="mt-2 text-xs text-muted-foreground">
                  {guidance.github.billing_prerequisite}
                </p>
                <div className="mt-2 flex gap-3 text-sm">
                  <a className="underline" href={guidance.github.create_url} target="_blank" rel="noreferrer">
                    创建 PAT
                  </a>
                  <a className="underline" href={guidance.github.revoke_url} target="_blank" rel="noreferrer">
                    撤销入口
                  </a>
                  <a className="underline" href={guidance.github.docs_url} target="_blank" rel="noreferrer">
                    文档
                  </a>
                </div>
              </div>

              <div className="grid gap-3">
                {guidance.agents.map((agent) => (
                  <div key={agent.provider} className="rounded-lg border p-4">
                    <div className="mb-1 font-medium">{agent.display_name}</div>
                    <p className="text-sm text-muted-foreground">{agent.billing_prerequisite}</p>
                    <p className="mt-1 flex items-center gap-1 text-sm text-amber-700 dark:text-amber-400">
                      <ShieldAlert className="size-4" />
                      {agent.test_cost_warning}
                    </p>
                    <div className="mt-2 flex gap-3 text-sm">
                      <a className="underline" href={agent.create_url} target="_blank" rel="noreferrer">
                        创建 key
                      </a>
                      <a className="underline" href={agent.revoke_url} target="_blank" rel="noreferrer">
                        撤销入口
                      </a>
                      <a className="underline" href={agent.docs_url} target="_blank" rel="noreferrer">
                        文档
                      </a>
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>新增 / 轮换运行凭据</CardTitle>
          <CardDescription>
            按仓库提交凭据。GitHub PAT 保存即只读校验；Agent key 保存不调模型，需经
            “测试连接”在显式确认费用后发起一次最小真实请求才启用。secret 不回显。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="cred-provider">凭据类型</Label>
              <select
                id="cred-provider"
                data-testid="hosted-credential-provider"
                className="h-9 w-full rounded-md border border-slate-200 bg-white px-2 text-sm dark:border-slate-800 dark:bg-slate-950"
                value={provider}
                onChange={(event) => setProvider(event.target.value as ProviderChoice)}
              >
                {PROVIDER_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="cred-repo-id">仓库 id（repo_id）</Label>
              <Input
                id="cred-repo-id"
                data-testid="hosted-credential-repo-id"
                list="hosted-repo-id-options"
                value={repoId}
                placeholder="与 runner 登记表一致的仓库 id"
                onChange={(event) => setRepoId(event.target.value)}
              />
              <datalist id="hosted-repo-id-options">
                {repoIdChoices.map((choice) => (
                  <option key={choice} value={choice} />
                ))}
              </datalist>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="cred-owner-repo">owner/repo</Label>
              <Input
                id="cred-owner-repo"
                data-testid="hosted-credential-owner-repo"
                value={ownerRepo}
                placeholder="octocat/hello-world"
                onChange={(event) => setOwnerRepo(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="cred-secret">{provider === "github" ? "PAT" : "API key"}</Label>
              <PasswordInput
                id="cred-secret"
                data-testid="hosted-credential-secret"
                value={secret}
                autoComplete="off"
                placeholder="粘贴凭据，保存后立即清空且不回显"
                onChange={(event) => setSecret(event.target.value)}
              />
            </div>
          </div>

          {selectedProviderGuidance ? (
            <p className="text-xs text-muted-foreground">
              {selectedProviderGuidance.billing_prerequisite}
            </p>
          ) : null}

          <div className="flex flex-wrap gap-3">
            <Button
              data-testid="hosted-credential-save"
              onClick={() => void handleSave()}
              disabled={saving}
            >
              <Plus className="mr-2 size-4" />
              保存
            </Button>
            {provider !== "github" ? (
              <Button
                data-testid="hosted-credential-test"
                variant="outline"
                onClick={openTestDialog}
                disabled={saving}
              >
                测试连接（需确认费用）
              </Button>
            ) : null}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>已保存凭据</CardTitle>
          <CardDescription>仅展示状态与长度，不含任何凭据内容。</CardDescription>
        </CardHeader>
        <CardContent>
          {credentials.length === 0 ? (
            <p className="text-sm text-muted-foreground">尚无已保存的凭据。</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-muted-foreground">
                  <tr>
                    <th className="py-2 pr-4">仓库 id</th>
                    <th className="py-2 pr-4">owner/repo</th>
                    <th className="py-2 pr-4">类型</th>
                    <th className="py-2 pr-4">状态</th>
                    <th className="py-2 pr-4">长度</th>
                    <th className="py-2">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {credentials.map((credential) => {
                    const badge = STATE_BADGE[credential.state];
                    const githubCredential = credentials.find(
                      (candidate) =>
                        candidate.repo_id === credential.repo_id &&
                        candidate.kind === "github" &&
                        candidate.state === "active" &&
                        candidate.owner_repo === credential.owner_repo,
                    );
                    const canStartRunner =
                      credential.kind === "agent" &&
                      credential.state === "active" &&
                      githubCredential !== undefined;
                    return (
                      <tr
                        key={`${credential.repo_id}-${credential.kind}`}
                        className="border-t"
                        data-testid="hosted-credential-row"
                      >
                        <td className="py-2 pr-4 font-mono">{credential.repo_id}</td>
                        <td className="py-2 pr-4">{credential.owner_repo}</td>
                        <td className="py-2 pr-4">{credential.provider}</td>
                        <td className="py-2 pr-4">
                          <Badge variant={badge.variant}>{badge.label}</Badge>
                        </td>
                        <td className="py-2 pr-4">{credential.secret_length} 位</td>
                        <td className="py-2">
                          {canStartRunner ? (
                            <Button
                              size="sm"
                              className="mr-2"
                              data-testid="hosted-runner-start"
                              disabled={saving || transitioningRepoId === credential.repo_id}
                              onClick={() => void handleStartRunner(credential.repo_id)}
                            >
                              {transitioningRepoId === credential.repo_id ? "处理中…" : "启动 runner"}
                            </Button>
                          ) : null}
                          {canStartRunner ? (
                            <Button
                              size="sm"
                              className="mr-2"
                              data-testid="hosted-runner-stop"
                              disabled={saving || transitioningRepoId === credential.repo_id}
                              onClick={() => void handleStopRunner(credential.repo_id)}
                            >
                              停止 runner
                            </Button>
                          ) : null}
                          <Button
                            size="sm"
                            variant="destructive"
                            data-testid="hosted-credential-delete"
                            onClick={() => setDeleteTarget(credential)}
                          >
                            <Trash2 className="mr-1 size-4" />
                            删除
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={testDialogOpen} onOpenChange={setTestDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认发起模型费用测试</DialogTitle>
            <DialogDescription>
              测试连接会向 {selectedProviderGuidance?.display_name ?? provider} 发起一次最小真实模型请求，
              可能产生 API 费用。确认后方可继续。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setTestDialogOpen(false)} disabled={saving}>
              取消
            </Button>
            <Button
              data-testid="hosted-credential-test-confirm"
              onClick={() => void confirmTest()}
              disabled={saving}
            >
              {saving ? "测试中…" : "我已了解费用，开始测试"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>删除本地凭据副本</DialogTitle>
            <DialogDescription>
              删除会停止 {deleteTarget?.repo_id} 对应 runner、清除加密副本与该仓库的
              tmpfs 明文目录，但<strong>不会</strong>替你在上游撤销 key。请前往 provider
              官方撤销入口手动 revoke，否则旧 key 在上游仍然有效。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)} disabled={saving}>
              取消
            </Button>
            <Button
              variant="destructive"
              data-testid="hosted-credential-delete-confirm"
              onClick={() => void confirmDelete()}
              disabled={saving}
            >
              {saving ? "删除中…" : "确认删除本地副本"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
