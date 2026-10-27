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

#[cfg(target_os = "macos")]
use objc2_app_kit::NSScreen;
#[cfg(target_os = "macos")]
use objc2_foundation::NSPoint;
use tauri::menu::{CheckMenuItem, Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{
    ActivationPolicy, AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
    WindowEvent,
};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
use tauri_plugin_notification::NotificationExt;
#[cfg(not(target_os = "macos"))]
use tauri_plugin_positioner::{Position, WindowExt};

const CONSOLE_PROBE_PATH: &str = "/api/v1/agent-runner/console/version";
const COMPANION_PATH: &str = "/app/companion/?desktop=tauri";
const CONSOLE_WINDOW_PATH: &str = "/app/dashboard";
const WINDOW_LABEL: &str = "main";
const CONSOLE_WINDOW_LABEL: &str = "console";
const PANEL_SHORTCUT: &str = "CommandOrControl+Shift+K";
/// 登录自启时由 autostart 插件附加：此时不该弹窗口打扰用户。
const AUTOSTART_FLAG: &str = "--autostart";
const CONSOLE_STARTUP_TIMEOUT: Duration = Duration::from_secs(30);
const CONSOLE_PROBE_INTERVAL: Duration = Duration::from_millis(400);

#[derive(Default)]
struct PanelState {
    pinned: Mutex<bool>,
    dock_icon_visible: Mutex<bool>,
    console_origin: Mutex<Option<String>>,
}

#[derive(Default)]
struct MenuState {
    dock_item: Mutex<Option<CheckMenuItem<tauri::Wry>>>,
    autostart_item: Mutex<Option<CheckMenuItem<tauri::Wry>>>,
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

/// 把本 App 激活为前台：菜单栏 App（Accessory）显示面板前必须先抢一次活跃态。
fn activate_app() {
    #[cfg(target_os = "macos")]
    {
        use objc2::MainThreadMarker;
        use objc2_app_kit::NSApplication;

        let Some(main_thread) = MainThreadMarker::new() else {
            return;
        };
        let application = NSApplication::sharedApplication(main_thread);
        application.activate();
    }
}

/// 显示并聚焦状态面板；未固定的面板先定位到菜单栏下方。
fn show_panel(app: &AppHandle) -> Result<(), String> {
    let window = main_window(app)?;
    let pinned = app
        .state::<PanelState>()
        .pinned
        .lock()
        .map(|state| *state)
        .unwrap_or(false);
    if !pinned {
        // 定位失败不该让面板彻底不出现：退化为居中，至少让用户看得见。
        if let Err(error) = move_panel_to_tray(&window) {
            append_log(&format!("面板定位失败：{error}，改为居中显示。\n"));
            let _ = window.center();
        }
    }
    // Accessory 模式下的 App 默认不是活跃 App：不先激活，面板刚 show 出来就会
    // 立刻收到失焦事件而被收起，表现为「点了图标什么都没出现」。
    activate_app();
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

/// 托盘点击与全局快捷键共用的开关：可见就收起，不可见就定位后展开。
fn toggle_panel(app: &AppHandle) {
    let Ok(window) = main_window(app) else {
        return;
    };
    if window.is_visible().unwrap_or(false) {
        let _ = window.hide();
        return;
    }
    if let Err(error) = show_panel(app) {
        append_log(&format!("面板显示失败：{error}\n"));
    }
}

/// 打开或聚焦完整控制台窗口。
///
/// 面板只放得下四条任务，完整页面（backlog / stats / 运行历史）在同一个
/// Console 上，因此这里复用已启动的 Console 地址另开一个普通窗口。
#[tauri::command]
fn open_console_window(app: AppHandle) -> Result<(), String> {
    let origin = app
        .state::<PanelState>()
        .console_origin
        .lock()
        .map_err(|_| "Console 地址状态锁不可用".to_string())?
        .clone()
        .ok_or_else(|| "本机 Console 尚未就绪，请稍后再试".to_string())?;
    let url = format!("{origin}{CONSOLE_WINDOW_PATH}");
    let parsed = tauri::Url::parse(&url).map_err(|error| format!("控制台地址无效：{error}"))?;

    // 完整控制台是「正常 App 的主窗口」：先切到 Regular（Dock + Cmd+Tab 可见），
    // 再激活，否则 Accessory 模式下窗口只会出现在后台。
    promote_to_regular_app(&app);

    let console_window = if let Some(window) = app.get_webview_window(CONSOLE_WINDOW_LABEL) {
        window
            .navigate(parsed)
            .map_err(|error| format!("无法打开控制台窗口：{error}"))?;
        window
    } else {
        WebviewWindowBuilder::new(&app, CONSOLE_WINDOW_LABEL, WebviewUrl::External(parsed))
            .title("KedaCode 控制台")
            .inner_size(1280.0, 860.0)
            .min_inner_size(960.0, 640.0)
            .center()
            .build()
            .map_err(|error| format!("无法打开控制台窗口：{error}"))?
    };

    console_window
        .show()
        .map_err(|error| format!("无法显示控制台窗口：{error}"))?;
    console_window
        .set_focus()
        .map_err(|error| format!("无法聚焦控制台窗口：{error}"))?;
    // 窗口已经存在后再激活 App：先激活再建窗口时，App 会在窗口出现前就被别的
    // 进程抢回前台，用户看到的仍是「窗口在后台」。
    let activation_handle = app.clone();
    let _ = activation_handle.run_on_main_thread(activate_app);
    Ok(())
}

/// 在系统默认浏览器中打开 http/https 链接。
///
/// 控制台窗口是 Tauri 的 WKWebView，`target="_blank"` 不会被它转换成系统浏览器
/// 新标签（没有实现新窗口代理），页面里的 Issue / PR 链接看起来就「点了没反应」。
/// 前端在 Tauri 环境里改走这个命令，由宿主调用 `open` 交给用户配置的浏览器。
#[tauri::command]
fn open_external_url(url: String) -> Result<(), String> {
    let parsed = tauri::Url::parse(&url).map_err(|error| format!("链接无效：{error}"))?;
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err("只允许在系统浏览器中打开 http/https 链接".to_string());
    }
    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg(parsed.as_str())
            .spawn()
            .map_err(|error| format!("无法打开系统浏览器：{error}"))?;
    }
    #[cfg(not(target_os = "macos"))]
    {
        return Err("当前平台未实现外部浏览器打开".to_string());
    }
    Ok(())
}

/// 切到常规 App 形态：有 Dock 图标、进 Cmd+Tab、窗口能正经到前台。
fn promote_to_regular_app(app: &AppHandle) {
    if let Err(error) = app.set_activation_policy(ActivationPolicy::Regular) {
        append_log(&format!("切换常规 App 形态失败：{error}\n"));
        return;
    }
    // 启动路径跑在工作线程上，AppKit 的激活只能在主线程做。
    let main_thread_handle = app.clone();
    let _ = main_thread_handle.run_on_main_thread(activate_app);
}

/// 控制台窗口关闭后回到菜单栏形态；用户显式勾了 Dock 图标则保持不动。
fn restore_menubar_policy(app: &AppHandle) {
    let dock_icon_visible = app
        .state::<PanelState>()
        .dock_icon_visible
        .lock()
        .map(|visible| *visible)
        .unwrap_or(false);
    if dock_icon_visible {
        return;
    }
    if let Err(error) = app.set_activation_policy(ActivationPolicy::Accessory) {
        append_log(&format!("切回菜单栏形态失败：{error}\n"));
    }
}

/// 本次启动是否由登录自启触发。
fn launched_by_autostart() -> bool {
    std::env::args().any(|argument| argument == AUTOSTART_FLAG)
}

/// 发一条系统通知；任务进入阻塞或失败时由面板触发。
#[tauri::command]
fn notify_task(app: AppHandle, title: String, body: String) -> Result<(), String> {
    let notification = app.notification();
    let granted = matches!(
        notification.permission_state(),
        Ok(tauri_plugin_notification::PermissionState::Granted)
    );
    if !granted {
        let _ = notification.request_permission();
    }
    notification
        .builder()
        .title(title)
        .body(body)
        .show()
        .map_err(|error| format!("发送系统通知失败：{error}"))
}

/// 切换 Dock 图标。菜单栏模式（Accessory）不占 Dock，也不进 Cmd+Tab。
fn toggle_dock_icon(app: &AppHandle) {
    let Some(next) = toggle_flag(&app.state::<PanelState>().dock_icon_visible) else {
        return;
    };
    let policy = if next {
        ActivationPolicy::Regular
    } else {
        ActivationPolicy::Accessory
    };
    if let Err(error) = app.set_activation_policy(policy) {
        append_log(&format!("切换 Dock 图标失败：{error}\n"));
        return;
    }
    // 切回 Regular 后要主动激活一次，否则 Dock 图标出现了窗口还在后台。
    if next {
        let _ = show_panel(app);
    }
    set_menu_checked(app, MenuSlot::Dock, next);
}

/// 切换登录自启；状态由 autostart 插件持久化，菜单勾选只是回显。
fn toggle_autostart(app: &AppHandle) {
    let manager = app.autolaunch();
    let enabled = manager.is_enabled().unwrap_or(false);
    let transition = if enabled {
        manager.disable()
    } else {
        manager.enable()
    };
    match transition {
        Ok(()) => set_menu_checked(app, MenuSlot::Autostart, !enabled),
        Err(error) => append_log(&format!("切换登录自启失败：{error}\n")),
    }
}

/// 翻转一个布尔状态；锁中毒时返回 None，调用方直接放弃本次动作。
fn toggle_flag(slot: &Mutex<bool>) -> Option<bool> {
    let mut value = slot.lock().ok()?;
    *value = !*value;
    Some(*value)
}

#[derive(Clone, Copy)]
enum MenuSlot {
    Dock,
    Autostart,
}

/// 回显托盘菜单里的勾选态；该状态不落盘，重启后回到默认。
fn set_menu_checked(app: &AppHandle, slot: MenuSlot, checked: bool) {
    let menu_state = app.state::<MenuState>();
    let handle = match slot {
        MenuSlot::Dock => menu_state.dock_item.lock().ok().and_then(|item| item.clone()),
        MenuSlot::Autostart => menu_state
            .autostart_item
            .lock()
            .ok()
            .and_then(|item| item.clone()),
    };
    if let Some(item) = handle {
        let _ = item.set_checked(checked);
    }
}

fn main_window(app: &AppHandle) -> Result<WebviewWindow, String> {
    app.get_webview_window(WINDOW_LABEL)
        .ok_or_else(|| "找不到 KedaCode 状态面板窗口".to_string())
}

/// 指针是否落在该显示器的矩形内（AppKit 逻辑坐标，可为负）。
#[cfg(target_os = "macos")]
fn screen_contains_pointer(screen: &NSScreen, pointer: NSPoint) -> bool {
    let frame = screen.frame();
    pointer.x >= frame.origin.x
        && pointer.x < frame.origin.x + frame.size.width
        && pointer.y >= frame.origin.y
        && pointer.y < frame.origin.y + frame.size.height
}

/// 指针到该显示器中心的距离，用于「指针落在多屏缝隙」时的兜底选屏。
#[cfg(target_os = "macos")]
fn screen_pointer_distance(screen: &NSScreen, pointer: NSPoint) -> f64 {
    let frame = screen.frame();
    let center_x = frame.origin.x + frame.size.width / 2.0;
    let center_y = frame.origin.y + frame.size.height / 2.0;
    ((pointer.x - center_x).powi(2) + (pointer.y - center_y).powi(2)).sqrt()
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
        // 多显示器之间可能留有空隙：指针落在间隙里时 `find` 会一无所获，
        // 这里退化为「离指针最近的那块屏」，别让一次定位失败把面板整个吞掉。
        let nearest = screens.iter().min_by(|left, right| {
            screen_pointer_distance(left, pointer)
                .partial_cmp(&screen_pointer_distance(right, pointer))
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        let screen = screens
            .iter()
            .find(|screen| screen_contains_pointer(screen, pointer))
            .or(nearest)
            .ok_or("找不到可用显示器")?;
        let native_pointer = window.ns_window().map_err(|error| error.to_string())?;
        // 调用方位于 UI 主线程，Tauri 窗口在整个调用期间持有原生窗口。
        let native_window = unsafe { &*native_pointer.cast::<NSWindow>() };
        // 全程使用 AppKit 的逻辑坐标，避免混合 DPI 和负坐标显示器被重复缩放。
        let visible_frame = screen.visibleFrame();
        let window_frame = native_window.frame();
        let window_width = window_frame.size.width;
        let window_height = window_frame.size.height;
        let leftmost_x = visible_frame.origin.x;
        let rightmost_x = (visible_frame.origin.x + visible_frame.size.width - window_width)
            .max(leftmost_x);
        let panel_x = (pointer.x - window_width / 2.0).clamp(leftmost_x, rightmost_x);
        // 顶边贴住菜单栏下沿；窗口比可见区还高时退化为底边对齐，
        // 避免 clamp 的上下界倒挂。
        let lowest_top = visible_frame.origin.y + window_height;
        let highest_top = visible_frame.origin.y + visible_frame.size.height;
        let panel_top = highest_top.clamp(lowest_top.min(highest_top), highest_top);
        native_window.setFrameTopLeftPoint(NSPoint::new(panel_x, panel_top));
        Ok(())
    }
    #[cfg(not(target_os = "macos"))]
    window
        .move_window_constrained(Position::TrayBottomCenter)
        .map_err(|error| error.to_string())
}

fn main() {
    let application = tauri::Builder::default()
        // 单实例必须最先注册：第二次启动只唤醒已有面板，不再拉第二个 console。
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            // 用户再次点 App 图标 = 想看主界面；Console 还没起来时才退回面板。
            if let Err(error) = open_console_window(app.clone()) {
                append_log(&format!("重复启动打开控制台失败：{error}，退回状态面板。\n"));
                if let Err(panel_error) = show_panel(app) {
                    append_log(&format!("重复启动唤出面板失败：{panel_error}\n"));
                }
            }
        }))
        .plugin(tauri_plugin_positioner::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec![AUTOSTART_FLAG]),
        ))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        toggle_panel(app);
                    }
                })
                .build(),
        )
        .manage(PanelState::default())
        .manage(MenuState::default())
        .manage(ConsoleProcess::default())
        .invoke_handler(tauri::generate_handler![
            set_panel_pinned,
            hide_panel,
            open_console_window,
            open_external_url,
            notify_task
        ])
        .on_window_event(|window, event| {
            if window.label() == CONSOLE_WINDOW_LABEL {
                // 控制台是普通窗口：关掉它就把 App 退回菜单栏形态。
                if matches!(event, WindowEvent::Destroyed) {
                    restore_menubar_policy(window.app_handle());
                }
                return;
            }
            // 失焦收起与关窗隐藏只适用于菜单栏面板。
            if window.label() != WINDOW_LABEL {
                return;
            }
            match event {
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
            }
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

            let open_console_item =
                MenuItem::with_id(app, "open-console", "打开完整控制台", true, None::<&str>)?;
            let dock_item = CheckMenuItem::with_id(
                app,
                "dock-icon",
                "在 Dock 中显示图标",
                true,
                false,
                None::<&str>,
            )?;
            let autostart_item = CheckMenuItem::with_id(
                app,
                "autostart",
                "登录时启动",
                true,
                app.autolaunch().is_enabled().unwrap_or(false),
                None::<&str>,
            )?;
            let quit_item = MenuItem::with_id(app, "quit", "退出 KedaCode", true, None::<&str>)?;
            let menu = Menu::with_items(
                app,
                &[&open_console_item, &dock_item, &autostart_item, &quit_item],
            )?;

            let menu_state = app.state::<MenuState>();
            if let Ok(mut slot) = menu_state.dock_item.lock() {
                *slot = Some(dock_item);
            }
            if let Ok(mut slot) = menu_state.autostart_item.lock() {
                *slot = Some(autostart_item);
            }

            if let Err(error) = app.global_shortcut().register(PANEL_SHORTCUT) {
                append_log(&format!("注册全局快捷键失败：{error}\n"));
            }

            let icon = tauri::include_image!("icons/menubar.png");

            TrayIconBuilder::with_id("kedacode-menubar")
                .icon(icon)
                .icon_as_template(true)
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "open-console" => {
                        if let Err(error) = open_console_window(app.clone()) {
                            append_log(&format!("打开控制台失败：{error}\n"));
                        }
                    }
                    "dock-icon" => toggle_dock_icon(app),
                    "autostart" => toggle_autostart(app),
                    "quit" => app.exit(0),
                    _ => {}
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

                    toggle_panel(tray.app_handle());
                })
                .build(app)?;

            let app_handle = app.handle().clone();
            thread::spawn(move || match launch_console(&app_handle) {
                Ok(console_origin) => {
                    // 完整控制台窗口与面板共用同一个 Console 地址，启动成功后记下来。
                    if let Ok(mut slot) = app_handle.state::<PanelState>().console_origin.lock() {
                        *slot = Some(console_origin.clone());
                    }
                    // 用户点图标打开 App 就该看到主界面；登录自启则安静待在菜单栏。
                    if !launched_by_autostart() {
                        if let Err(error) = open_console_window(app_handle.clone()) {
                            append_log(&format!("启动时打开控制台失败：{error}\n"));
                        }
                    }
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

    application.run(|app, event| match event {
        RunEvent::Exit => stop_console(app, true),
        // 点 Dock 图标（或 Cmd+Tab 回来）时，主界面应该是完整控制台。
        #[cfg(target_os = "macos")]
        RunEvent::Reopen { .. } => {
            if let Err(error) = open_console_window(app.clone()) {
                append_log(&format!("前台唤出控制台失败：{error}\n"));
            }
        }
        _ => {}
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
