use std::collections::HashMap;
use std::sync::Mutex;

use tauri::Emitter;

struct PtySessionState {
    /// trace_id → pty_handler_id mapping
    trace_map: Mutex<HashMap<String, u32>>,
}

#[tauri::command]
fn register_trace_pty(
    trace_id: String,
    handler_id: u32,
    state: tauri::State<'_, PtySessionState>,
) -> Result<(), String> {
    state
        .trace_map
        .lock()
        .map_err(|e| e.to_string())?
        .insert(trace_id, handler_id);
    Ok(())
}

#[tauri::command]
fn unregister_trace_pty(
    trace_id: String,
    state: tauri::State<'_, PtySessionState>,
) -> Result<(), String> {
    state
        .trace_map
        .lock()
        .map_err(|e| e.to_string())?
        .remove(&trace_id);
    Ok(())
}

#[tauri::command]
fn kill_trace_pty(
    trace_id: String,
    app_handle: tauri::AppHandle,
    state: tauri::State<'_, PtySessionState>,
) -> Result<(), String> {
    let handler_id = state
        .trace_map
        .lock()
        .map_err(|e| e.to_string())?
        .get(&trace_id)
        .copied();

    if let Some(pid) = handler_id {
        app_handle
            .emit("pty-kill-request", serde_json::json!({ "handler_id": pid }))
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn kill_all_ptys(
    app_handle: tauri::AppHandle,
    state: tauri::State<'_, PtySessionState>,
) -> Result<(), String> {
    let ids: Vec<u32> = state
        .trace_map
        .lock()
        .map_err(|e| e.to_string())?
        .values()
        .copied()
        .collect();

    for pid in ids {
        app_handle
            .emit("pty-kill-request", serde_json::json!({ "handler_id": pid }))
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn list_trace_ptys(state: tauri::State<'_, PtySessionState>) -> Result<Vec<String>, String> {
    let keys: Vec<String> = state
        .trace_map
        .lock()
        .map_err(|e| e.to_string())?
        .keys()
        .cloned()
        .collect();
    Ok(keys)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_pty::init())
        .manage(PtySessionState {
            trace_map: Mutex::new(HashMap::new()),
        })
        .invoke_handler(tauri::generate_handler![
            register_trace_pty,
            unregister_trace_pty,
            kill_trace_pty,
            kill_all_ptys,
            list_trace_ptys,
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = &event {
                // Emit close-requested to the webview so JS can clean up PTYs
                let _ = window.emit("app-close-requested", ());
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
