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
  entity_id?: number;
  candidate_id?: number;
  candidate_status?: string;
  duplicate_count?: number;
  history_only?: boolean;
  display_name?: string;
  alias?: string | null;
  region?: string;
  cp?: string;
  entity_type?: EntityType | null;
  src_region?: string | null;
  dst_region?: string | null;
  enabled?: boolean;
  remark?: string;
};

export type EntityPayload = {
  edc_name: string;
  sn: string;
  display_name: string;
  alias: string;
  region: string;
  cp: string;
  entity_type: EntityType;
  src_region: string;
  dst_region: string;
  is_backup: boolean;
  enabled: boolean;
  remark: string;
};

export type ConfiguredEntity = {
  id: number;
  edc_name: string;
  sn: string;
  display_name: string;
  alias?: string | null;
  region: string;
  cp: string;
  entity_type?: EntityType | null;
  src_region?: string | null;
  dst_region?: string | null;
  is_backup: boolean;
  enabled: boolean;
  remark: string;
  created_at?: string;
  updated_at?: string;
};

export type EntityType = "node" | "transmission";

export type Execution = {
  id: number;
  status: string;
  data_start_time: string;
  data_end_time: string;
  rows_read: number;
  rows_written: number;
  unmapped_count: number;
  negative_service_count?: number;
  negative_cache_count?: number;
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
  negative_service_count?: number;
  negative_cache_count?: number;
  duration_ms?: number;
  error?: string;
};

export type ExecutionProgress = {
  kind?: string;
  total_chunks?: number;
  completed_chunks?: number;
  total_entities?: number;
  completed_entities?: number;
  rows_scanned?: number;
  rows_updated?: number;
  entity_id?: number;
  percent?: number;
  current_start_time?: string;
  current_end_time?: string;
  rows_read?: number;
  rows_written?: number;
  unmapped_count?: number;
  negative_service_count?: number;
  negative_cache_count?: number;
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

export type OnboardingCandidate = {
  id: number;
  edc_name: string;
  sn: string;
  status: string;
  enabled?: boolean;
  first_seen_at: string | null;
  latest_seen_at: string | null;
  backfill_error: string | null;
  backfill_rows: number;
};

export type OnboardingState = {
  items: OnboardingCandidate[];
  counts: Record<string, number>;
};

async function request<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const requestInit = {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init
  };
  let response: Response;
  try {
    response = await fetch(input, requestInit);
  } catch (error) {
    if (!shouldRetryFetch(input, init)) throw error;
    await wait(800);
    try {
      response = await fetch(input, requestInit);
    } catch (retryError) {
      throw retryError instanceof Error ? new Error(`请求连接失败，请稍后重试：${retryError.message}`) : retryError;
    }
  }
  const text = await response.text();
  const contentType = response.headers.get("content-type") || "";
  const isJson = contentType.includes("application/json");
  const body = text && isJson ? JSON.parse(text) : {};
  if (!response.ok) {
    const fallback = text && !isJson ? "服务器返回了非 JSON 响应" : response.statusText;
    throw new Error(body.error || fallback || "请求失败");
  }
  if (text && !isJson) {
    throw new Error("服务器返回了非 JSON 响应");
  }
  return body as T;
}

function shouldRetryFetch(input: RequestInfo | URL, init?: RequestInit) {
  const method = (init?.method || "GET").toUpperCase();
  const url = String(input);
  return method === "GET" || (method === "POST" && url === "/api/entities");
}

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
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
  return request<{
    upserted: number;
    backfill_status?: string;
    metadata_sync_status?: string;
    metadata_sync_execution_id?: number | null;
  }>("/api/entities", {
    method: "POST",
    body: JSON.stringify({ items })
  });
}

export function getConfiguredEntities() {
  return request<{ items: ConfiguredEntity[] }>("/api/entities/configured");
}

export function setEntityEnabled(entityId: number, enabled: boolean) {
  return request<{ item: ConfiguredEntity }>(`/api/entities/${entityId}/enabled`, {
    method: "PATCH",
    body: JSON.stringify({ enabled })
  });
}

export function setEntityCandidateEnabled(candidateId: number, enabled: boolean) {
  return request<{ item: OnboardingCandidate }>(`/api/entity-candidates/${candidateId}/enabled`, {
    method: "PATCH",
    body: JSON.stringify({ enabled })
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

export function getOnboarding() {
  return request<OnboardingState>("/api/onboarding");
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
