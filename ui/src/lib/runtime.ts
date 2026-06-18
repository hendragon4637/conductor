// Detect whether we are running inside the Tauri webview.
// Tauri v2 injects window.__TAURI_INTERNALS__; browser builds do not.
export function isTauri(): boolean {
  return typeof window !== "undefined"
    && typeof (window as any).__TAURI_INTERNALS__ !== "undefined";
}
