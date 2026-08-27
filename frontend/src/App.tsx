import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  CalendarDays,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Database,
  FileClock,
  Filter,
  Loader2,
  Play,
  Power,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  SlidersHorizontal
} from "lucide-react";
import {
  discoverEntities,
  ConfiguredEntity,
  EntityPayload,
  EntityType,
  Execution,
  ExecutionProgress,
  getTasks,
  getExecutions,
  getHealth,
  getConfiguredEntities,
  getOnboarding,
  Health,
  OnboardingState,
  runTask,
  runSync,
  saveEntity,
  saveEntities,
  setEntityEnabled,
  ScheduledTask,
  SourceEntity,
  SyncResult,
  updateTask
} from "./api";

type Notice = { tone: "ok" | "error" | "info"; text: string } | null;
type ViewKey = "状态" | "EDC发现" | "映射录入" | "实体状态" | "同步任务" | "执行记录";
type RangeSelectMode = "start" | "end";
type SseState = "connecting" | "connected" | "fallback";
type DiscoverySortKey = "edc_name" | "sn" | "latest_create_time" | "record_count" | "configured" | "enabled";
type DiscoveryStatusFilter = "all" | "enabled" | "disabled";

type SseLease = {
  owner: string;
  expiresAt: number;
};

const SSE_LEASE_KEY = "edc-extractor:sse-owner";
const SSE_LEASE_TTL_MS = 20000;
const SSE_LEASE_RENEW_MS = 10000;

type SseSnapshot = {
  tasks?: ScheduledTask[];
  executions?: Execution[];
  active_execution?: Execution | null;
  health?: Health;
  onboarding?: OnboardingState;
};

const navItems: Array<{ label: ViewKey; icon: typeof Activity }> = [
  { label: "状态", icon: Activity },
  { label: "EDC发现", icon: Search },
  { label: "映射录入", icon: ShieldCheck },
  { label: "实体状态", icon: Power },
  { label: "同步任务", icon: Play },
  { label: "执行记录", icon: FileClock }
];

function toInputValue(date: Date) {
  const offsetMs = date.getTimezoneOffset() * 60 * 1000;
  return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16);
}

function toApiTime(value: string) {
  return value.replace("T", " ") + ":00";
}

function defaultRange() {
  const end = new Date();
  end.setMinutes(Math.floor(end.getMinutes() / 5) * 5, 0, 0);
  const start = new Date(end.getTime() - 60 * 60 * 1000);
  return { start: toInputValue(start), end: toInputValue(end) };
}

function toDateValue(value: string) {
  return value.slice(0, 10);
}

function parseDateValue(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function formatDateValue(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatMonthTitle(date: Date) {
  return `${date.getFullYear()}年${date.getMonth() + 1}月`;
}

function addMonths(date: Date, amount: number) {
  return new Date(date.getFullYear(), date.getMonth() + amount, 1);
}

function addDays(date: Date, amount: number) {
  const next = new Date(date);
  next.setDate(next.getDate() + amount);
  return next;
}

function buildCalendarMonth(monthDate: Date) {
  const monthStart = new Date(monthDate.getFullYear(), monthDate.getMonth(), 1);
  const gridStart = addDays(monthStart, -monthStart.getDay());
  return Array.from({ length: 42 }, (_, index) => {
    const date = addDays(gridStart, index);
    return {
      date,
      value: formatDateValue(date),
      inMonth: date.getMonth() === monthStart.getMonth()
    };
  });
}

function quickDateRanges() {
  const today = parseDateValue(formatDateValue(new Date()));
  const yesterday = addDays(today, -1);
  const monthStart = new Date(today.getFullYear(), today.getMonth(), 1);
  const previousMonthStart = new Date(today.getFullYear(), today.getMonth() - 1, 1);
  const previousMonthEnd = addDays(monthStart, -1);
  return [
    { label: "今天", startDate: formatDateValue(today), endDate: formatDateValue(today) },
    { label: "昨天", startDate: formatDateValue(yesterday), endDate: formatDateValue(yesterday) },
    { label: "最近7天", startDate: formatDateValue(addDays(today, -6)), endDate: formatDateValue(today) },
    { label: "本月", startDate: formatDateValue(monthStart), endDate: formatDateValue(today) },
    { label: "上月", startDate: formatDateValue(previousMonthStart), endDate: formatDateValue(previousMonthEnd) }
  ];
}

function defaultDateRange() {
  const range = defaultRange();
  return { startDate: toDateValue(range.start), endDate: toDateValue(range.end) };
}

function fullDayStart(date: string) {
  return `${date} 00:00:00`;
}

function fullDayEnd(date: string) {
  return `${date} 23:59:59`;
}

function mappingForm(row: SourceEntity): EntityPayload | null {
  const entityType: EntityType = row.entity_type === "transmission" ? "transmission" : "node";
  if (row.configured && row.display_name !== undefined && row.region !== undefined && row.cp !== undefined) {
    return {
      edc_name: row.edc_name,
      sn: row.sn,
      display_name: row.display_name,
      alias: row.alias || "",
      region: row.region,
      cp: row.cp,
      entity_type: entityType,
      src_region: row.src_region || "",
      dst_region: row.dst_region || "",
      is_backup: row.is_backup,
      enabled: row.enabled ?? true,
      remark: row.remark || ""
    };
  }
  if (row.configured) return null;
  const displayName = row.edc_name.replace(/-backup/gi, "");
  const parts = displayName.split("-");
  return {
    edc_name: row.edc_name,
    sn: row.sn,
    display_name: displayName,
    alias: row.alias || "",
    region: parts[0] || "",
    cp: parts[1] || "",
    entity_type: entityType,
    src_region: row.src_region || "",
    dst_region: row.dst_region || "",
    is_backup: row.is_backup,
    enabled: true,
    remark: row.is_backup ? "备份数据源，暂不自动补录" : ""
  };
}

function connectionLabel(connected?: boolean) {
  if (connected === undefined) return "未知";
  return connected ? "已连接" : "异常";
}

function sourceIndexLabel(health: Health | null) {
  if (!health?.source?.connected) return "源库异常";
  return health.recommended_source_index ? "已就绪" : "待检查";
}

function formatNextRun(value: string) {
  return value.replace("T", " ").slice(0, 19);
}

function compareDiscoveryEntities(left: SourceEntity, right: SourceEntity, key: DiscoverySortKey) {
  if (key === "record_count") {
    return left.record_count - right.record_count;
  }
  if (key === "configured" || key === "enabled") {
    const leftValue = key === "configured" ? Number(left.configured) : Number(left.enabled ?? false);
    const rightValue = key === "configured" ? Number(right.configured) : Number(right.enabled ?? false);
    return leftValue - rightValue;
  }
  const leftValue = String(left[key] ?? "").toLocaleLowerCase();
  const rightValue = String(right[key] ?? "").toLocaleLowerCase();
  return leftValue.localeCompare(rightValue, "zh-CN", { numeric: true, sensitivity: "base" });
}

export function App() {
  const initialRange = useMemo(defaultRange, []);
  const [health, setHealth] = useState<Health | null>(null);
  const [onboarding, setOnboarding] = useState<OnboardingState>({ items: [], counts: {} });
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [entities, setEntities] = useState<SourceEntity[]>([]);
  const [configuredEntities, setConfiguredEntities] = useState<ConfiguredEntity[]>([]);
  const [entityStatusFilter, setEntityStatusFilter] = useState<"all" | "enabled" | "disabled">("all");
  const [entityStatusSearch, setEntityStatusSearch] = useState("");
  const [statusPendingIds, setStatusPendingIds] = useState<Set<number>>(() => new Set());
  const [discoverySearch, setDiscoverySearch] = useState("");
  const [discoveryStatusFilter, setDiscoveryStatusFilter] = useState<DiscoveryStatusFilter>("all");
  const [discoverySort, setDiscoverySort] = useState<{ key: DiscoverySortKey; direction: "asc" | "desc" }>({
    key: "latest_create_time",
    direction: "desc"
  });
  const [duplicateNameFilter, setDuplicateNameFilter] = useState<string | null>(null);
  const [selected, setSelected] = useState<SourceEntity | null>(null);
  const [form, setForm] = useState<EntityPayload | null>(null);
  const [range, setRange] = useState(initialRange);
  const [syncDateRange, setSyncDateRange] = useState(defaultDateRange);
  const [syncDraftRange, setSyncDraftRange] = useState(defaultDateRange);
  const [syncCalendarMonth, setSyncCalendarMonth] = useState(() => parseDateValue(defaultDateRange().startDate));
  const [syncRangeMode, setSyncRangeMode] = useState<RangeSelectMode>("start");
  const [syncPickerOpen, setSyncPickerOpen] = useState(false);
  const [limit, setLimit] = useState(200);
  const [onlyUnconfigured, setOnlyUnconfigured] = useState(true);
  const [loading, setLoading] = useState({ health: false, entities: false, save: false, sync: false, configured: false });
  const [notice, setNotice] = useState<Notice>(null);
  const [lastSync, setLastSync] = useState<SyncResult | null>(null);
  const [activeExecutionId, setActiveExecutionId] = useState<number | null>(null);
  const [activeExecution, setActiveExecution] = useState<Execution | null>(null);
  const [activeView, setActiveView] = useState<ViewKey>("EDC发现");
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(() => new Set());
  const [taskState, setTaskState] = useState<ScheduledTask | null>(null);
  const [taskDraft, setTaskDraft] = useState<ScheduledTask | null>(null);
  const [taskDirty, setTaskDirty] = useState(false);
  const [sseState, setSseState] = useState<SseState>("connecting");
  const trackedExecutionIdRef = useRef<number | null>(null);

  const loadHealth = useCallback(async () => {
    setLoading((current) => ({ ...current, health: true }));
    try {
      setHealth(await getHealth());
    } catch (error) {
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "健康检查失败" });
    } finally {
      setLoading((current) => ({ ...current, health: false }));
    }
  }, []);

  const loadExecutions = useCallback(async () => {
    try {
      const response = await getExecutions();
      setExecutions(response.items);
    } catch (error) {
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "执行记录加载失败" });
    }
  }, []);

  const loadTasks = useCallback(async () => {
    try {
      const response = await getTasks();
      const nextTask = response.items[0] || null;
      setTaskState(nextTask);
      setTaskDraft((current) => (taskDirty ? current : nextTask));
    } catch (error) {
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "自动同步配置加载失败" });
    }
  }, [taskDirty]);

  const loadConfiguredEntities = useCallback(async () => {
    setLoading((current) => ({ ...current, configured: true }));
    try {
      const response = await getConfiguredEntities();
      setConfiguredEntities(response.items);
    } catch (error) {
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "实体状态加载失败" });
    } finally {
      setLoading((current) => ({ ...current, configured: false }));
    }
  }, []);

  const refreshOperationalState = useCallback(async () => {
    await Promise.all([
      loadHealth(),
      loadExecutions(),
      loadTasks(),
      loadConfiguredEntities(),
      getOnboarding().then(setOnboarding).catch(() => undefined)
    ]);
  }, [loadConfiguredEntities, loadExecutions, loadHealth, loadTasks]);

  const applyTasks = useCallback((items: ScheduledTask[]) => {
    const nextTask = items[0] || null;
    setTaskState(nextTask);
    setTaskDraft((current) => (taskDirty ? current : nextTask));
  }, [taskDirty]);

  const applyExecutions = useCallback((items: Execution[]) => {
    setExecutions(items);
    const trackedId = trackedExecutionIdRef.current;
    if (!trackedId) return;
    const tracked = items.find((item) => item.id === trackedId);
    if (!tracked || tracked.status === "running") return;
    setLastSync({
      execution_id: tracked.id,
      rows_read: tracked.rows_read,
      rows_written: tracked.rows_written,
      unmapped_count: tracked.unmapped_count,
      negative_service_count: tracked.negative_service_count,
      negative_cache_count: tracked.negative_cache_count,
      duration_ms: tracked.duration_ms
    });
    setActiveExecutionId(null);
    setActiveExecution(null);
    setLoading((current) => ({ ...current, sync: false }));
    if (tracked.status === "failed") {
      setNotice({ tone: "error", text: tracked.error_message || `同步任务 ${tracked.id} 失败` });
    }
  }, []);

  const applySnapshot = useCallback((payload: SseSnapshot) => {
    if (payload.health) {
      setHealth(payload.health);
    }
    if (payload.onboarding) {
      setOnboarding(payload.onboarding);
    }
    if (payload.tasks) {
      applyTasks(payload.tasks);
    }
    if (payload.executions) {
      applyExecutions(payload.executions);
    }
    if ("active_execution" in payload) {
      setActiveExecution(payload.active_execution || null);
      if (payload.active_execution) {
        setActiveExecutionId(payload.active_execution.id);
        setLoading((current) => ({ ...current, sync: true }));
      } else {
        setActiveExecutionId(null);
        setLoading((current) => ({ ...current, sync: false }));
      }
    }
  }, [applyExecutions, applyTasks]);

  const loadEntities = useCallback(async (onlyUnconfiguredOverride = onlyUnconfigured) => {
    setLoading((current) => ({ ...current, entities: true }));
    try {
      const response = await discoverEntities({
        startTime: toApiTime(range.start),
        endTime: toApiTime(range.end),
        limit,
        onlyUnconfigured: onlyUnconfiguredOverride
      });
      setEntities(response.items);
      const nextSelected = response.items[0] || null;
      setSelected(nextSelected);
      setForm(nextSelected ? mappingForm(nextSelected) : null);
      setSelectedKeys(new Set());
      const unconfiguredCount = response.items.filter((item) => !item.configured).length;
      setNotice({
        tone: "ok",
        text:
          response.items.length === 0 && onlyUnconfiguredOverride
            ? "未发现新的未配置 EDC，取消勾选“仅未配置”可查看全部已配置项"
            : onlyUnconfiguredOverride
              ? `已发现 ${response.items.length} 条未配置 EDC 名称`
              : `已发现 ${response.items.length} 条 EDC 名称，其中 ${unconfiguredCount} 条未配置`,
      });
    } catch (error) {
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "EDC 发现失败" });
    } finally {
      setLoading((current) => ({ ...current, entities: false }));
    }
  }, [limit, onlyUnconfigured, range.end, range.start]);

  useEffect(() => {
    trackedExecutionIdRef.current = activeExecutionId || lastSync?.execution_id || null;
  }, [activeExecutionId, lastSync?.execution_id]);

  useEffect(() => {
    void refreshOperationalState();
  }, [refreshOperationalState]);

  useEffect(() => {
    if (sseState === "connected") return;
    const refresh = () => {
      if (document.visibilityState === "hidden") return;
      void Promise.all([loadExecutions(), loadTasks()]);
    };
    const timer = window.setInterval(refresh, 15000);
    return () => window.clearInterval(timer);
  }, [loadExecutions, loadTasks, sseState]);

  useEffect(() => {
    if (sseState === "connected") return;
    const refresh = () => {
      if (document.visibilityState === "hidden") return;
      void loadHealth();
    };
    const timer = window.setInterval(refresh, 30000);
    return () => window.clearInterval(timer);
  }, [loadHealth, sseState]);

  useEffect(() => {
    const shouldUseSse = activeView === "同步任务" || activeView === "执行记录";
    let source: EventSource | null = null;
    let reconnectTimer = 0;
    let leaseTimer = 0;
    let closed = false;
    const tabId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;

    const readLease = (): SseLease | null => {
      try {
        const raw = window.localStorage.getItem(SSE_LEASE_KEY);
        return raw ? (JSON.parse(raw) as SseLease) : null;
      } catch {
        return null;
      }
    };

    const writeLease = () => {
      try {
        window.localStorage.setItem(
          SSE_LEASE_KEY,
          JSON.stringify({ owner: tabId, expiresAt: Date.now() + SSE_LEASE_TTL_MS }),
        );
      } catch {
        return;
      }
    };

    const acquireLease = () => {
      const lease = readLease();
      if (lease && lease.owner !== tabId && lease.expiresAt > Date.now()) {
        return false;
      }
      writeLease();
      return true;
    };

    const releaseLease = () => {
      const lease = readLease();
      if (!lease || lease.owner !== tabId) return;
      try {
        window.localStorage.removeItem(SSE_LEASE_KEY);
      } catch {
        return;
      }
    };

    const closeSource = () => {
      source?.close();
      source = null;
      window.clearInterval(leaseTimer);
      releaseLease();
    };

    const connect = () => {
      if (closed || !shouldUseSse || source || document.visibilityState === "hidden") return;
      if (!acquireLease()) {
        setSseState("fallback");
        window.clearTimeout(reconnectTimer);
        reconnectTimer = window.setTimeout(connect, 5000);
        return;
      }
      setSseState((current) => (current === "connected" ? current : "connecting"));
      source = new EventSource("/api/events?stream=true");
      source.onopen = () => {
        writeLease();
        window.clearInterval(leaseTimer);
        leaseTimer = window.setInterval(writeLease, SSE_LEASE_RENEW_MS);
        setSseState("connected");
      };
      source.onerror = () => {
        setSseState("fallback");
        closeSource();
        if (!closed) {
          window.clearTimeout(reconnectTimer);
          reconnectTimer = window.setTimeout(connect, 5000);
        }
      };
      source.addEventListener("snapshot", (event) => applySnapshot(parseEventData(event)));
      source.addEventListener("tasks", (event) => applyTasks(parseEventData<{ items: ScheduledTask[] }>(event).items || []));
      source.addEventListener("executions", (event) => applyExecutions(parseEventData<{ items: Execution[] }>(event).items || []));
      source.addEventListener("onboarding", (event) => setOnboarding(parseEventData<OnboardingState>(event)));
      source.addEventListener("active_execution", (event) => applySnapshot({ active_execution: parseEventData<Execution | null>(event) }));
      source.addEventListener("health", (event) => setHealth(parseEventData<Health>(event)));
      source.addEventListener("error", (event) => {
        const payload = parseEventData<{ message?: string }>(event);
        setNotice({ tone: "error", text: payload.message || "实时状态刷新失败" });
      });
    };

    const handleVisibility = () => {
      if (document.visibilityState === "hidden") {
        closeSource();
        setSseState("fallback");
        return;
      }
      connect();
      void refreshOperationalState();
    };

    const handleStorage = (event: StorageEvent) => {
      if (event.key !== SSE_LEASE_KEY || closed || source || document.visibilityState === "hidden") return;
      connect();
    };

    if (shouldUseSse) {
      connect();
    } else {
      setSseState("fallback");
    }
    document.addEventListener("visibilitychange", handleVisibility);
    window.addEventListener("storage", handleStorage);
    return () => {
      closed = true;
      window.clearTimeout(reconnectTimer);
      window.clearInterval(leaseTimer);
      document.removeEventListener("visibilitychange", handleVisibility);
      window.removeEventListener("storage", handleStorage);
      closeSource();
    };
  }, [activeView, applySnapshot, applyTasks, refreshOperationalState]);

  useEffect(() => {
    if (activeExecutionId || sseState === "connected") return;
    const running = executions.find((item) => item.status === "running");
    if (running) {
      setActiveExecution(running);
      return;
    }
    setActiveExecution((current) => (current?.status === "running" ? null : current));
  }, [activeExecutionId, executions, sseState]);

  const configuredCount = entities.filter((item) => item.configured).length;
  const backupCount = entities.filter((item) => item.is_backup).length;
  const unconfiguredCount = entities.length - configuredCount;
  const filteredEntities = useMemo(() => {
    const keyword = discoverySearch.trim().toLocaleLowerCase();
    const rows = entities.filter((item) => {
      if (discoveryStatusFilter === "enabled" && (!item.configured || !item.enabled)) return false;
      if (discoveryStatusFilter === "disabled" && (!item.configured || item.enabled !== false)) return false;
      if (duplicateNameFilter && item.edc_name !== duplicateNameFilter) return false;
      if (!keyword) return true;
      return [item.edc_name, item.sn, item.display_name || "", item.alias || ""]
        .some((value) => value.toLocaleLowerCase().includes(keyword));
    });
    return rows
      .map((item, index) => ({ item, index }))
      .sort((left, right) => {
        const result = compareDiscoveryEntities(left.item, right.item, discoverySort.key);
        if (result !== 0) return discoverySort.direction === "asc" ? result : -result;
        return left.index - right.index;
      })
      .map(({ item }) => item);
  }, [discoverySearch, discoverySort, discoveryStatusFilter, duplicateNameFilter, entities]);
  const selectableEntities = filteredEntities.filter((item) => !item.configured);
  const selectedEntities = entities.filter((item) => selectedKeys.has(entityKey(item)));
  const allSelectableChecked =
    selectableEntities.length > 0 && selectableEntities.every((item) => selectedKeys.has(entityKey(item)));

  function selectEntity(row: SourceEntity) {
    setSelected(row);
    setForm(mappingForm(row));
    setNotice(null);
  }

  function toggleDiscoverySort(key: DiscoverySortKey) {
    setDiscoverySort((current) =>
      current.key === key
        ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
        : { key, direction: key === "record_count" || key === "latest_create_time" ? "desc" : "asc" },
    );
  }

  function discoverySortLabel(key: DiscoverySortKey) {
    if (discoverySort.key !== key) return "";
    return discoverySort.direction === "asc" ? " ↑" : " ↓";
  }

  async function filterDuplicateName(name: string) {
    const nextFilter = duplicateNameFilter === name ? null : name;
    setDuplicateNameFilter(nextFilter);
    if (nextFilter && onlyUnconfigured) {
      setOnlyUnconfigured(false);
      await loadEntities(false);
    }
  }

  function toggleEntity(row: SourceEntity) {
    if (row.configured) return;
    setSelectedKeys((current) => {
      const next = new Set(current);
      const key = entityKey(row);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }

  function toggleAllSelectable() {
    setSelectedKeys((current) => {
      if (allSelectableChecked) {
        return new Set();
      }
      const next = new Set(current);
      selectableEntities.forEach((item) => next.add(entityKey(item)));
      return next;
    });
  }

  async function handleSave() {
    if (!form) return;
    setLoading((current) => ({ ...current, save: true }));
    try {
      const response = await saveEntity(form);
      setNotice({
        tone: "ok",
        text: response.metadata_sync_execution_id
          ? `已写入映射：${form.edc_name}，历史元数据同步任务 #${response.metadata_sync_execution_id} 已排队`
          : response.backfill_status === "scheduled"
            ? `已写入映射：${form.edc_name}，历史补录已后台排队`
            : `已写入映射：${form.edc_name}`
      });
      await loadEntities();
    } catch (error) {
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "映射写入失败" });
    } finally {
      setLoading((current) => ({ ...current, save: false }));
    }
  }

  async function handleBulkSave() {
    if (!selectedEntities.length) {
      setNotice({ tone: "info", text: "请先勾选需要批量写入的未配置 EDC 名称" });
      return;
    }
    setLoading((current) => ({ ...current, save: true }));
    try {
      const payload = selectedEntities.map((item) => mappingForm(item)).filter((item): item is EntityPayload => item !== null);
      const response = await saveEntities(payload);
      setNotice({
        tone: "ok",
        text: response.metadata_sync_execution_id
          ? `已批量写入 ${response.upserted} 条映射，历史元数据同步任务 #${response.metadata_sync_execution_id} 已排队`
          : response.backfill_status === "scheduled"
            ? `已批量写入 ${response.upserted} 条映射，历史补录已后台排队`
            : `已批量写入 ${response.upserted} 条映射`
      });
      setSelectedKeys(new Set());
      await loadEntities();
    } catch (error) {
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "批量写入失败" });
    } finally {
      setLoading((current) => ({ ...current, save: false }));
    }
  }

  const filteredConfiguredEntities = useMemo(() => {
    const keyword = entityStatusSearch.trim().toLowerCase();
    return configuredEntities.filter((item) => {
      if (entityStatusFilter === "enabled" && !item.enabled) return false;
      if (entityStatusFilter === "disabled" && item.enabled) return false;
      if (!keyword) return true;
      return [item.edc_name, item.sn, item.display_name, item.alias || ""]
        .some((value) => value.toLowerCase().includes(keyword));
    });
  }, [configuredEntities, entityStatusFilter, entityStatusSearch]);

  async function handleEntityStatusToggle(entity: ConfiguredEntity | SourceEntity) {
    const entityId = "id" in entity ? entity.id : entity.entity_id;
    if (entityId === undefined) return;
    const nextEnabled = !(entity.enabled ?? false);
    const action = nextEnabled ? "启用" : "禁用";
    const impact = nextEnabled
      ? "恢复该实体的同步，并重新纳入结算平台有效查询范围。"
      : "停止该实体的同步，并从结算平台有效查询范围隐藏；历史事实不会删除。";
    if (!window.confirm(`确认${action} ${entity.edc_name}（entity_id=${entityId}，SN=${entity.sn || "-"}）？\n${impact}`)) return;
    setStatusPendingIds((current) => new Set(current).add(entityId));
    try {
      const response = await setEntityEnabled(entityId, nextEnabled);
      setConfiguredEntities((current) =>
        current.map((item) => item.id === entityId ? response.item : item),
      );
      setEntities((current) => current.map((item) =>
        item.entity_id === entityId
          ? { ...item, ...response.item, entity_id: entityId, configured: true }
          : item,
      ));
      setSelected((current) => {
        if (!current || current.entity_id !== entityId) return current;
        const updated = { ...current, ...response.item, entity_id: entityId, configured: true };
        setForm(mappingForm(updated));
        return updated;
      });
      setNotice({ tone: "ok", text: `${entity.edc_name}（${entity.sn || "无SN"}）已${action}` });
    } catch (error) {
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "实体状态更新失败" });
    } finally {
      setStatusPendingIds((current) => {
        const next = new Set(current);
        next.delete(entityId);
        return next;
      });
    }
  }

  async function handleSync() {
      setLoading((current) => ({ ...current, sync: true }));
    try {
      const response = await runSync(fullDayStart(syncDateRange.startDate), fullDayEnd(syncDateRange.endDate));
      setLastSync(response);
      setActiveExecutionId(response.execution_id);
      setActiveExecution({
        id: response.execution_id,
        status: response.status || "running",
        data_start_time: fullDayStart(syncDateRange.startDate),
        data_end_time: fullDayEnd(syncDateRange.endDate),
        rows_read: 0,
        rows_written: 0,
        unmapped_count: 0,
        negative_service_count: 0,
        negative_cache_count: 0,
        duration_ms: 0,
        error_message: null,
        progress_info: null,
        created_at: new Date().toISOString()
      });
      setNotice({ tone: "info", text: `同步任务 ${response.execution_id} 已启动` });
      await loadExecutions();
    } catch (error) {
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "同步任务失败" });
      setLoading((current) => ({ ...current, sync: false }));
      await loadExecutions();
    }
  }

  async function handleSaveTask() {
    if (!taskDraft) return;
    setLoading((current) => ({ ...current, save: true }));
    try {
      const updated = await updateTask(taskDraft.id, {
        cron_expression: taskDraft.cron_expression,
        time_window_minutes: Number(taskDraft.time_window_minutes),
        delay_minutes: Number(taskDraft.delay_minutes),
        enabled: taskDraft.enabled
      });
      setTaskState(updated);
      setTaskDraft(updated);
      setTaskDirty(false);
      setNotice({ tone: "ok", text: "自动同步配置已保存" });
      await loadTasks();
    } catch (error) {
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "自动同步配置保存失败" });
    } finally {
      setLoading((current) => ({ ...current, save: false }));
    }
  }

  async function handleRunTaskNow() {
    if (!taskDraft) return;
    setLoading((current) => ({ ...current, sync: true }));
    try {
      const response = await runTask(taskDraft.id);
      setLastSync(response);
      setActiveExecution(null);
      setActiveExecutionId(response.execution_id);
      setNotice({ tone: "info", text: `自动同步任务 ${response.execution_id} 已启动` });
      await Promise.all([loadExecutions(), loadTasks()]);
    } catch (error) {
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "自动同步触发失败" });
      setLoading((current) => ({ ...current, sync: false }));
      await loadExecutions();
    }
  }

  function openSyncPicker() {
    setSyncDraftRange(syncDateRange);
    setSyncCalendarMonth(parseDateValue(syncDateRange.startDate));
    setSyncRangeMode("start");
    setSyncPickerOpen((open) => !open);
  }

  function chooseSyncDate(value: string) {
    setSyncDraftRange((current) => {
      if (syncRangeMode === "start") {
        setSyncRangeMode("end");
        return { startDate: value, endDate: value };
      }
      setSyncRangeMode("start");
      if (value < current.startDate) {
        return { startDate: value, endDate: current.startDate };
      }
      return { ...current, endDate: value };
    });
  }

  function applySyncDateRange() {
    setSyncDateRange(syncDraftRange);
    setSyncPickerOpen(false);
  }

  function chooseQuickSyncRange(range: { startDate: string; endDate: string }) {
    setSyncDraftRange({ startDate: range.startDate, endDate: range.endDate });
    setSyncCalendarMonth(parseDateValue(range.startDate));
    setSyncRangeMode("start");
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">E</div>
          <div>
            <strong>EDC Extractor</strong>
            <span>本地同步控制台</span>
          </div>
        </div>
        <nav className="nav-list">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                className={activeView === item.label ? "active" : ""}
                key={item.label}
                onClick={() => setActiveView(item.label)}
                type="button"
              >
                <Icon size={16} />
                {item.label}
              </button>
            );
          })}
        </nav>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <h1>EDC 同步管理</h1>
            <p>发现源端名称，确认映射，再执行可核验的同步任务。</p>
          </div>
          <div className="status-strip">
            <StatusPill label="健康" value={health?.status || "未知"} ok={health?.status === "ok"} />
            <StatusPill
              label="源库"
              value={connectionLabel(health?.source?.connected)}
              ok={Boolean(health?.source?.connected)}
              title={health?.source?.error || health?.source?.database}
            />
            <StatusPill
              label="源索引"
              value={sourceIndexLabel(health)}
              ok={Boolean(health?.source?.connected && health?.recommended_source_index)}
              title={health?.source?.index_error || undefined}
            />
            <StatusPill
              label="目标库"
              value={connectionLabel(health?.target?.connected)}
              ok={Boolean(health?.target?.connected)}
              title={health?.target?.error || health?.target?.database}
            />
            <button className="icon-button" onClick={() => void refreshOperationalState()}>
              {loading.health ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
              刷新
            </button>
            <StatusPill label="实时" value={sseState === "connected" ? "SSE" : "兜底"} ok={sseState === "connected"} />
          </div>
        </header>

        {notice && <div className={`notice ${notice.tone}`}>{notice.text}</div>}

        {activeView === "状态" && (
         <section className="metrics-row view-only">
           <Metric label="发现项" value={String(entities.length)} icon={<Database size={17} />} />
           <Metric label="未配置" value={String(unconfiguredCount)} icon={<Filter size={17} />} />
           <Metric
             label="补录待处理"
             value={String((onboarding.counts.pending || 0) + (onboarding.counts.backfill_pending || 0) + (onboarding.counts.backfilling || 0))}
             icon={<Clock3 size={17} />}
           />
           <Metric label="备份数据" value={String(backupCount)} icon={<ShieldCheck size={17} />} />
          <Metric label="最近执行" value={executions[0]?.status || "暂无"} icon={<Clock3 size={17} />} />
        </section>
        )}

        {(activeView === "EDC发现" || activeView === "映射录入") && (
        <section className={activeView === "EDC发现" ? "content-grid" : "content-grid inspector-focus"}>
          {activeView === "EDC发现" && (
          <div className="main-panel">
            <div className="panel-heading">
              <div>
                <h2>源端 EDC 发现</h2>
                <p>从云端 EDC 数据里读取真实名称，未配置项交由右侧确认。</p>
              </div>
              <button className="primary" onClick={() => void loadEntities()} disabled={loading.entities}>
                {loading.entities ? <Loader2 className="spin" size={16} /> : <Search size={16} />}
                发现
              </button>
            </div>

            <div className="toolbar">
              <label>
                开始时间
                <input
                  type="datetime-local"
                  value={range.start}
                  onChange={(event) => setRange((current) => ({ ...current, start: event.target.value }))}
                />
              </label>
              <label>
                结束时间
                <input
                  type="datetime-local"
                  value={range.end}
                  onChange={(event) => setRange((current) => ({ ...current, end: event.target.value }))}
                />
              </label>
              <label className="limit-field">
                数量
                <input
                  type="number"
                  min="1"
                  max="5000"
                  value={limit}
                  onChange={(event) => setLimit(Number(event.target.value))}
                />
              </label>
              <label className="check-field">
                <input
                  type="checkbox"
                  checked={onlyUnconfigured}
                  onChange={(event) => setOnlyUnconfigured(event.target.checked)}
                />
                仅未配置
              </label>
              <label className="discovery-search-field">
                搜索
                <input
                  placeholder="名称、SN、展示名或别名"
                  value={discoverySearch}
                  onChange={(event) => setDiscoverySearch(event.target.value)}
                />
              </label>
              <label>
                状态
                <select value={discoveryStatusFilter} onChange={(event) => setDiscoveryStatusFilter(event.target.value as DiscoveryStatusFilter)}>
                  <option value="all">全部</option>
                  <option value="enabled">仅启用</option>
                  <option value="disabled">仅禁用</option>
                </select>
              </label>
              {duplicateNameFilter && (
                <button className="ghost clear-filter" onClick={() => setDuplicateNameFilter(null)} type="button">
                  重复：{duplicateNameFilter} ×
                </button>
              )}
              <div className="discovery-summary">
                显示 {filteredEntities.length}/{entities.length}
              </div>
              <button className="ghost batch-button" onClick={() => void handleBulkSave()} disabled={loading.save || !selectedEntities.length}>
                {loading.save ? <Loader2 className="spin" size={15} /> : <Save size={15} />}
                批量写入 {selectedEntities.length}
              </button>
            </div>

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th className="select-col">
                      <input
                        aria-label="选择全部未配置项"
                        checked={allSelectableChecked}
                        disabled={!selectableEntities.length}
                        onChange={toggleAllSelectable}
                        type="checkbox"
                      />
                    </th>
                      <th><button className="sort-button" onClick={() => toggleDiscoverySort("edc_name")} type="button">EDC名称{discoverySortLabel("edc_name")}</button></th>
                      <th><button className="sort-button" onClick={() => toggleDiscoverySort("sn")} type="button">SN{discoverySortLabel("sn")}</button></th>
                      <th>主/备</th>
                      <th><button className="sort-button" onClick={() => toggleDiscoverySort("latest_create_time")} type="button">最近时间{discoverySortLabel("latest_create_time")}</button></th>
                      <th><button className="sort-button" onClick={() => toggleDiscoverySort("record_count")} type="button">记录数{discoverySortLabel("record_count")}</button></th>
                      <th>配置状态</th>
                      <th><button className="sort-button" onClick={() => toggleDiscoverySort("enabled")} type="button">同步状态{discoverySortLabel("enabled")}</button></th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                  {filteredEntities.map((row) => (
                    <tr
                      className={selected?.edc_name === row.edc_name && selected?.sn === row.sn ? "selected" : ""}
                      key={`${row.edc_name}-${row.sn}`}
                      onClick={() => selectEntity(row)}
                    >
                      <td className="select-col">
                        <input
                          aria-label={`选择 ${row.edc_name}`}
                          checked={selectedKeys.has(entityKey(row))}
                          disabled={row.configured}
                          onChange={() => toggleEntity(row)}
                          onClick={(event) => event.stopPropagation()}
                          type="checkbox"
                        />
                      </td>
                      <td className="name-cell">
                        <div className="entity-name-line">
                          <span>{row.edc_name}</span>
                          {!!row.duplicate_count && (
                            <button
                              aria-label={`筛选重复名称 ${row.edc_name}`}
                              className="duplicate-badge"
                              onClick={(event) => {
                                event.stopPropagation();
                                void filterDuplicateName(row.edc_name);
                              }}
                              title="点击筛选全部同名条目"
                              type="button"
                            >
                              +{row.duplicate_count}
                            </button>
                          )}
                        </div>
                      </td>
                      <td>{row.sn || "-"}</td>
                      <td>
                        <span className={row.is_backup ? "tag backup" : "tag primary"}>
                          {row.is_backup ? "备份" : "主"}
                        </span>
                      </td>
                      <td>{row.history_only ? "历史（当前窗口无数据）" : row.latest_create_time}</td>
                      <td>{row.history_only ? "-" : row.record_count}</td>
                      <td>
                        <span className={row.configured ? "state ok" : "state pending"}>
                          {row.configured ? "已配置" : "待录入"}
                        </span>
                      </td>
                      <td>
                        {row.configured && row.enabled !== undefined ? (
                          <span className={row.enabled ? "state ok" : "state pending"}>
                            {row.enabled ? "已启用" : "已禁用"}
                          </span>
                        ) : "-"}
                      </td>
                      <td>
                        {row.configured && row.entity_id !== undefined ? (
                          <button
                            className={row.enabled ? "status-action disable" : "status-action enable"}
                            disabled={statusPendingIds.has(row.entity_id)}
                            onClick={(event) => {
                              event.stopPropagation();
                              void handleEntityStatusToggle(row);
                            }}
                            type="button"
                          >
                            {statusPendingIds.has(row.entity_id) ? <Loader2 className="spin" size={14} /> : <Power size={14} />}
                            {row.enabled ? "禁用" : "启用"}
                          </button>
                        ) : "-"}
                      </td>
                    </tr>
                  ))}
                  {!filteredEntities.length && (
                    <tr>
                      <td className="empty" colSpan={9}>
                        {entities.length ? "没有匹配当前搜索或筛选条件的条目。" : "设置时间范围后点击“发现”，这里会列出源端真实 EDC 名称。"}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
          )}

          <aside className="inspector">
            <div className="panel-heading compact">
              <div>
                <h2>映射确认</h2>
                <p>主备由名称识别，字段由人工确认后写入。</p>
              </div>
              <SlidersHorizontal size={18} />
            </div>

            {form ? (
              <div className="form-stack">
                <ReadonlyField label="EDC名称" value={form.edc_name} />
                <ReadonlyField label="SN" value={form.sn || "-"} />
                <label>
                  展示名称
                  <input value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} />
                </label>
                <label>
                  识别别名
                  <input
                    value={form.alias}
                    placeholder="可选，便于 dashboard 识别"
                    onChange={(event) => setForm({ ...form, alias: event.target.value })}
                  />
                </label>
                <label>
                  地区
                  <input value={form.region} onChange={(event) => setForm({ ...form, region: event.target.value })} />
                </label>
                <label>
                  CP
                  <input value={form.cp} onChange={(event) => setForm({ ...form, cp: event.target.value })} />
                </label>
                <label>
                  类型
                  <select
                    value={form.entity_type}
                    onChange={(event) => setForm({ ...form, entity_type: event.target.value as EntityType })}
                  >
                    <option value="node">节点</option>
                    <option value="transmission">传输</option>
                  </select>
                </label>
                <label>
                  源区域
                  <input value={form.src_region} onChange={(event) => setForm({ ...form, src_region: event.target.value })} />
                </label>
                <label>
                  目区域
                  <input value={form.dst_region} onChange={(event) => setForm({ ...form, dst_region: event.target.value })} />
                </label>
                <label className="switch-row">
                  <input
                    type="checkbox"
                    checked={form.is_backup}
                    onChange={(event) => setForm({ ...form, is_backup: event.target.checked })}
                  />
                  备份节点
                </label>
                <label>
                  备注
                  <textarea value={form.remark} onChange={(event) => setForm({ ...form, remark: event.target.value })} />
                </label>
                <label className="switch-row">
                  <input
                    type="checkbox"
                    checked={form.enabled}
                    onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
                  />
                  启用同步和展示
                </label>
                <div className={form.is_backup ? "backup-note visible" : "backup-note"}>
                  该映射会以备份节点保存，后续查询和修复逻辑可按字段过滤。
                </div>
                <button className="primary wide" onClick={() => void handleSave()} disabled={loading.save}>
                  {loading.save ? <Loader2 className="spin" size={16} /> : <Save size={16} />}
                  写入映射
                </button>
              </div>
            ) : (
              <div className="empty-side">从左侧发现列表选择一条 EDC 名称。</div>
            )}
          </aside>
        </section>
        )}

        {activeView === "实体状态" && (
          <section className="content-grid inspector-focus">
            <div className="main-panel">
              <div className="panel-heading">
                <div>
                  <h2>实体状态管理</h2>
                  <p>enabled 同时控制 extractor 同步和结算平台有效展示；历史事实不会删除。</p>
                </div>
                <button className="icon-button" onClick={() => void loadConfiguredEntities()} disabled={loading.configured}>
                  {loading.configured ? <Loader2 className="spin" size={16} /> : <RefreshCw size={16} />}
                  刷新
                </button>
              </div>
              <div className="toolbar entity-status-toolbar">
                <label>
                  搜索
                  <input
                    placeholder="名称、SN或展示名"
                    value={entityStatusSearch}
                    onChange={(event) => setEntityStatusSearch(event.target.value)}
                  />
                </label>
                <label>
                  状态
                  <select value={entityStatusFilter} onChange={(event) => setEntityStatusFilter(event.target.value as typeof entityStatusFilter)}>
                    <option value="all">全部</option>
                    <option value="enabled">仅启用</option>
                    <option value="disabled">仅禁用</option>
                  </select>
                </label>
                <div className="status-summary">
                  <span>实体 {configuredEntities.length}</span>
                  <span>启用 {configuredEntities.filter((item) => item.enabled).length}</span>
                  <span>禁用 {configuredEntities.filter((item) => !item.enabled).length}</span>
                </div>
              </div>
              <div className="table-wrap">
                <table className="entity-status-table">
                  <thead>
                    <tr>
                      <th>实体ID</th>
                      <th>EDC名称</th>
                      <th>SN</th>
                      <th>主/备</th>
                      <th>状态</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredConfiguredEntities.map((item) => {
                      const pending = statusPendingIds.has(item.id);
                      return (
                        <tr key={item.id}>
                          <td>{item.id}</td>
                          <td className="name-cell">{item.edc_name}</td>
                          <td>{item.sn || "-"}</td>
                          <td><span className={item.is_backup ? "tag backup" : "tag primary"}>{item.is_backup ? "备份" : "主"}</span></td>
                          <td><span className={item.enabled ? "state ok" : "state pending"}>{item.enabled ? "已启用" : "已禁用"}</span></td>
                          <td>
                            <button
                              className={item.enabled ? "status-action disable" : "status-action enable"}
                              disabled={pending}
                              onClick={() => void handleEntityStatusToggle(item)}
                              type="button"
                            >
                              {pending ? <Loader2 className="spin" size={14} /> : <Power size={14} />}
                              {item.enabled ? "禁用" : "启用"}
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                    {!filteredConfiguredEntities.length && (
                      <tr><td className="empty" colSpan={6}>没有匹配的已配置实体。</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        )}

        {(activeView === "同步任务" || activeView === "执行记录") && (
        <section className={activeView === "同步任务" ? "bottom-grid sync-focus" : "bottom-grid timeline-focus"}>
          {activeView === "同步任务" && (
          <div className="sync-stack">
          <div className="sync-panel">
            <div className="panel-heading compact">
              <div>
                <h2>手动同步</h2>
                <p>使用已启用映射写入本地 5 分钟事实表。</p>
              </div>
            </div>
            <div className="sync-form">
              <div className="date-range-control">
                <span>日期范围</span>
                <button className="date-range-trigger" onClick={openSyncPicker} type="button">
                  <CalendarDays size={15} />
                  {syncDateRange.startDate} 至 {syncDateRange.endDate}
                </button>
                {syncPickerOpen && (
                  <div className="date-range-popover">
                    <div className="range-summary">
                      <span>同步日期范围</span>
                      <strong>{syncDraftRange.startDate} 至 {syncDraftRange.endDate}</strong>
                      <em>{syncRangeMode === "start" ? "选择开始日期" : "选择结束日期"}</em>
                      <small>开始 00:00:00，结束 23:59:59</small>
                      <div className="quick-ranges">
                        {quickDateRanges().map((range) => (
                          <button key={range.label} onClick={() => chooseQuickSyncRange(range)} type="button">
                            {range.label}
                          </button>
                        ))}
                      </div>
                    </div>
                    <div className="calendar-shell">
                      <div className="calendar-toolbar">
                        <button className="icon-button compact" onClick={() => setSyncCalendarMonth((date) => addMonths(date, -1))} type="button">
                          <ChevronLeft size={15} />
                        </button>
                        <span>{formatMonthTitle(syncCalendarMonth)} - {formatMonthTitle(addMonths(syncCalendarMonth, 1))}</span>
                        <button className="icon-button compact" onClick={() => setSyncCalendarMonth((date) => addMonths(date, 1))} type="button">
                          <ChevronRight size={15} />
                        </button>
                      </div>
                      <div className="calendar-months">
                        {[syncCalendarMonth, addMonths(syncCalendarMonth, 1)].map((month) => (
                          <CalendarMonth
                            key={formatMonthTitle(month)}
                            month={month}
                            range={syncDraftRange}
                            onSelect={chooseSyncDate}
                          />
                        ))}
                      </div>
                    </div>
                    <div className="range-actions">
                      <button className="ghost" onClick={() => setSyncPickerOpen(false)} type="button">
                        取消
                      </button>
                      <button className="primary" onClick={applySyncDateRange} type="button">
                        应用范围
                      </button>
                    </div>
                  </div>
                )}
              </div>
              <button className="primary" onClick={() => void handleSync()} disabled={loading.sync}>
                {loading.sync ? <Loader2 className="spin" size={16} /> : <Play size={16} />}
                执行同步
              </button>
            </div>
            {lastSync && (
              <div className="sync-result">
                <span>任务 #{lastSync.execution_id}</span>
                <span>读取 {lastSync.rows_read ?? 0}</span>
                <span>写入 {lastSync.rows_written ?? 0}</span>
                <span>未映射 {lastSync.unmapped_count ?? 0}</span>
                <span>服务负值修正 {lastSync.negative_service_count ?? 0}</span>
                <span>回源负值修正 {lastSync.negative_cache_count ?? 0}</span>
              </div>
            )}
            {activeExecution && (
              <SyncProgress execution={activeExecution} />
            )}
          </div>
          <div className="sync-panel">
            <div className="panel-heading compact">
              <div>
                <h2>自动同步</h2>
                <p>按 cron 周期自动补最近窗口，执行结果进入同一份记录。</p>
              </div>
              <span className={taskState?.enabled ? "state ok" : "state pending"}>
                {taskState?.enabled ? "已启用" : "已暂停"}
              </span>
            </div>
            {taskDraft ? (
              <>
                <div className="task-form">
                  <label>
                    Cron
                    <input
                      value={taskDraft.cron_expression}
                      onChange={(event) => {
                        setTaskDirty(true);
                        setTaskDraft({ ...taskDraft, cron_expression: event.target.value });
                      }}
                    />
                  </label>
                  <label>
                    同步窗口(分钟)
                    <input
                      min="1"
                      type="number"
                      value={taskDraft.time_window_minutes}
                      onChange={(event) => {
                        setTaskDirty(true);
                        setTaskDraft({ ...taskDraft, time_window_minutes: Number(event.target.value) });
                      }}
                    />
                  </label>
                  <label>
                    延迟(分钟)
                    <input
                      min="1"
                      type="number"
                      value={taskDraft.delay_minutes}
                      onChange={(event) => {
                        setTaskDirty(true);
                        setTaskDraft({ ...taskDraft, delay_minutes: Number(event.target.value) });
                      }}
                    />
                  </label>
                  <label className="switch-row">
                    <input
                      checked={taskDraft.enabled}
                      onChange={(event) => {
                        setTaskDirty(true);
                        setTaskDraft({ ...taskDraft, enabled: event.target.checked });
                      }}
                      type="checkbox"
                    />
                    启用自动同步
                  </label>
                </div>
                <div className="task-actions">
                  <span>
                    下次执行：{taskState?.next_run_time ? formatNextRun(taskState.next_run_time) : "暂无"}
                  </span>
                  <button className="ghost" onClick={() => void handleRunTaskNow()} disabled={loading.sync || !taskDraft.enabled}>
                    {loading.sync ? <Loader2 className="spin" size={15} /> : <Play size={15} />}
                    立即执行
                  </button>
                  <button className="primary" onClick={() => void handleSaveTask()} disabled={loading.save}>
                    {loading.save ? <Loader2 className="spin" size={15} /> : <Power size={15} />}
                    保存配置
                  </button>
                </div>
              </>
            ) : (
              <div className="empty-side">暂无自动同步任务。</div>
            )}
          </div>
          </div>
          )}

          {activeView === "执行记录" && (
          <div className="timeline-panel">
            <div className="panel-heading compact">
              <div>
                <h2>执行记录</h2>
                <p>最近 50 次任务状态。</p>
              </div>
              <button className="ghost" onClick={() => void loadExecutions()}>
                <RefreshCw size={15} />
                更新
              </button>
            </div>
            <div className="timeline">
              {executions.slice(0, 6).map((item) => (
                <div className="timeline-item" key={item.id}>
                  <CheckCircle2 className={item.status === "completed" ? "ok-icon" : "warn-icon"} size={16} />
                  <div>
                    <strong>#{item.id} {executionLabel(item)} {item.status}</strong>
                    <span>{item.data_start_time} 至 {item.data_end_time}</span>
                    {item.error_message && <em>{item.error_message}</em>}
                  </div>
                  <small>{item.rows_written} 行</small>
                </div>
              ))}
              {!executions.length && <div className="empty-side">暂无执行记录。</div>}
            </div>
          </div>
          )}
        </section>
        )}
      </main>
    </div>
  );
}

function SyncProgress({ execution }: { execution: Execution }) {
  const progress = parseProgress(execution.progress_info);
  const percent = Math.max(0, Math.min(100, Number(progress.percent || 0)));
  const isMetadata = progress.kind === "metadata_reconcile";
  const completed = progress.completed_chunks || 0;
  const total = progress.total_chunks || 0;
  return (
    <div className="sync-progress">
      <div className="progress-heading">
        <strong>任务 #{execution.id} {execution.status}</strong>
        <span>{percent}%</span>
      </div>
      <div className="progress-bar">
        <i style={{ width: `${percent}%` }} />
      </div>
      <div className="progress-grid">
        {isMetadata ? (
          <>
            <span>实体 {progress.completed_entities || 0}/{progress.total_entities || "-"}</span>
            <span>扫描 {progress.rows_scanned || 0}</span>
            <span>更新 {progress.rows_updated || 0}</span>
            <span>当前实体 {progress.entity_id || "-"}</span>
          </>
        ) : (
          <>
            <span>分片 {completed}/{total || "-"}</span>
            <span>读取 {execution.rows_read}</span>
            <span>写入 {execution.rows_written}</span>
            <span>未映射 {execution.unmapped_count}</span>
            <span>服务负值修正 {progress.negative_service_count || 0}</span>
            <span>回源负值修正 {progress.negative_cache_count || 0}</span>
          </>
        )}
      </div>
      {progress.current_start_time && progress.current_end_time && (
        <small>{progress.current_start_time} 至 {progress.current_end_time}</small>
      )}
      {execution.error_message && <em>{execution.error_message}</em>}
    </div>
  );
}

function executionLabel(item: Execution) {
  const progress = parseProgress(item.progress_info);
  return progress.kind === "metadata_reconcile" ? "映射元数据同步" : "数据同步";
}

function parseProgress(value: string | null): ExecutionProgress {
  if (!value) return {};
  try {
    return JSON.parse(value) as ExecutionProgress;
  } catch {
    return {};
  }
}

function parseEventData<T>(event: Event): T {
  const message = event as MessageEvent<string>;
  return message.data ? JSON.parse(message.data) as T : ({} as T);
}

function StatusPill({ label, value, ok, title }: { label: string; value: string; ok: boolean; title?: string }) {
  return (
    <span className={ok ? "status-pill ok" : "status-pill warn"} title={title}>
      <i />
      {label}: {value}
    </span>
  );
}

function Metric({ label, value, icon }: { label: string; value: string; icon: React.ReactNode }) {
  return (
    <div className="metric">
      <span>{icon}</span>
      <div>
        <strong>{value}</strong>
        <small>{label}</small>
      </div>
    </div>
  );
}

function ReadonlyField({ label, value }: { label: string; value: string }) {
  return (
    <label>
      {label}
      <input readOnly value={value} />
    </label>
  );
}

function entityKey(row: Pick<SourceEntity, "edc_name" | "sn">) {
  return `${row.edc_name}\u0000${row.sn}`;
}

function CalendarMonth({
  month,
  range,
  onSelect
}: {
  month: Date;
  range: { startDate: string; endDate: string };
  onSelect: (value: string) => void;
}) {
  const days = buildCalendarMonth(month);
  const weekdays = ["日", "一", "二", "三", "四", "五", "六"];
  return (
    <div className="calendar-month">
      <strong>{formatMonthTitle(month)}</strong>
      <div className="calendar-weekdays">
        {weekdays.map((day) => (
          <span key={day}>{day}</span>
        ))}
      </div>
      <div className="calendar-grid">
        {days.map((day) => {
          const selected = day.value === range.startDate || day.value === range.endDate;
          const inRange = day.value > range.startDate && day.value < range.endDate;
          return (
            <button
              className={[
                "calendar-day",
                day.inMonth ? "" : "muted",
                selected ? "selected" : "",
                inRange ? "in-range" : ""
              ].join(" ")}
              key={day.value}
              onClick={() => onSelect(day.value)}
              type="button"
            >
              {day.date.getDate()}
            </button>
          );
        })}
      </div>
    </div>
  );
}
