fn main() {
    // 面板加载的是本机 http://127.0.0.1:<port> 的远程页面：远程来源的 IPC 调用
    // 一律要过 ACL，应用命令不会自动放行，因此这里为每个命令生成
    // `allow-<command>` 权限，由 capabilities/menubar-panel.json 显式授予。
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(&[
            "open_console_window",
            "open_external_url",
            "notify_task",
            "set_panel_pinned",
            "hide_panel",
        ])),
    )
    .expect("failed to run tauri-build");
}
