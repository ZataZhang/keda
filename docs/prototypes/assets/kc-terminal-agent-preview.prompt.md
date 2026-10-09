# KC terminal agent preview image provenance

- 工具：OpenAI 内置 ImageGen（`image_gen.imagegen`）
- 日期：2026-10-09
- 模式：参考图引导的全新生成
- 画布：1586 × 992 像素（约 16:10）
- 参考图：用户在本对话中提供的 CodeBuddy 终端截图（仅参考终端框架与信息层级，不复用品牌、文字或精确布局；原始临时附件：`/var/folders/n8/x_smwvn16b3b30f7wnl4s2kh0000gn/T/codex-clipboard-rVHtUb.png`）
- 保留/约束：体现裸 `kc` 启动 Codex 原生 TUI；只有用户在对话中明确要求时才启动项目开发服务；URL 在终端回复，由用户手动访问；不创建 KC 网页聊天、不在启动时运行前端、不自动打开浏览器。

## 完整生成提示词

~~~text
Use case: terminal-agent-ui-mockup
Asset type: high-fidelity static concept screenshot for a KedaCode PRD.
Primary request: Show the exact desired experience: the user runs `kc`, which launches the configured native terminal agent executor (Codex CLI in this example), and the user talks to that executor in its terminal interface. KC does not create a separate web chat or browser UI. The user asks the agent to open the current repository's frontend; only then does the agent start the configured dev server and print its loopback URL in the terminal. The user manually opens the URL if desired.
Reference use: Use the attached CodeBuddy screenshot only as a broad reference for terminal framing, clear startup context, concise recent activity, and a visible local URL. Do not copy its logo, product name, exact text, or pixel layout. Do not show a browser window or Keda web chat UI.
Canvas/style: wide 16:10 crisp terminal screenshot, dark graphite background, monospaced typography, restrained mint/teal borders and accents, readable Chinese, realistic developer CLI. No device mockup.
Composition: Top terminal title area reads “Codex · KedaCode” with repo “/Users/zata/code/keda”. Show a compact launch status line “kc → Codex CLI（来自仓库设置）” and “KedaCode operator skill 已加载”. Main conversation is the real native terminal-agent interaction, not a KC-designed chat app: user message “帮我打开这个仓库的前端。” Assistant reply “已按仓库预览配置启动 pnpm dev。” Then a clear result section “本地预览已就绪” followed by “http://localhost:3000” and the hint “复制到浏览器打开”。 This preview URL must appear only after the user asks in conversation. Add a small status “仅本机 · 由用户手动打开浏览器”. At the bottom keep the native executor prompt active with “>” and a cursor, plus a compact permission status “权限由 Codex 管理”.
Visual hierarchy: the provider's native TUI and direct conversation are primary. The project preview URL is a response from the agent, not the KC app's own web address. Use an understated KedaCode label only as launcher/context.
Safety and constraints: This is a static concept image, not a real terminal capture. No independent KC web chat, no session server URL, no auto-open browser, no frontend server before the user asks, no cloud/public URL, no bypass permissions.
Avoid: browser chrome or browser window, CodeBuddy logo/name, large separate application panels that suggest a KC UI, fake analytics, 3D rendering, people, watermarks, garbled dense text.
~~~
