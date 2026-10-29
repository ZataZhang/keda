use std::env;
use std::ffi::OsString;
use std::fs::{self, OpenOptions};
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const PATH_BEGIN_MARKER: &str = "__KEDACODE_LOGIN_PATH_BEGIN__";
const PATH_END_MARKER: &str = "__KEDACODE_LOGIN_PATH_END__";
const LOGIN_SHELL_TIMEOUT: Duration = Duration::from_secs(5);
const LOGIN_SHELL_POLL_INTERVAL: Duration = Duration::from_millis(25);

/// 获取当前用户登录 shell 配置后的 PATH，并保留桌面 App 原有的路径项。
pub(super) fn resolve_user_path() -> Option<OsString> {
    let capture_file = create_capture_file()?;
    let resolved_path = capture_login_path(&capture_file);
    let _ = fs::remove_file(capture_file);
    resolved_path
}

fn create_capture_file() -> Option<PathBuf> {
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .ok()?
        .as_nanos();
    let capture_file = env::temp_dir().join(format!(
        "kedacode-login-path-{}-{timestamp}.txt",
        std::process::id()
    ));
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&capture_file)
        .ok()?;
    Some(capture_file)
}

fn capture_login_path(capture_file: &Path) -> Option<OsString> {
    let shell_path = env::var_os("SHELL")
        .map(PathBuf::from)
        .filter(|shell_path| shell_path.is_absolute() && shell_path.is_file())
        .unwrap_or_else(|| PathBuf::from("/bin/zsh"));
    let capture_script = format!(
        "printf '%s' '{PATH_BEGIN_MARKER}' > \"$KEDACODE_LOGIN_PATH_CAPTURE\"; \
         /usr/bin/printenv PATH >> \"$KEDACODE_LOGIN_PATH_CAPTURE\"; \
         printf '%s' '{PATH_END_MARKER}' >> \"$KEDACODE_LOGIN_PATH_CAPTURE\""
    );
    let mut shell_process = Command::new(shell_path)
        .args(["-l", "-i", "-c", &capture_script])
        .env("KEDACODE_LOGIN_PATH_CAPTURE", capture_file)
        .env("HISTFILE", "/dev/null")
        .env("SAVEHIST", "0")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;

    let deadline = Instant::now() + LOGIN_SHELL_TIMEOUT;
    loop {
        match shell_process.try_wait() {
            Ok(Some(status)) if status.success() => break,
            Ok(Some(_)) => return None,
            Ok(None) if Instant::now() < deadline => thread::sleep(LOGIN_SHELL_POLL_INTERVAL),
            Ok(None) | Err(_) => {
                let _ = shell_process.kill();
                let _ = shell_process.wait();
                return None;
            }
        }
    }

    let captured_output = fs::read_to_string(capture_file).ok()?;
    let path_start = captured_output.rfind(PATH_BEGIN_MARKER)? + PATH_BEGIN_MARKER.len();
    let path_end = path_start + captured_output[path_start..].find(PATH_END_MARKER)?;
    let login_path = captured_output[path_start..path_end]
        .trim_matches(|character| character == '\r' || character == '\n');
    if login_path.is_empty() {
        return None;
    }

    let mut path_entries = env::split_paths(&OsString::from(login_path))
        .filter(|entry| !entry.as_os_str().is_empty())
        .collect::<Vec<_>>();
    if let Some(inherited_path) = env::var_os("PATH") {
        for path_entry in env::split_paths(&inherited_path) {
            if !path_entry.as_os_str().is_empty() && !path_entries.contains(&path_entry) {
                path_entries.push(path_entry);
            }
        }
    }
    env::join_paths(path_entries).ok()
}
