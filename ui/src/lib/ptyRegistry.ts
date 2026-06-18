import type { Terminal } from "xterm";
import type { PtySpec } from "./types";

interface PtyEntry {
  trace_id: string;
  title: string;
  term: Terminal;
  pty: any;
  handlerId?: number;
  exitCode: number | null;
  createdAt: number;
  taskId?: string;
  agentConfigId?: string;
  cwd?: string;
  /** If true, this is a shell PTY (not an opencode trace). Skip pty-exit on close. */
  isShell?: boolean;
}

class PtyRegistry {
  private map = new Map<string, PtyEntry>();
  private listeners = new Set<() => void>();

  set(entry: PtyEntry) {
    this.map.set(entry.trace_id, entry);
    this.emit();
  }

  get(trace_id: string): PtyEntry | undefined {
    return this.map.get(trace_id);
  }

  list(): PtyEntry[] {
    return Array.from(this.map.values()).sort((a, b) => a.createdAt - b.createdAt);
  }

  remove(trace_id: string) {
    const entry = this.map.get(trace_id);
    if (!entry) return;
    try { entry.pty?.kill?.(); } catch {}
    try { entry.term.dispose(); } catch {}
    this.map.delete(trace_id);
    this.emit();
  }

  killAll() {
    for (const entry of this.map.values()) {
      try { entry.pty?.kill?.(); } catch {}
      try { entry.term.dispose(); } catch {}
    }
    this.map.clear();
    this.emit();
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit() {
    for (const fn of this.listeners) fn();
  }
}

export const ptyRegistry = new PtyRegistry();

export const pendingSpawns = new Map<string, PtySpec>();
