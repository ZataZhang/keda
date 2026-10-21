#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::env;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddrV4, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, WebviewWindow, WindowEvent};
#[cfg(not(target_os = "macos"))]
use tauri_plugin_positioner::{Position, WindowExt};

const CONSOLE_PROBE_PATH: &str = "/api/v1/agent-runner/console/version";
const COMPANION_PATH: &str = "/app/companion/?desktop=tauri";
const WINDOW_LABEL: &str = "main";
const CONSOLE_STARTUP_TIMEOUT: Duration = Duration::from_secs(30);
const CONSOLE_PROBE_INTERVAL: Duration = Duration::from_millis(400);

#[derive(Default)]
struct PanelState {
    pinned: Mutex<bool>,
}

#[derive(Default)]
struct ConsoleProcess {
    shutting_down: AtomicBool,
    child: Mutex<Option<Child>>,
}

impl Drop for ConsoleProcess {
    fn drop(&mut self) {
        if let Ok(child_slot) = self.child.get_mut() {
            if let Some(mut child) = child_slot.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

#[tauri::command]
fn set_panel_pinned(app: AppHandle, pinned: bool) -> Result<(), String> {
    let window = main_window(&app)?;
    let panel_state = app.state::<PanelState>();
    let previous_pinned = {
        let mut pinned_state = panel_state
            .pinned
            .lock()
            .map_err(|_| "窗口状态锁不可用".to_string())?;
        let previous_pinned = *pinned_state;
        // 先更新状态，避免切换窗口样式或聚焦时触发失焦处理器将面板隐藏。
        *pinned_state = pinned;
        previous_pinned
    };

    let transition_result = (|| {
        if pinned {
            window
                .set_decorations(true)
                .map_err(|error| format!("无法显示原生标题栏：{error}"))?;
            window
                .set_always_on_top(true)
                .map_err(|error| format!("无法置顶悬浮窗：{error}"))?;
            window
                .center()
                .map_err(|error| format!("无法定位悬浮窗：{error}"))?;
        } else {
            window
                .set_decorations(false)
                .map_err(|error| format!("无法切回菜单栏面板：{error}"))?;
            window
                .set_always_on_top(true)
                .map_err(|error| format!("无法显示菜单栏面板：{error}"))?;
            move_panel_to_tray(&window)?;
        }

        window
            .show()
            .map_err(|error| format!("无法显示状态面板：{error}"))?;
        window
            .set_focus()
            .map_err(|error| format!("无法聚焦状态面板：{error}"))?;

        Ok(())
    })();

    if let Err(error) = transition_result {
        if let Ok(mut pinned_state) = panel_state.pinned.lock() {
            *pinned_state = previous_pinned;
        }
        // 恢复切换前的窗口外观；回滚失败时保留原始错误供 UI 显示。
        let _ = window.set_decorations(previous_pinned);
        if !previous_pinned {
            let _ = move_panel_to_tray(&window);
        }
        return Err(error);
    }

    Ok(())
}

#[tauri::command]
fn hide_panel(window: WebviewWindow) -> Result<(), String> {
    window
        .hide()
        .map_err(|error| format!("无法隐藏状态面板：{error}"))
}

fn main_window(app: &AppHandle) -> Result<WebviewWindow, String> {
    app.get_webview_window(WINDOW_LABEL)
        .ok_or_else(|| "找不到 KedaCode 状态面板窗口".to_string())
}

fn move_panel_to_tray(window: &WebviewWindow) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        use objc2::MainThreadMarker;
        use objc2_app_kit::{NSEvent, NSScreen, NSWindow};
        use objc2_foundation::NSPoint;

        let main_thread = MainThreadMarker::new().ok_or("面板定位必须在主线程执行")?;
        let pointer = NSEvent::mouseLocation();
        let screens = NSScreen::screens(main_thread);
        let screen = screens
            .iter()
            .find(|screen| {
                let frame = screen.frame();
                pointer.x >= frame.origin.x
                    && pointer.x < frame.origin.x + frame.size.width
                    && pointer.y >= frame.origin.y
                    && pointer.y < frame.origin.y + frame.size.height
            })
            .ok_or("找不到菜单栏所在显示器")?;
        let native_pointer = window.ns_window().map_err(|error| error.to_string())?;
        // 调用方位于 UI 主线程，Tauri 窗口在整个调用期间持有原生窗口。
        let native_window = unsafe { &*native_pointer.cast::<NSWindow>() };
        // 全程使用 AppKit 的逻辑坐标，避免混合 DPI 和负坐标显示器被重复缩放。
        let visible_frame = screen.visibleFrame();
        let window_width = native_window.frame().size.width;
        let rightmost_x = (visible_frame.origin.x + visible_frame.size.width - window_width)
            .max(visible_frame.origin.x);
        let panel_x = (pointer.x - window_width / 2.0).clamp(visible_frame.origin.x, rightmost_x);
        native_window.setFrameTopLeftPoint(NSPoint::new(
            panel_x,
            visible_frame.origin.y + visible_frame.size.height,
        ));
        Ok(())
    }
    #[cfg(not(target_os = "macos"))]
    window
        .move_window_constrained(Position::TrayBottomCenter)
        .map_err(|error| error.to_string())
}

fn main() {
    let application = tauri::Builder::default()
        .plugin(tauri_plugin_positioner::init())
        .manage(PanelState::default())
        .manage(ConsoleProcess::default())
        .invoke_handler(tauri::generate_handler![set_panel_pinned, hide_panel])
        .on_window_event(|window, event| match event {
            WindowEvent::Focused(false) => {
                let app = window.app_handle();
                let pinned = app
                    .state::<PanelState>()
                    .pinned
                    .lock()
                    .map(|state| *state)
                    .unwrap_or(false);
                if !pinned {
                    let _ = window.hide();
                }
            }
            WindowEvent::CloseRequested { api, .. } => {
                api.prevent_close();
                let _ = window.hide();
            }
            _ => {}
        })
        .setup(|app| {
            #[cfg(target_os = "macos")]
            {
                use objc2_app_kit::{NSWindow, NSWindowCollectionBehavior};

                app.set_activation_policy(tauri::ActivationPolicy::Accessory);
                let window = main_window(app.handle())?;
                let native_window = window.ns_window()?;
                // setup 在主线程执行；指针由仍存活的 Tauri 窗口持有，不转移所有权。
                let native_window = unsafe { &*native_window.cast::<NSWindow>() };
                // 置顶只影响当前 Space；菜单栏面板还须加入其他桌面及全屏应用的 Space。
                let collection_behavior = (native_window.collectionBehavior()
                    & !NSWindowCollectionBehavior::FullScreenPrimary)
                    | NSWindowCollectionBehavior::CanJoinAllSpaces
                    | NSWindowCollectionBehavior::FullScreenAuxiliary;
                // macOS 13 起需显式声明可加入其他应用的全屏/台前调度空间。
                let collection_behavior = if objc2::available!(macos = 13.0) {
                    (collection_behavior
                        & !(NSWindowCollectionBehavior::Primary
                            | NSWindowCollectionBehavior::Auxiliary))
                        | NSWindowCollectionBehavior::CanJoinAllApplications
                } else {
                    collection_behavior
                };
                native_window.setCollectionBehavior(collection_behavior);
            }

            let quit_item = MenuItem::with_id(app, "quit", "退出 KedaCode", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&quit_item])?;
            let icon = tauri::include_image!("icons/menubar.png");

            TrayIconBuilder::with_id("kedacode-menubar")
                .icon(icon)
                .icon_as_template(true)
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| {
                    if event.id().as_ref() == "quit" {
                        app.exit(0);
                    }
                })
                .on_tray_icon_event(|tray, event| {
                    tauri_plugin_positioner::on_tray_event(tray.app_handle(), &event);
                    let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    else {
                        return;
                    };

                    let app = tray.app_handle();
                    let Ok(window) = main_window(app) else {
                        return;
                    };
                    if window.is_visible().unwrap_or(false) {
                        let _ = window.hide();
                        return;
                    }

                    let pinned = app
                        .state::<PanelState>()
                        .pinned
                        .lock()
                        .map(|state| *state)
                        .unwrap_or(false);
                    if !pinned {
                        if let Err(error) = move_panel_to_tray(&window) {
                            append_log(&format!("面板定位失败：{error}\n"));
                            return;
                        }
                    }
                    let show_result = window.show();
                    let focus_result = window.set_focus();
                    if let Err(error) = show_result.and(focus_result) {
                        append_log(&format!("面板显示失败：{error}\n"));
                    }
                })
                .build(app)?;

            let app_handle = app.handle().clone();
            thread::spawn(move || match launch_console(&app_handle) {
                Ok(console_origin) => {
                    if let Ok(window) = main_window(&app_handle) {
                        let companion_url = format!("{console_origin}{COMPANION_PATH}");
                        match tauri::Url::parse(&companion_url) {
                            Ok(url) => {
                                if let Err(error) = window.navigate(url) {
                                    set_startup_status(
                                        &window,
                                        &format!("Console 已启动，但状态页面加载失败：{error}"),
                                    );
                                }
                            }
                            Err(error) => {
                                set_startup_status(&window, &format!("Console 地址无效：{error}"))
                            }
                        }
                    }
                }
                Err(error) => {
                    append_log(&format!("启动失败：{error}\n"));
                    if let Ok(window) = main_window(&app_handle) {
                        set_startup_status(&window, &error);
                    }
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("无法初始化 KedaCode 菜单栏应用");

    application.run(|app, event| {
        if matches!(event, RunEvent::Exit) {
            stop_console(app, true);
        }
    });
}

fn launch_console(app: &AppHandle) -> Result<String, String> {
    let console_cli = find_console_cli().ok_or_else(|| {
        "没有找到 kc 命令。请先安装 KedaCode CLI（uv tool install kedacode），再重新打开菜单栏 App。".to_string()
    })?;
    let port = choose_free_port()?;
    let origin = format!("http://127.0.0.1:{port}");
    let log_file = open_log_file().map_err(|error| format!("无法创建 Console 日志：{error}"))?;
    let log_copy = log_file
        .try_clone()
        .map_err(|error| format!("无法打开 Console 日志：{error}"))?;
    let mut command = Command::new(&console_cli);
    command
        .args(["console", "--no-browser", "--port", &port.to_string()])
        .stdin(Stdio::null())
        .stdout(Stdio::from(log_copy))
        .stderr(Stdio::from(log_file));

    if let Some(repository_root) = repository_root() {
        command.current_dir(repository_root);
    }

    let mut child = command
        .spawn()
        .map_err(|error| format!("无法启动 {}：{error}", console_cli.display()))?;
    append_log(&format!(
        "已启动 kc console，端口 {port}，程序 {}\n",
        console_cli.display()
    ));
    let process_state = app.state::<ConsoleProcess>();
    let Ok(mut child_slot) = process_state.child.lock() else {
        let _ = child.kill();
        let _ = child.wait();
        return Err("Console 进程状态锁不可用".to_string());
    };
    if process_state.shutting_down.load(Ordering::Acquire) {
        drop(child_slot);
        let _ = child.kill();
        let _ = child.wait();
        return Err("KedaCode 菜单栏 App 正在退出".to_string());
    }
    child_slot.replace(child);
    drop(child_slot);

    let deadline = Instant::now() + CONSOLE_STARTUP_TIMEOUT;
    while Instant::now() < deadline {
        if console_is_ready(port) {
            append_log(&format!("Console 已就绪：{origin}\n"));
            return Ok(origin);
        }
        if child_has_exited(app) {
            return Err(
                "kc console 已退出。请查看 ~/Library/Logs/KedaCode/desktop-console.log。"
                    .to_string(),
            );
        }
        thread::sleep(CONSOLE_PROBE_INTERVAL);
    }

    stop_console(app, false);
    Err("kc console 启动超时。请查看 ~/Library/Logs/KedaCode/desktop-console.log。".to_string())
}

fn find_console_cli() -> Option<PathBuf> {
    let home_directory = env::var_os("HOME").map(PathBuf::from);
    let mut candidates = Vec::new();
    if let Some(home) = home_directory.as_ref() {
        candidates.push(home.join(".local/bin/kc"));
    }
    candidates.push(PathBuf::from("/opt/homebrew/bin/kc"));
    candidates.push(PathBuf::from("/usr/local/bin/kc"));
    if let Some(path_value) = env::var_os("PATH") {
        candidates.extend(env::split_paths(&path_value).map(|directory| directory.join("kc")));
    }
    candidates.into_iter().find(|candidate| candidate.is_file())
}

fn repository_root() -> Option<PathBuf> {
    let manifest_directory = Path::new(env!("CARGO_MANIFEST_DIR"));
    let root = manifest_directory.parent()?.parent()?;
    root.is_dir().then(|| root.to_path_buf())
}

fn choose_free_port() -> Result<u16, String> {
    let listener = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0))
        .map_err(|error| format!("无法分配本机 Console 端口：{error}"))?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| format!("无法读取本机 Console 端口：{error}"))
}

fn console_is_ready(port: u16) -> bool {
    let socket_address = SocketAddrV4::new(Ipv4Addr::LOCALHOST, port);
    let Ok(mut stream) =
        TcpStream::connect_timeout(&socket_address.into(), Duration::from_millis(500))
    else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let request = format!(
        "GET {CONSOLE_PROBE_PATH} HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }

    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() {
        return false;
    }
    response.starts_with("HTTP/1.1 200") && response.contains("\"version\"")
}

fn child_has_exited(app: &AppHandle) -> bool {
    let process_state = app.state::<ConsoleProcess>();
    let Ok(mut child_slot) = process_state.child.lock() else {
        return true;
    };
    let Some(child) = child_slot.as_mut() else {
        return true;
    };
    match child.try_wait() {
        Ok(Some(status)) => {
            child_slot.take();
            append_log(&format!("kc console 提前退出：{status}\n"));
            true
        }
        Ok(None) => false,
        Err(error) => {
            append_log(&format!("检查 kc console 状态失败：{error}\n"));
            true
        }
    }
}

fn stop_console(app: &AppHandle, app_is_exiting: bool) {
    let process_state = app.state::<ConsoleProcess>();
    if app_is_exiting {
        process_state.shutting_down.store(true, Ordering::Release);
    }
    let Ok(mut child_slot) = process_state.child.lock() else {
        return;
    };
    if let Some(mut child) = child_slot.take() {
        let _ = child.kill();
        let _ = child.wait();
        append_log("已回收 KedaCode 菜单栏 App 启动的 kc console。\n");
    }
}

fn set_startup_status(window: &WebviewWindow, message: &str) {
    let json_message = serde_json::to_string(message)
        .unwrap_or_else(|_| "\"状态面板启动失败，请查看桌面 Console 日志。\"".to_string());
    let script = format!(
        "const status = document.getElementById('status'); if (status) status.textContent = {json_message};"
    );
    let _ = window.eval(&script);
}

fn open_log_file() -> std::io::Result<File> {
    let home = env::var_os("HOME")
        .map(PathBuf::from)
        .ok_or_else(|| std::io::Error::other("找不到 HOME 目录"))?;
    let log_directory = home.join("Library/Logs/KedaCode");
    fs::create_dir_all(&log_directory)?;
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_directory.join("desktop-console.log"))
}

fn append_log(message: &str) {
    if let Ok(mut log_file) = open_log_file() {
        let _ = log_file.write_all(message.as_bytes());
    }
}
