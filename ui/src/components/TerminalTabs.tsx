import { useEffect, useState, useCallback } from "react";
import { ptyRegistry, pendingSpawns } from "../lib/ptyRegistry";
import { isTauri } from "../lib/runtime";
import TerminalPane from "./TerminalPane";

export default function TerminalTabs() {
  const [, force] = useState(0);
  const [active, setActive] = useState<string | null>(null);

  useEffect(() => ptyRegistry.subscribe(() => force(n => n + 1)), []);

  const registered = ptyRegistry.list();
  const pendingIds = Array.from(pendingSpawns.keys());
  const allTraceIds = [
    ...registered.map(t => t.trace_id),
    ...pendingIds.filter(id => !registered.some(r => r.trace_id === id)),
  ];

  if (allTraceIds.length === 0) return null;

  const current = active && allTraceIds.includes(active)
    ? active
    : allTraceIds[allTraceIds.length - 1] ?? null;

  const handleKillAll = useCallback(async () => {
    if (isTauri()) {
      try {
        const { invoke } = await import("@tauri-apps/api/core");
        await invoke("kill_all_ptys");
      } catch {}
    }
    setTimeout(() => ptyRegistry.killAll(), 100);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "`" && e.ctrlKey && !e.shiftKey && allTraceIds.length > 0) {
        e.preventDefault();
        const idx = allTraceIds.indexOf(current!);
        setActive(allTraceIds[(idx + 1) % allTraceIds.length]);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [allTraceIds, current]);

  return (
    <div style={{ display: "flex", height: "100%", background: "var(--bg-base)" }}>
      {/* All terminal panes stacked; visibility toggled so xterm stays mounted */}
      <div style={{ flex: 1, position: "relative", minWidth: 0 }}>
        {allTraceIds.map(tid => (
          <div
            key={tid}
            style={{
              position: "absolute",
              inset: 0,
              visibility: current === tid ? "visible" : "hidden",
              pointerEvents: current === tid ? "auto" : "none",
              zIndex: current === tid ? 1 : 0,
            }}
          >
            <TerminalPane trace_id={tid} />
          </div>
        ))}
      </div>

      <div style={{
        width: 200,
        minWidth: 200,
        borderLeft: "1px solid var(--border-subtle)",
        background: "var(--bg-elevated)",
        display: "flex",
        flexDirection: "column",
        overflowY: "auto",
        fontSize: 12,
      }}>
        <div style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "6px 10px",
          fontSize: 11,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.5px",
          color: "var(--text-muted)",
          borderBottom: "1px solid var(--border-subtle)",
        }}>
          <span>Terminals ({allTraceIds.length})</span>
          <button
            onClick={handleKillAll}
            title="Kill all terminals"
            style={{
              background: "none",
              border: "none",
              color: "var(--text-muted)",
              cursor: "pointer",
              fontSize: 12,
              padding: "2px 4px",
              borderRadius: 3,
              opacity: 0.6,
            }}
            onMouseEnter={e => (e.currentTarget.style.opacity = "1")}
            onMouseLeave={e => (e.currentTarget.style.opacity = "0.6")}
          >
            {'\u26A0'} Kill All
          </button>
        </div>

        {allTraceIds.map(tid => {
          const entry = ptyRegistry.get(tid);
          const isPending = pendingSpawns.has(tid);
          const isActive = current === tid;
          const isExited = entry?.exitCode !== null && entry?.exitCode !== undefined;
          const title = entry?.title ?? (isPending ? `spawning #${tid.slice(0, 8)}` : tid.slice(0, 8));

          return (
            <div
              key={tid}
              onClick={() => setActive(tid)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "6px 10px",
                cursor: "pointer",
                background: isActive ? "var(--bg-elevated-2)" : "transparent",
                borderLeft: isActive ? "2px solid var(--accent)" : "2px solid transparent",
                transition: "background 0.1s",
              }}
              title={title}
            >
              <span style={{
                fontSize: 8,
                color: isExited ? "var(--text-muted)"
                  : isPending ? "var(--text-muted)"
                  : "#76B900",
                flexShrink: 0,
              }}>
                {isExited ? '\u25CB' : isPending ? '\u25D4' : '\u25CF'}
              </span>

              <span style={{
                flex: 1,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                color: isExited ? "var(--text-muted)" : "var(--text-primary)",
              }}>
                {title.replace("[AIPC] ", "")}
              </span>

              {!isPending && (<>
                <span
                  onClick={(e) => {
                    e.stopPropagation();
                    ptyRegistry.remove(tid);
                    if (current === tid) setActive(allTraceIds.find(id => id !== tid) ?? null);
                  }}
                  style={{
                    flexShrink: 0,
                    width: 18,
                    height: 18,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    borderRadius: 3,
                    opacity: 0.5,
                    fontSize: 14,
                    lineHeight: 1,
                  }}
                  onMouseEnter={e => (e.currentTarget.style.opacity = "1")}
                  onMouseLeave={e => (e.currentTarget.style.opacity = "0.5")}
                >{'\u00D7'}</span>
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
