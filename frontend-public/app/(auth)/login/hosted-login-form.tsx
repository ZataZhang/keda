"use client"

import { useRouter, useSearchParams } from "next/navigation"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { Loader2, LogIn } from "lucide-react"
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
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { PasswordInput } from "@/components/ui/password-input"
import { hostedLogin } from "@/lib/api/hostedConsole"

const loginSchema = z.object({
  password: z.string().min(1, "请输入管理员口令"),
})

/** 托管 Console 登录表单：首期单管理员，只需口令，无用户名/注册入口。 */
export function HostedLoginForm() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [isLoading, setIsLoading] = useState(false)

  const form = useForm<z.infer<typeof loginSchema>>({
    resolver: zodResolver(loginSchema),
    defaultValues: { password: "" },
  })

  /** 提交口令登录；成功后回到原本要访问的页面。 */
  async function onSubmit(data: z.infer<typeof loginSchema>) {
    setIsLoading(true)
    try {
      await hostedLogin(data.password)
      toast.success("登录成功")
      const redirect = searchParams.get("redirect")
      router.replace(redirect || "/app/dashboard")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "登录失败")
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <Card className="border shadow-lg">
      <CardHeader className="text-center">
        <CardTitle className="text-2xl">托管 Console 登录</CardTitle>
        <CardDescription>请输入管理员口令登录本部署的管理终端</CardDescription>
      </CardHeader>
      <CardContent>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="grid gap-4">
            <FormField
              control={form.control}
              name="password"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>管理员口令</FormLabel>
                  <FormControl>
                    <PasswordInput
                      data-testid="hosted-login-password-input"
                      placeholder="********"
                      autoComplete="current-password"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button data-testid="hosted-login-submit-button" type="submit" disabled={isLoading}>
              {isLoading ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : (
                <LogIn className="mr-2 size-4" />
              )}
              登录
            </Button>
          </form>
        </Form>
      </CardContent>
    </Card>
  )
}
