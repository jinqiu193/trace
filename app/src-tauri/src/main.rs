// Windows GUI 子系统：debug/release 均不弹控制台（sidecar 日志改写 sidecar.log）
#![windows_subsystem = "windows"]

fn main() {
    trace_desktop_lib::run()
}
