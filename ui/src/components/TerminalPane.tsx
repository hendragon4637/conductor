import { useEffect, useRef } from "react";
import { Terminal } from "xterm";
import { FitAddon } from "xterm-addon-fit";
import "xterm/css/xterm.css";
import { ptyRegistry, pendingSpawns } from "../lib/ptyRegistry";
import { isTauri } from "../lib/runtime";
import type { PtySpec } from "../lib/types";

interface Props {
  trace_id: string;
  spec?: PtySpec;
  onExit?: (code: number) => void;
}

export default function TerminalPane({ trace_id, spec, onExit }: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const fitRef = useRef<FitAddon | null>(null);

  useEffect(() => {
    if (!hostRef.current) return;

    let entry = ptyRegistry.get(trace_id);

    const resolvedSpec = spec || pendingSpawns.get(trace_id);
    if (resolvedSpec && resolvedSpec !== spec) {
      pendingSpawns.delete(trace_id);
    }

    if (!entry && resolvedSpec) {
      const term = new Terminal({
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        fontSize: 13,
        scrollback: 5000,
        theme: {
          background: "#0A0A0A",
          foreground: "#F5F5F5",
          cursor: "#76B900",
          selectionBackground: "rgba(118,185,0,0.25)",
        },
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(hostRef.current);
      fit.fit();
      fitRef.current = fit;
      term.focus();

      let pty: any = null;
      (async () => {
        const { spawn } = await import("tauri-pty");
        pty = spawn(resolvedSpec.command, resolvedSpec.args, {
          cols: term.cols,
          rows: term.rows,
          cwd: resolvedSpec.cwd,
          env: resolvedSpec.env,
        });
        pty.onData((d: string) => term.write(d));
        term.onData((d: string) => pty.write(d));
        pty.onExit((e: { exitCode: number }) => {
          term.writeln(`\r\n\x1b[33m[trace ${trace_id.slice(0,8)} exited code ${e.exitCode}]\x1b[0m`);
          const live = ptyRegistry.get(trace_id);
          if (live) live.exitCode = e.exitCode;
          onExit?.(e.exitCode);
          // Only post pty-exit for real traces, not shell terminals
          if (!live?.isShell) {
            fetch(`/api/traces/${trace_id}/pty-exit`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ exit_code: e.exitCode }),
            }).catch(() => {});
          }
        });

        // Register the PTY handler ID with Tauri for lifecycle management
        const handlerId = pty.pid;
        if (isTauri() && handlerId !== undefined) {
          try {
            const { invoke } = await import("@tauri-apps/api/core");
            await invoke("register_trace_pty", { traceId: trace_id, handlerId });
          } catch {}
        }

        ptyRegistry.set({
          trace_id,
          title: resolvedSpec.title,
          term,
          pty,
          handlerId,
          exitCode: null,
          createdAt: Date.now(),
          taskId: resolvedSpec.env?.["AIPC_TASK_ID"],
          agentConfigId: resolvedSpec.env?.["AIPC_AGENT_CONFIG_ID"],
          cwd: resolvedSpec.cwd,
          isShell: resolvedSpec.isShell,
        });
      })();
    } else if (entry) {
      entry.term.open(hostRef.current);
      fitRef.current = new FitAddon();
      entry.term.loadAddon(fitRef.current);
      fitRef.current.fit();
      entry.term.focus();
    }

    const onResize = () => {
      try { fitRef.current?.fit(); } catch {}
      const e = ptyRegistry.get(trace_id);
      if (e?.pty?.resize) {
        e.pty.resize(e.term.cols, e.term.rows);
      }
    };
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
    };
  }, [trace_id, spec]);

  return (
    <div
      ref={hostRef}
      style={{
        width: "100%",
        height: "100%",
        minHeight: "300px",
        background: "var(--bg-base)",
      }}
    />
  );
}
