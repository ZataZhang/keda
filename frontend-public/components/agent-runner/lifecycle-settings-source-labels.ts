// 生命周期设置页的来源词表与展示 helper（矩阵行、预设卡片、回退行共用）。
//
// 来源值本身由后端聚合视图下发（core 层的字段来源词汇表），这里只做「值 → 中文
// 说明」的翻译与缺省占位，不持有第二份生命周期映射。

/** 配置层来源的中文标签（agent 字段来源取层名时用于可读展示）。 */
export const LAYER_SOURCE_LABELS: Record<string, string> = {
  global: "全局 config.toml",
  repository: "仓库 .kedacode.toml",
  legacy: "既有配置键",
  builtin: "内置默认",
};

/** 预设定义来源层的中文标签。 */
export const PRESET_SOURCE_LABELS: Record<string, string> = {
  global: "全局",
  repository_only: "本仓库独有",
  repository_overrides: "本仓库覆盖全局",
};

/**
 * 把一个逐字段来源值翻译成中文说明：先查后端词表，再查层名标签，最后原样返回。
 *
 * @param value - 字段来源值（预设 / 继承 / 默认 / 不支持，或配置层名）。
 * @param vocabulary - 后端下发的来源标签词表。
 * @returns 可读的中文来源说明。
 */
export function describeFieldSource(
  value: string | null,
  vocabulary: Record<string, string>,
): string {
  if (!value) {
    return "—";
  }
  return vocabulary[value] ?? LAYER_SOURCE_LABELS[value] ?? value;
}

/**
 * 展示某个可选值：有值显示值，否则按来源显示占位说明。
 *
 * @param value - 字段当前值（null 表示未显式设置）。
 * @param source - 该字段的来源标识。
 * @param vocabulary - 后端下发的来源标签词表。
 * @returns 可读的字段展示文本。
 */
export function displayOptionalField(
  value: string | null,
  source: string,
  vocabulary: Record<string, string>,
): string {
  if (value) {
    return value;
  }
  if (source === "not_supported") {
    return "该 Agent 不支持";
  }
  return describeFieldSource(source, vocabulary);
}
