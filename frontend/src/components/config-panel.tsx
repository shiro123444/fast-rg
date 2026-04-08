import { useEffect, useState } from "preact/hooks";
import type { ConfigField, ConfigSection } from "../types/runtime";
import type { TaobaoPoolSnapshot } from "../types/api";
import {
  abandonTaobaoPool,
  fetchTaobaoPoolSnapshot,
  importTaobaoPoolText,
  requeueTaobaoPool,
  testPoolConnection,
} from "../services/api";

type ConfigPanelProps = {
  sections: ConfigSection[];
  onValueChange: (sectionKey: string, fieldKey: string, nextValue: string | number | boolean) => void;
  onSave: () => void;
  onStart: () => void;
  onStartLoop: () => void;
  onStop: () => void;
  onLogout: () => void;
  busy?: boolean;
  running?: boolean;
  loopRunning?: boolean;
  hasStoredToken?: boolean;
};

type ConfigCategory = "common" | "mail" | "advanced";

type CpaPoolRow = {
  name: string;
  base_url: string;
  token: string;
  target_type: string;
  min_candidates: number;
  enabled: boolean;
};

function parseCpaPoolRows(value: string): CpaPoolRow[] {
  return String(value || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, idx) => {
      const out: Record<string, string> = {};
      line.split(";").forEach((part) => {
        const i = part.indexOf("=");
        if (i < 0) return;
        const key = part.slice(0, i).trim();
        const val = part.slice(i + 1).trim();
        if (!key) return;
        out[key] = val;
      });
      return {
        name: out.name || `pool-${idx + 1}`,
        base_url: out.base_url || "",
        token: out.token || "",
        target_type: out.target_type || "codex",
        min_candidates: Number(out.min_candidates || 50) || 50,
        enabled: ["1", "true", "yes", "on"].includes(String(out.enabled || "1").toLowerCase()),
      };
    });
}

function serializeCpaPoolRows(rows: CpaPoolRow[]): string {
  return rows
    .map((row) =>
      [
        `name=${row.name || ""}`,
        `base_url=${row.base_url || ""}`,
        `token=${row.token || ""}`,
        `target_type=${row.target_type || "codex"}`,
        `min_candidates=${Number(row.min_candidates) || 50}`,
        `enabled=${row.enabled ? "1" : "0"}`,
      ].join(";"),
    )
    .join("\n");
}

function CpaPoolsEditor(props: {
  sectionKey: string;
  fieldKey: string;
  value: string;
  onValueChange: ConfigPanelProps["onValueChange"];
}) {
  const { sectionKey, fieldKey, value, onValueChange } = props;
  const [rows, setRows] = useState<CpaPoolRow[]>(() => {
    const parsed = parseCpaPoolRows(value);
    return parsed.length
      ? parsed
      : [
          {
            name: "default",
            base_url: "http://127.0.0.1:8317",
            token: "",
            target_type: "codex",
            min_candidates: 50,
            enabled: true,
          },
      ];
  });
  const [testingIndex, setTestingIndex] = useState<number | null>(null);
  const [testMessage, setTestMessage] = useState<string>("");

  useEffect(() => {
    const parsed = parseCpaPoolRows(value);
    if (parsed.length) {
      setRows(parsed);
    }
  }, [value]);

  const syncRows = (nextRows: CpaPoolRow[]) => {
    setRows(nextRows);
    onValueChange(sectionKey, fieldKey, serializeCpaPoolRows(nextRows));
  };

  const updateRow = (index: number, patch: Partial<CpaPoolRow>) => {
    const next = rows.map((row, idx) => (idx === index ? { ...row, ...patch } : row));
    syncRows(next);
  };

  const addRow = () => {
    syncRows([
      ...rows,
      {
        name: `pool-${rows.length + 1}`,
        base_url: "",
        token: "",
        target_type: "codex",
        min_candidates: rows[0]?.min_candidates || 50,
        enabled: true,
      },
    ]);
  };

  const removeRow = (index: number) => {
    const next = rows.filter((_, idx) => idx !== index);
    syncRows(next.length ? next : []);
  };

  const runTest = async (index: number) => {
    const row = rows[index];
    if (!row) return;
    setTestingIndex(index);
    setTestMessage("");
    try {
      const result = await testPoolConnection({
        base_url: row.base_url,
        token: row.token,
        target_type: row.target_type,
      });
      if (result.ok) {
        setTestMessage(
          `号池[${row.name}] 连接成功: candidates=${result.candidates ?? "-"}, total=${result.total ?? "-"}`,
        );
      } else {
        setTestMessage(`号池[${row.name}] 连接失败: ${result.message}`);
      }
    } catch (error) {
      setTestMessage(`号池[${row.name}] 连接失败: ${String(error)}`);
    } finally {
      setTestingIndex(null);
    }
  };

  return (
    <div class="pool-editor">
      {rows.map((row, index) => (
        <div class="pool-row" key={`${row.name}-${index}`}>
          <div class="pool-row-head">
            <strong>号池 {index + 1}</strong>
            <div>
              <button
                type="button"
                class="link-button"
                disabled={testingIndex === index}
                onClick={() => runTest(index)}
              >
                {testingIndex === index ? "测试中..." : "测试连接"}
              </button>
              <button type="button" class="link-button" onClick={() => removeRow(index)}>
                删除
              </button>
            </div>
          </div>
          <input
            type="text"
            value={row.name}
            placeholder="名称"
            onInput={(event) => updateRow(index, { name: (event.currentTarget as HTMLInputElement).value })}
          />
          <input
            type="text"
            value={row.base_url}
            placeholder="CPA 地址"
            onInput={(event) => updateRow(index, { base_url: (event.currentTarget as HTMLInputElement).value })}
          />
          <input
            type="password"
            value={row.token}
            placeholder="CPA 令牌"
            onInput={(event) => updateRow(index, { token: (event.currentTarget as HTMLInputElement).value })}
          />
          <div class="pool-row-inline">
            <select
              value={row.target_type}
              onInput={(event) => updateRow(index, { target_type: (event.currentTarget as HTMLSelectElement).value })}
            >
              <option value="codex">codex</option>
              <option value="chatgpt">chatgpt</option>
            </select>
            <input
              type="number"
              value={String(row.min_candidates)}
              placeholder="阈值"
              onInput={(event) =>
                updateRow(index, { min_candidates: Number((event.currentTarget as HTMLInputElement).value) || 50 })
              }
            />
            <label class="check-row">
              <input
                type="checkbox"
                checked={row.enabled}
                onInput={(event) => updateRow(index, { enabled: (event.currentTarget as HTMLInputElement).checked })}
              />
              <span>启用</span>
            </label>
          </div>
        </div>
      ))}
      <button type="button" class="button secondary" onClick={addRow}>
        + 添加号池
      </button>
      {testMessage ? <div class="field-hint">{testMessage}</div> : null}
    </div>
  );
}

function TaobaoPoolManager(props: { visible: boolean }) {
  const { visible } = props;
  const [snapshot, setSnapshot] = useState<TaobaoPoolSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [importText, setImportText] = useState("");
  const [message, setMessage] = useState("");
  const [selectedFailed, setSelectedFailed] = useState<Record<string, boolean>>({});

  const refresh = async () => {
    setLoading(true);
    try {
      const data = await fetchTaobaoPoolSnapshot();
      setSnapshot(data);
    } catch (error) {
      setMessage(`加载淘宝邮箱池失败: ${String(error)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!visible) {
      return;
    }
    refresh().catch(() => undefined);
  }, [visible]);

  const failedEmails = (snapshot?.failed ?? []).map((item) => item.email);
  const selectedEmails = failedEmails.filter((email) => selectedFailed[email]);

  const handleImport = async () => {
    if (!importText.trim()) {
      setMessage("请先粘贴淘宝邮箱文本");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const result = await importTaobaoPoolText(importText);
      setSnapshot(result.snapshot ?? null);
      const added = Number(result.result?.added ?? 0);
      const duplicates = Number(result.result?.duplicates ?? 0);
      const invalid = Number((result.result?.invalid_lines ?? []).length);
      setMessage(`导入完成: 新增 ${added}, 重复 ${duplicates}, 无效 ${invalid}`);
      setImportText("");
      setSelectedFailed({});
    } catch (error) {
      setMessage(`导入失败: ${String(error)}`);
    } finally {
      setBusy(false);
    }
  };

  const handleRequeue = async (emails: string[]) => {
    setBusy(true);
    setMessage("");
    try {
      const result = await requeueTaobaoPool(emails);
      setSnapshot(result.snapshot ?? null);
      const updated = Number(result.result?.updated ?? 0);
      setMessage(`重跑队列更新完成: ${updated}`);
      setSelectedFailed({});
    } catch (error) {
      setMessage(`重跑失败: ${String(error)}`);
    } finally {
      setBusy(false);
    }
  };

  const handleAbandon = async () => {
    if (!selectedEmails.length) {
      setMessage("请先勾选失败邮箱");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const result = await abandonTaobaoPool(selectedEmails);
      setSnapshot(result.snapshot ?? null);
      const updated = Number(result.result?.updated ?? 0);
      setMessage(`已遗弃 ${updated} 个失败邮箱`);
      setSelectedFailed({});
    } catch (error) {
      setMessage(`遗弃失败: ${String(error)}`);
    } finally {
      setBusy(false);
    }
  };

  if (!visible) {
    return null;
  }

  const summary = snapshot?.summary;

  return (
    <section class="config-group expanded taobao-group">
      <div class="group-toggle">
        <span class="group-title">
          淘宝邮箱池
          <span class="group-key">taobao_pool</span>
        </span>
      </div>
      <div class="group-content taobao-content">
        <div class="taobao-summary">
          <span>未使用: {summary?.unused ?? 0}</span>
          <span>使用成功: {summary?.used ?? 0}</span>
          <span>失败: {summary?.failed ?? 0}</span>
          <span>占用中: {summary?.in_use ?? 0}</span>
        </div>

        <label class="field">
          <span class="field-label">粘贴导入 (支持 email----password----clientId----refreshToken)</span>
          <textarea
            value={importText}
            placeholder="每行一条，重复自动去重"
            onInput={(event) => setImportText((event.currentTarget as HTMLTextAreaElement).value)}
          />
        </label>

        <div class="taobao-actions">
          <button type="button" class="button secondary" disabled={busy} onClick={handleImport}>
            {busy ? "处理中..." : "导入并去重"}
          </button>
          <button type="button" class="button tertiary" disabled={busy || loading} onClick={() => refresh()}>
            {loading ? "刷新中..." : "刷新状态"}
          </button>
          <button
            type="button"
            class="button secondary"
            disabled={busy || (summary?.failed ?? 0) <= 0}
            onClick={() => handleRequeue([])}
          >
            全部失败重跑
          </button>
          <button
            type="button"
            class="button secondary"
            disabled={busy || selectedEmails.length <= 0}
            onClick={() => handleRequeue(selectedEmails)}
          >
            重跑选中失败
          </button>
          <button
            type="button"
            class="button warning"
            disabled={busy || selectedEmails.length <= 0}
            onClick={handleAbandon}
          >
            遗弃选中失败
          </button>
        </div>

        <div class="taobao-lists">
          <div class="taobao-list">
            <div class="taobao-list-title">未使用 ({summary?.unused ?? 0})</div>
            <div class="taobao-list-body">
              {(snapshot?.unused ?? []).map((item) => (
                <div class="taobao-item" key={`unused-${item.email}`}>
                  <span>{item.email}</span>
                </div>
              ))}
            </div>
          </div>

          <div class="taobao-list">
            <div class="taobao-list-title">已使用 ({summary?.used ?? 0})</div>
            <div class="taobao-list-body">
              {(snapshot?.used ?? []).map((item) => (
                <div class="taobao-item" key={`used-${item.email}`}>
                  <span>{item.email}</span>
                </div>
              ))}
            </div>
          </div>

          <div class="taobao-list">
            <div class="taobao-list-title">失败 ({summary?.failed ?? 0})</div>
            <div class="taobao-list-body">
              {(snapshot?.failed ?? []).map((item) => (
                <label class="taobao-item check-row" key={`failed-${item.email}`}>
                  <input
                    type="checkbox"
                    checked={Boolean(selectedFailed[item.email])}
                    onInput={(event) => {
                      const checked = (event.currentTarget as HTMLInputElement).checked;
                      setSelectedFailed((current) => ({ ...current, [item.email]: checked }));
                    }}
                  />
                  <span>{item.email}</span>
                  <small>{item.last_error || "register_failed"}</small>
                </label>
              ))}
            </div>
          </div>
        </div>

        {message ? <div class="field-hint">{message}</div> : null}
      </div>
    </section>
  );
}

function FieldControl(props: {
  sectionKey: string;
  field: ConfigField;
  onValueChange: ConfigPanelProps["onValueChange"];
}) {
  const { sectionKey, field, onValueChange } = props;

  if (field.type === "select") {
    return (
      <select
        value={String(field.value)}
        onInput={(event) =>
          onValueChange(sectionKey, field.key, (event.currentTarget as HTMLSelectElement).value)
        }
      >
        {(field.options ?? []).map((option) => (
          <option value={option.value} key={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  }

  if (field.type === "checkbox") {
    return (
      <label class="check-row">
        <input
          type="checkbox"
          checked={Boolean(field.value)}
          onInput={(event) =>
            onValueChange(sectionKey, field.key, (event.currentTarget as HTMLInputElement).checked)
          }
        />
        <span>
          {field.label} <span class="field-key">{field.key}</span>
        </span>
      </label>
    );
  }

  if (field.type === "textarea") {
    if (sectionKey === "priority" && field.key === "cpa_pools") {
      return (
        <CpaPoolsEditor
          sectionKey={sectionKey}
          fieldKey={field.key}
          value={String(field.value)}
          onValueChange={onValueChange}
        />
      );
    }
    return (
      <textarea
        value={String(field.value)}
        onInput={(event) =>
          onValueChange(sectionKey, field.key, (event.currentTarget as HTMLTextAreaElement).value)
        }
      />
    );
  }

  return (
    <input
      type={field.type}
      value={String(field.value)}
      placeholder={field.sensitive && String(field.value) === "__MASKED__" ? "已保存，留空或保持不变将沿用原值" : ""}
      onInput={(event) => {
        const target = event.currentTarget as HTMLInputElement;
        const nextValue = field.type === "number" ? Number(target.value) : target.value;
        onValueChange(sectionKey, field.key, nextValue);
      }}
    />
  );
}

export function ConfigPanel(props: ConfigPanelProps) {
  const {
    sections,
    onValueChange,
    onSave,
    onStart,
    onStartLoop,
    onStop,
    onLogout,
    busy = false,
    running = false,
    loopRunning = false,
    hasStoredToken = false,
  } = props;
  const [activeCategory, setActiveCategory] = useState<ConfigCategory>("common");
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({
    priority: true,
    mail: true,
    luckmail: true,
    gmail: false,
    hotmail007: false,
    outlook_api: false,
    file_mail: false,
    cfmail: false,
    run: false,
    registration: false,
    flow: false,
    oauth: false,
    output: false,
  });

  const sectionCategoryMap: Record<string, ConfigCategory> = {
    priority: "common",
    mail: "mail",
    luckmail: "mail",
    gmail: "mail",
    hotmail007: "mail",
    outlook_api: "mail",
    file_mail: "mail",
    cfmail: "mail",
    run: "advanced",
    registration: "advanced",
    flow: "advanced",
    oauth: "advanced",
    output: "advanced",
  };

  const categoryLabelMap: Record<ConfigCategory, string> = {
    common: "常用",
    mail: "邮箱",
    advanced: "高级",
  };

  const selectedProvider =
    sections.find((section) => section.key === "mail")?.fields.find((field) => field.key === "provider")?.value ??
    "luckmail";

  const providerLabelMap: Record<string, string> = {
    luckmail: "LuckMail",
    gmail: "Gmail",
    hotmail007: "Hotmail007",
    outlook_api: "淘宝邮箱",
    file: "邮箱文件",
    cf: "Cloudflare",
  };

  const visibleSections = sections.filter((section) => {
    if (sectionCategoryMap[section.key] !== activeCategory) {
      return false;
    }
    if (section.key === "luckmail") return selectedProvider === "luckmail";
    if (section.key === "gmail") return selectedProvider === "gmail";
    if (section.key === "hotmail007") return selectedProvider === "hotmail007";
    if (section.key === "outlook_api") return selectedProvider === "outlook_api";
    if (section.key === "file_mail") return selectedProvider === "file";
    if (section.key === "cfmail") return selectedProvider === "cf";
    return true;
  });

  const summaryItems = [
    {
      label: "当前邮箱",
      value: providerLabelMap[String(selectedProvider)] ?? String(selectedProvider),
    },
    {
      label: "号池数量",
      value: String(
        Math.max(
          1,
          String(
            sections
              .find((section) => section.key === "priority")
              ?.fields.find((field) => field.key === "cpa_pools")
              ?.value || "",
          )
            .split(/\r?\n/)
            .filter((item) => item.trim()).length,
        ),
      ),
    },
    {
      label: "补号并发",
      value: String(
        sections.find((section) => section.key === "run")?.fields.find((field) => field.key === "workers")?.value ?? "",
      ),
    },
  ];

  const toggleSection = (sectionKey: string) => {
    setExpandedSections((current) => ({
      ...current,
      [sectionKey]: !(current[sectionKey] ?? false),
    }));
  };

  useEffect(() => {
    if (
      selectedProvider === "luckmail" ||
      selectedProvider === "gmail" ||
      selectedProvider === "hotmail007" ||
      selectedProvider === "outlook_api" ||
      selectedProvider === "file" ||
      selectedProvider === "cf"
    ) {
      const sectionKey =
        selectedProvider === "file"
          ? "file_mail"
          : selectedProvider === "cf"
            ? "cfmail"
            : String(selectedProvider);
      setExpandedSections((current) => ({
        ...current,
        [sectionKey]: true,
      }));
    }
  }, [selectedProvider]);

  useEffect(() => {
    if (activeCategory === "mail") {
      return;
    }

    if (
      selectedProvider === "luckmail" ||
      selectedProvider === "gmail" ||
      selectedProvider === "hotmail007" ||
      selectedProvider === "outlook_api" ||
      selectedProvider === "file" ||
      selectedProvider === "cf"
    ) {
      const sectionKey =
        selectedProvider === "file"
          ? "file_mail"
          : selectedProvider === "cf"
            ? "cfmail"
            : String(selectedProvider);
      setExpandedSections((current) => ({
        ...current,
        [sectionKey]: true,
      }));
    }
  }, [activeCategory, selectedProvider]);

  return (
    <aside class="card settings-card">
      <div class="card-head">
        <div class="card-title">
          <span class="title-icon">📝</span>
          <span>维护配置</span>
        </div>
        {hasStoredToken ? (
          <button class="link-button" type="button" onClick={onLogout}>
            退出登录
          </button>
        ) : null}
      </div>

      <div class="settings-body">
        <div class="settings-summary">
          {summaryItems.map((item) => (
            <div class="summary-chip" key={item.label}>
              <span class="summary-label">{item.label}</span>
              <span class="summary-value">{item.value}</span>
            </div>
          ))}
        </div>

        <div class="settings-tabs">
          {(Object.keys(categoryLabelMap) as ConfigCategory[]).map((category) => (
            <button
              key={category}
              type="button"
              class={`settings-tab${activeCategory === category ? " active" : ""}`}
              onClick={() => setActiveCategory(category)}
            >
              {categoryLabelMap[category]}
            </button>
          ))}
        </div>

        {visibleSections.map((section) => {
          const isExpanded = expandedSections[section.key] ?? false;

          return (
            <section class={`config-group${isExpanded ? " expanded" : ""}`} key={section.key}>
              <button class="group-toggle" type="button" onClick={() => toggleSection(section.key)}>
                <span class="group-title">
                  {section.label}
                  <span class="group-key">{section.key}</span>
                </span>
                <span class={`group-caret${isExpanded ? " open" : ""}`}>⌄</span>
              </button>

              {isExpanded ? (
                <div class="group-content field-row single-col">
                  {section.fields.map((field) => (
                    <label class={`field${field.type === "checkbox" ? " checkbox-group compact" : ""}`} key={field.key}>
                      {field.type !== "checkbox" ? (
                        <span class="field-label">
                          {field.label}
                          <span class="field-key">{field.key}</span>
                        </span>
                      ) : null}
                      <FieldControl sectionKey={section.key} field={field} onValueChange={onValueChange} />
                      {field.hint ? <span class="field-hint">{field.hint}</span> : null}
                    </label>
                  ))}
                </div>
              ) : null}
            </section>
          );
        })}

        <TaobaoPoolManager visible={String(selectedProvider) === "outlook_api"} />

        <div class="settings-actions">
          <button class="button primary" type="button" onClick={onStart} disabled={busy || running}>
            开始维护
          </button>
          <button class="button primary" type="button" onClick={onStartLoop} disabled={busy || running}>
            {loopRunning ? "循环补号运行中" : "循环补号"}
          </button>
          <button class="button warning" type="button" onClick={onStop} disabled={busy || !running}>
            停止维护
          </button>
          <button class="button secondary" type="button" onClick={onSave} disabled={busy}>
            保存配置
          </button>
        </div>
      </div>
    </aside>
  );
}
