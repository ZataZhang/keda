import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

/** Merge Tailwind classes with conditional class names. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// 将 ISO 8601 时间字符串按本地时区拆成补零后的时间片段；无法解析时返回 null。
function localTimeParts(isoString: string | null | undefined) {
  if (!isoString) {
    return null
  }
  const parsedDate = new Date(isoString)
  if (Number.isNaN(parsedDate.getTime())) {
    return null
  }
  const pad = (value: number) => String(value).padStart(2, "0")
  return {
    year: String(parsedDate.getFullYear()),
    month: pad(parsedDate.getMonth() + 1),
    day: pad(parsedDate.getDate()),
    hour: pad(parsedDate.getHours()),
    minute: pad(parsedDate.getMinutes()),
    second: pad(parsedDate.getSeconds()),
  }
}

/**
 * 将 ISO 8601 UTC 时间字符串格式化为本地系统时间（yyyy-MM-dd HH:mm:ss）。
 * @param isoString - 待格式化的 ISO 时间字符串，可为空。
 * @returns 本地日期时间；缺省返回 `—`，无法解析时原样返回入参。
 */
export function formatLocalDateTime(isoString: string | null | undefined): string {
  const parts = localTimeParts(isoString)
  if (parts) {
    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`
  }
  return isoString ? isoString : "—"
}

/**
 * 将 ISO 8601 时间字符串格式化为本地时分秒（HH:mm:ss）。
 * 用于「数据截至」这类只需当天时刻的新鲜度提示，避免整串日期挤占表头。
 * @param isoString - 待格式化的 ISO 时间字符串，可为空。
 * @returns 本地时分秒；缺省或无法解析时返回 `—`。
 */
export function formatLocalClockTime(isoString: string | null | undefined): string {
  const parts = localTimeParts(isoString)
  return parts ? `${parts.hour}:${parts.minute}:${parts.second}` : "—"
}
