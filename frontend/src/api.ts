export type Health = {
  status: string;
  recommended_source_index: boolean;
  source?: DatabaseHealth & {
    recommended_index?: boolean;
    index_error?: string | null;
  };
  target?: DatabaseHealth;
};

export type DatabaseHealth = {
  role: string;
  connected: boolean;
  database: string;
  error: string | null;
};

export type SourceEntity = {
  edc_name: string;
  sn: string;
  latest_create_time: string;
  record_count: number;
  is_backup: boolean;
  configured: boolean;
};

export type EntityPayload = {
  edc_name: string;
  sn: string;
  display_name: string;
  region: string;
  cp: string;
  is_backup: boolean;
  enabled: boolean;
  remark: string;
};

export type Execution = {
  id: number;
  status: string;
  data_start_time: string;
  data_end_time: string;
  rows_read: number;
  rows_written: number;
  unmapped_count: number;
  duration_ms: number;
  error_message: string | null;
  progress_info: string | null;
  created_at: string;
};

export type SyncResult = {
  execution_id: number;
  status?: string;
  rows_read?: number;
  rows_written?: number;
  unmapped_count?: number;
  duration_ms?: number;
  error?: string;
};

export type ExecutionProgress = {
  total_chunks?: number;
  completed_chunks?: number;
  percent?: number;
  current_start_time?: string;
  current_end_time?: string;
  rows_read?: number;
  rows_written?: number;
  unmapped_count?: number;
  duration_ms?: number;
};

export type ScheduledTask = {
  id: number;
  name: string;
  cron_expression: string;
  time_window_minutes: number;
  delay_minutes: number;
  enabled: boolean;
  next_run_time: string | null;
  updated_at: string;
};

async function request<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(body.error || response.statusText || "请求失败");
  }
  return body as T;
}

export function getHealth() {
  return request<Health>("/health");
}

export function getExecutions() {
  return request<{ items: Execution[] }>("/api/executions");
}

export function getExecution(id: number) {
  return request<Execution>(`/api/executions/${id}`);
}

export function discoverEntities(params: {
  startTime: string;
  endTime: string;
  limit: number;
  onlyUnconfigured: boolean;
}) {
  const search = new URLSearchParams({
    start_time: params.startTime,
    end_time: params.endTime,
    limit: String(params.limit),
    only_unconfigured: params.onlyUnconfigured ? "true" : "false"
  });
  return request<{ items: SourceEntity[] }>(`/api/source/entities?${search}`);
}

export function saveEntity(item: EntityPayload) {
  return saveEntities([item]);
}

export function saveEntities(items: EntityPayload[]) {
  return request<{ upserted: number }>("/api/entities", {
    method: "POST",
    body: JSON.stringify({ items })
  });
}

export function runSync(startTime: string, endTime: string) {
  return request<SyncResult>("/api/sync", {
    method: "POST",
    body: JSON.stringify({ start_time: startTime, end_time: endTime })
  });
}

export function getTasks() {
  return request<{ items: ScheduledTask[] }>("/api/tasks");
}

export function updateTask(id: number, payload: Pick<ScheduledTask, "cron_expression" | "time_window_minutes" | "delay_minutes" | "enabled">) {
  return request<ScheduledTask>(`/api/tasks/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload)
  });
}

export function runTask(id: number) {
  return request<SyncResult>(`/api/tasks/${id}/run`, {
    method: "POST",
    body: JSON.stringify({})
  });
}
