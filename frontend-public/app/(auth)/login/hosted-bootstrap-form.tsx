"use client"

import { useRouter } from "next/navigation"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { KeyRound, Loader2 } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { PasswordInput } from "@/components/ui/password-input"
import { bootstrapHostedAdmin } from "@/lib/api/hostedConsole"

// 管理员口令下限与后端 `MIN_ADMIN_PASSWORD_LENGTH` 保持一致（12）。
const bootstrapSchema = z.object({
  bootstrap_code: z.string().min(1, "请输入 VM 本地签发的一次性引导码"),
  admin_password: z
    .string()
    .min(12, "管理员口令至少 12 个字符"),
})

/** 托管 Console 首次引导表单：用一次性引导码设置首个管理员口令。 */
export function HostedBootstrapForm() {
  const router = useRouter()
  const [isLoading, setIsLoading] = useState(false)

  const form = useForm<z.infer<typeof bootstrapSchema>>({
    resolver: zodResolver(bootstrapSchema),
    defaultValues: { bootstrap_code: "", admin_password: "" },
  })

  /** 提交引导码与口令；成功后进入受保护应用区。 */
  async function onSubmit(data: z.infer<typeof bootstrapSchema>) {
    setIsLoading(true)
    try {
      await bootstrapHostedAdmin(data)
      toast.success("引导完成，请登录并开始配置运行凭据")
      router.replace("/app/settings")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "引导失败")
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <Card className="border shadow-lg">
      <CardHeader className="text-center">
        <CardTitle className="text-2xl">托管 Console 首次引导</CardTitle>
        <CardDescription>
          粘贴在部署 VM 本地执行
          <code className="mx-1 rounded bg-muted px-1 py-0.5 text-xs">
            kc console bootstrap-token create --expires-in 24h
          </code>
          得到的一次性引导码，设置首个管理员口令。引导码只显示一次、24 小时过期、消费后失效。
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="grid gap-4">
            <FormField
              control={form.control}
              name="bootstrap_code"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>一次性引导码</FormLabel>
                  <FormControl>
                    <Input
                      data-testid="bootstrap-code-input"
                      placeholder="由 VM 本地 CLI 签发，只出现一次"
                      autoComplete="off"
                      spellCheck={false}
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    服务端只保存其哈希；换 VM 后引导码与密文都无法复用。
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="admin_password"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>管理员口令</FormLabel>
                  <FormControl>
                    <PasswordInput
                      data-testid="bootstrap-password-input"
                      placeholder="至少 12 个字符"
                      autoComplete="new-password"
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    该口令只用于登录 Console，永不下发给 runner。
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button data-testid="bootstrap-submit-button" type="submit" disabled={isLoading}>
              {isLoading ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : (
                <KeyRound className="mr-2 size-4" />
              )}
              完成引导
            </Button>
          </form>
        </Form>
      </CardContent>
    </Card>
  )
}
