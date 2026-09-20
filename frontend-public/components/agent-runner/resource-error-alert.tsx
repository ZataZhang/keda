"use client";

// 「资源加载失败」的统一提示块。
//
// 各 console 编辑器的失败态展示必须一致（同样的 role="alert"、同样的配色与
// data-testid 约定），否则不同的调用点会各自抄一份而慢慢漂移。

interface ResourceErrorAlertProps {
  /** 展示给用户的错误文案。 */
  message: string;
  /** data-testid，供 e2e 定位。 */
  testId: string;
}

/**
 * 加载/保存失败时的统一告警块。
 *
 * @param props - 错误文案与测试 id。
 * @returns 带 role="alert" 的告警块。
 */
export function ResourceErrorAlert({ message, testId }: ResourceErrorAlertProps) {
  return (
    <div
      role="alert"
      className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300"
      data-testid={testId}
    >
      {message}
    </div>
  );
}
