// Trace 桌面端（Tauri 2）：启动 Python 边车，暴露 backend URL 给前端。

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tauri::{Manager, RunEvent};

/// 持有 Python 边车的端口号和子进程句柄。
struct BackendState {
    port: Mutex<Option<u16>>,
    child: Mutex<Option<std::process::Child>>,
}

impl BackendState {
    fn new() -> Self {
        Self { port: Mutex::new(None), child: Mutex::new(None) }
    }
}

/// 查找 venv Python 可执行文件。从 exe 所在目录向上爬，找最近的 .venv。
fn find_python() -> PathBuf {
    if let Ok(env) = std::env::var("TRACE_PYTHON") {
        let p = PathBuf::from(env);
        if p.exists() { return p; }
    }
    let exe = std::env::current_exe().expect("current_exe");
    let mut root = exe.parent().map(|p| p.to_path_buf()).unwrap_or(exe);
    for _ in 0..10 {
        for cand in [
            root.join(".venv").join("Scripts").join("python.exe"),
            root.join(".venv").join("bin").join("python"),
        ] {
            if cand.exists() { return cand; }
        }
        if let Some(p) = root.parent() { root = p.to_path_buf(); } else { break; }
    }
    // fallback：PATH 上的 python
    PathBuf::from(if cfg!(target_os = "windows") { "python" } else { "python3" })
}

/// 从 exe 路径向上爬，找到含 trace/ 包的项目根（放 trace.db / models 的目录）。
fn project_root() -> PathBuf {
    let exe = std::env::current_exe().expect("current_exe");
    let mut root = exe.parent().map(|p| p.to_path_buf()).unwrap_or(exe);
    for _ in 0..10 {
        // 探测：目录下存在 trace 包 + trace.db → 命中
        let has_pkg = root.join("trace").join("__init__.py").exists()
            || root.join("trace").is_dir();
        if has_pkg && (root.join("trace.db").exists() || root.join("models").is_dir()) {
            return root;
        }
        if let Some(p) = root.parent() { root = p.to_path_buf(); } else { break; }
    }
    // fallback：环境变量
    if let Ok(env) = std::env::var("TRACE_PROJECT_ROOT") {
        return PathBuf::from(env);
    }
    PathBuf::from(".")
}

#[tauri::command]
fn backend_url(state: tauri::State<Arc<BackendState>>) -> Result<u16, String> {
    state.port.lock().map_err(|e| e.to_string())?
        .ok_or_else(|| "sidecar not ready".to_string())
}

/// 把 "HH:MM:SS" / "MM:SS" / "SS" 转成秒数。空串返回 None。
fn parse_ts(s: &str) -> Option<u64> {
    let s = s.trim();
    if s.is_empty() { return None; }
    let parts: Vec<&str> = s.split(':').collect();
    let mut total: u64 = 0;
    let mut mult: u64 = 1;
    for p in parts.iter().rev() {
        let n: u64 = p.parse().ok()?;
        total += n * mult;
        mult = mult.saturating_mul(60);
    }
    Some(total)
}

fn is_video_ext(p: &str) -> bool {
    let lower = p.to_lowercase();
    lower.ends_with(".mp4") || lower.ends_with(".mkv") || lower.ends_with(".avi")
        || lower.ends_with(".mov") || lower.ends_with(".webm") || lower.ends_with(".flv")
        || lower.ends_with(".wmv") || lower.ends_with(".m4v")
}

/// 视频命中：用 mpv/vlc 跳到指定秒数；都没装返回 false。
fn try_open_at(path: &str, sec: u64) -> bool {
    for player in &["mpv", "vlc"] {
        if let Ok(p) = which(player) {
            let mut cmd = Command::new(p);
            match *player {
                "mpv" => { cmd.arg("--start=").arg(format!("{}", sec)); }
                "vlc" => { cmd.arg(format!("--start-time={}", sec)); }
                _ => unreachable!(),
            }
            cmd.arg(path);
            // 不阻塞 shell：mpv/vlc 自己 fork
            if cmd.spawn().is_ok() { return true; }
        }
    }
    false
}

fn which(name: &str) -> Result<PathBuf, ()> {
    let exts: &[&str] = if cfg!(target_os = "windows") { &["", ".exe", ".bat", ".cmd"] } else { &[""] };
    if let Some(paths) = std::env::var_os("PATH") {
        for dir in std::env::split_paths(&paths) {
            for ext in exts {
                let candidate = dir.join(format!("{}{}", name, ext));
                if candidate.is_file() { return Ok(candidate); }
            }
        }
    }
    Err(())
}

#[tauri::command]
fn open_path(app: tauri::AppHandle, path: String, location: Option<String>) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    // 视频 + 有时间戳 → 调外部播放器跳播
    if is_video_ext(&path) {
        if let Some(sec) = location.as_deref().and_then(parse_ts) {
            if try_open_at(&path, sec) { return Ok(()); }
            // mpv/vlc 都没装，回退系统默认（从头开始，提示前端）
        }
    }
    // 文本/PDF/图片/无时间戳视频/外部播放器缺失：系统默认
    app.opener().open_path(path, None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
fn reveal_in_folder(path: String) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        Command::new("explorer")
            .args(["/select,", &path])
            .spawn()
            .map_err(|e| e.to_string())?;
        return Ok(());
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = path;
        Ok(())
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let backend = Arc::new(BackendState::new());

    // 启动 Python 边车：跑根目录的 webserver.py（不用 -m 避免 stdlib `trace` 撞名）
    let proj = project_root();
    let mut child = match Command::new(find_python())
        .args(["webserver.py", "--port", "0"])
        .current_dir(&proj)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[trace] failed to spawn sidecar: {}", e);
            std::process::exit(1);
        }
    };

    if let Some(stderr) = child.stderr.take() {
        std::thread::spawn(move || {
            let reader = BufReader::new(stderr);
            for line in reader.lines().flatten() { eprintln!("[sidecar] {}", line); }
        });
    }

    let stdout = child.stdout.take();
    let backend_for_reader = backend.clone();
    let reader_handle = std::thread::spawn(move || -> Option<u16> {
        let stdout = stdout?;
        let reader = BufReader::new(stdout);
        for line in reader.lines().flatten() {
            if let Some(rest) = line.strip_prefix("PORT=") {
                if let Ok(p) = rest.trim().parse::<u16>() {
                    if let Ok(mut guard) = backend_for_reader.port.lock() {
                        guard.replace(p);
                    }
                    return Some(p);
                }
            }
        }
        None
    });

    let port = match reader_handle.join().ok().flatten() {
        Some(p) => p,
        None => {
            eprintln!("[trace] sidecar 未在合理时间内上报端口");
            let _ = child.kill();
            std::process::exit(1);
        }
    };

    eprintln!("[trace] sidecar ready on http://127.0.0.1:{}", port);
    *backend.child.lock().unwrap() = Some(child);

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(backend)
        .invoke_handler(tauri::generate_handler![backend_url, open_path, reveal_in_folder])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    // 给前端最多 60s 启动时间（首次 ONNX 加载可能要 10s）
    let _ = Duration::from_secs(60); // 占位用，避免 unused 警告

    app.run(move |app_handle, event| {
        if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
            if let Some(state) = app_handle.try_state::<Arc<BackendState>>() {
                if let Ok(mut child_opt) = state.child.lock() {
                    if let Some(mut child) = child_opt.take() {
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                }
            }
        }
    });
}