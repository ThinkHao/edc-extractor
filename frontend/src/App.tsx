import { useCallback, useEffect, useMemo, useState } from "react";
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
  EntityPayload,
  Execution,
  ExecutionProgress,
  getTasks,
  getExecution,
  getExecutions,
  getHealth,
  Health,
  runTask,
  runSync,
  saveEntity,
  saveEntities,
  ScheduledTask,
  SourceEntity,
  SyncResult,
  updateTask
} from "./api";

type Notice = { tone: "ok" | "error" | "info"; text: string } | null;
type ViewKey = "状态" | "EDC发现" | "映射录入" | "同步任务" | "执行记录";
type RangeSelectMode = "start" | "end";

const navItems: Array<{ label: ViewKey; icon: typeof Activity }> = [
  { label: "状态", icon: Activity },
  { label: "EDC发现", icon: Search },
  { label: "映射录入", icon: ShieldCheck },
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

function guessMapping(row: SourceEntity): EntityPayload {
  const displayName = row.edc_name.replace(/-backup/gi, "");
  const parts = displayName.split("-");
  return {
    edc_name: row.edc_name,
    sn: row.sn,
    display_name: displayName,
    region: parts[0] || "",
    cp: parts[1] || "",
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

export function App() {
  const initialRange = useMemo(defaultRange, []);
  const [health, setHealth] = useState<Health | null>(null);
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [entities, setEntities] = useState<SourceEntity[]>([]);
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
  const [loading, setLoading] = useState({ health: false, entities: false, save: false, sync: false });
  const [notice, setNotice] = useState<Notice>(null);
  const [lastSync, setLastSync] = useState<SyncResult | null>(null);
  const [activeExecutionId, setActiveExecutionId] = useState<number | null>(null);
  const [activeExecution, setActiveExecution] = useState<Execution | null>(null);
  const [activeView, setActiveView] = useState<ViewKey>("EDC发现");
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(() => new Set());
  const [taskState, setTaskState] = useState<ScheduledTask | null>(null);
  const [taskDraft, setTaskDraft] = useState<ScheduledTask | null>(null);
  const [taskDirty, setTaskDirty] = useState(false);

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

  const refreshOperationalState = useCallback(async () => {
    await Promise.all([loadHealth(), loadExecutions(), loadTasks()]);
  }, [loadExecutions, loadHealth, loadTasks]);

  const loadEntities = useCallback(async () => {
    setLoading((current) => ({ ...current, entities: true }));
    try {
      const response = await discoverEntities({
        startTime: toApiTime(range.start),
        endTime: toApiTime(range.end),
        limit,
        onlyUnconfigured
      });
      setEntities(response.items);
      const nextSelected = response.items[0] || null;
      setSelected(nextSelected);
      setForm(nextSelected ? guessMapping(nextSelected) : null);
      setSelectedKeys(new Set());
      setNotice({ tone: "ok", text: `已发现 ${response.items.length} 条 EDC 名称` });
    } catch (error) {
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "EDC 发现失败" });
    } finally {
      setLoading((current) => ({ ...current, entities: false }));
    }
  }, [limit, onlyUnconfigured, range.end, range.start]);

  useEffect(() => {
    void refreshOperationalState();
  }, [refreshOperationalState]);

  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === "hidden") return;
      void Promise.all([loadExecutions(), loadTasks()]);
    };
    const timer = window.setInterval(refresh, 5000);
    return () => window.clearInterval(timer);
  }, [loadExecutions, loadTasks]);

  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === "hidden") return;
      void loadHealth();
    };
    const timer = window.setInterval(refresh, 30000);
    return () => window.clearInterval(timer);
  }, [loadHealth]);

  useEffect(() => {
    if (!activeExecutionId) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const execution = await getExecution(activeExecutionId);
        if (cancelled) return;
        setActiveExecution(execution);
        if (execution.status === "completed") {
          setLoading((current) => ({ ...current, sync: false }));
          setNotice({ tone: "ok", text: `同步任务 ${execution.id} 已完成` });
          setLastSync({
            execution_id: execution.id,
            rows_read: execution.rows_read,
            rows_written: execution.rows_written,
            unmapped_count: execution.unmapped_count,
            duration_ms: execution.duration_ms
          });
          setActiveExecutionId(null);
          await Promise.all([loadExecutions(), loadTasks()]);
        }
        if (execution.status === "failed") {
          setLoading((current) => ({ ...current, sync: false }));
          setNotice({ tone: "error", text: execution.error_message || `同步任务 ${execution.id} 失败` });
          setActiveExecutionId(null);
          await Promise.all([loadExecutions(), loadTasks()]);
        }
      } catch (error) {
        if (!cancelled) {
          setNotice({ tone: "error", text: error instanceof Error ? error.message : "同步进度加载失败" });
        }
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeExecutionId, loadExecutions, loadTasks]);

  useEffect(() => {
    if (activeExecutionId) return;
    const running = executions.find((item) => item.status === "running");
    if (running) {
      setActiveExecution(running);
      return;
    }
    setActiveExecution((current) => (current?.status === "running" ? null : current));
  }, [activeExecutionId, executions]);

  const configuredCount = entities.filter((item) => item.configured).length;
  const backupCount = entities.filter((item) => item.is_backup).length;
  const unconfiguredCount = entities.length - configuredCount;
  const selectableEntities = entities.filter((item) => !item.configured);
  const selectedEntities = entities.filter((item) => selectedKeys.has(entityKey(item)));
  const allSelectableChecked =
    selectableEntities.length > 0 && selectableEntities.every((item) => selectedKeys.has(entityKey(item)));

  function selectEntity(row: SourceEntity) {
    setSelected(row);
    setForm(guessMapping(row));
    setNotice(null);
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
      await saveEntity(form);
      setNotice({ tone: "ok", text: `已写入映射：${form.edc_name}` });
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
      const payload = selectedEntities.map(guessMapping);
      const response = await saveEntities(payload);
      setNotice({ tone: "ok", text: `已批量写入 ${response.upserted} 条映射` });
      setSelectedKeys(new Set());
      await loadEntities();
    } catch (error) {
      setNotice({ tone: "error", text: error instanceof Error ? error.message : "批量写入失败" });
    } finally {
      setLoading((current) => ({ ...current, save: false }));
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
          </div>
        </header>

        {notice && <div className={`notice ${notice.tone}`}>{notice.text}</div>}

        {activeView === "状态" && (
        <section className="metrics-row view-only">
          <Metric label="发现项" value={String(entities.length)} icon={<Database size={17} />} />
          <Metric label="未配置" value={String(unconfiguredCount)} icon={<Filter size={17} />} />
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
                    <th>EDC名称</th>
                    <th>SN</th>
                    <th>主/备</th>
                    <th>最近时间</th>
                    <th>记录数</th>
                    <th>配置状态</th>
                  </tr>
                </thead>
                <tbody>
                  {entities.map((row) => (
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
                      <td className="name-cell">{row.edc_name}</td>
                      <td>{row.sn || "-"}</td>
                      <td>
                        <span className={row.is_backup ? "tag backup" : "tag primary"}>
                          {row.is_backup ? "备份" : "主"}
                        </span>
                      </td>
                      <td>{row.latest_create_time}</td>
                      <td>{row.record_count}</td>
                      <td>
                        <span className={row.configured ? "state ok" : "state pending"}>
                          {row.configured ? "已配置" : "待录入"}
                        </span>
                      </td>
                    </tr>
                  ))}
                  {!entities.length && (
                    <tr>
                      <td className="empty" colSpan={7}>
                        设置时间范围后点击“发现”，这里会列出源端真实 EDC 名称。
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
                  地区
                  <input value={form.region} onChange={(event) => setForm({ ...form, region: event.target.value })} />
                </label>
                <label>
                  CP
                  <input value={form.cp} onChange={(event) => setForm({ ...form, cp: event.target.value })} />
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
                    <strong>#{item.id} {item.status}</strong>
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
        <span>分片 {completed}/{total || "-"}</span>
        <span>读取 {execution.rows_read}</span>
        <span>写入 {execution.rows_written}</span>
        <span>未映射 {execution.unmapped_count}</span>
      </div>
      {progress.current_start_time && progress.current_end_time && (
        <small>{progress.current_start_time} 至 {progress.current_end_time}</small>
      )}
      {execution.error_message && <em>{execution.error_message}</em>}
    </div>
  );
}

function parseProgress(value: string | null): ExecutionProgress {
  if (!value) return {};
  try {
    return JSON.parse(value) as ExecutionProgress;
  } catch {
    return {};
  }
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
