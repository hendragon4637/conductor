export interface PtySpec {
  command: string;
  args: string[];
  cwd: string;
  env: Record<string, string>;
  title: string;
  isShell?: boolean;
}

export interface SpawnResponse {
  trace_id: string;
  cli_session_id: string;
  spawn_mode: "detached" | "embedded";
  pty_spec: PtySpec | null;
}
