import type { BackendConfig } from "../types/api";
import type { ConfigSection } from "../types/runtime";

export const defaultBackendConfig: BackendConfig = {
  cpa_pools: [
    {
      name: "default",
      base_url: "http://127.0.0.1:8317",
      token: "",
      target_type: "codex",
      min_candidates: 50,
      enabled: true,
    },
  ],
  cfmail: {
    domain: "",
    worker_base: "",
    admin_password: "",
  },
  clean: {
    base_url: "http://127.0.0.1:8317",
    token: "",
    target_type: "codex",
    workers: 20,
    sample_size: 0,
    delete_workers: 20,
    timeout: 10,
    retries: 1,
    user_agent: "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal",
    used_percent_threshold: 95,
  },
  mail: {
    provider: "luckmail",
    otp_timeout_seconds: 120,
    poll_interval_seconds: 3,
  },
  luckmail: {
    sdk_path: "/home/shiro/文档/codex/tools/auto_reg/core",
    api_base: "https://mails.luckyous.com",
    api_key: "",
    project_code: "openai",
    email_type: "ms_imap",
    domain: "hotmail.com",
  },
  gmail: {
    base: "",
  },
  hotmail007: {
    api_url: "https://gapi.hotmail007.com",
    api_key: "",
    mail_type: "outlook Trusted Graph",
    mail_mode: "imap",
  },
  file_mail: {
    accounts_file: "accounts.txt",
  },
  maintainer: {
    min_candidates: 50,
    loop_interval_seconds: 60,
  },
  run: {
    workers: 4,
    proxy: "",
    proxy_file: "",
    failure_threshold_for_cooldown: 5,
    failure_cooldown_seconds: 45,
    loop_jitter_min_seconds: 2,
    loop_jitter_max_seconds: 6,
  },
  flow: {
    step_retry_attempts: 2,
    step_retry_delay_base: 0.2,
    step_retry_delay_cap: 0.8,
    outer_retry_attempts: 3,
    oauth_local_retry_attempts: 3,
    transient_markers:
      "sentinel_,oauth_authorization_code_not_found,headers_failed,timeout,timed out,server disconnected,unexpected_eof_while_reading,transport,remoteprotocolerror,connection reset,temporarily unavailable,network,eof occurred,http_429,http_500,http_502,http_503,http_504",
    register_otp_validate_order: "normal,sentinel",
    oauth_otp_validate_order: "normal,sentinel",
    oauth_password_phone_action: "warn_and_continue",
    oauth_otp_phone_action: "warn_and_continue",
  },
  registration: {
    entry_mode: "chatgpt_web",
    entry_mode_fallback: true,
    chatgpt_base: "https://chatgpt.com",
    register_create_account_phone_action: "warn_and_continue",
    phone_verification_markers:
      "add_phone,/add-phone,phone_verification,phone-verification,phone/verify",
  },
  oauth: {
    issuer: "https://auth.openai.com",
    client_id: "app_EMoamEEZ73f0CkXaXp7hrann",
    redirect_uri: "http://localhost:1455/auth/callback",
    retry_attempts: 3,
    retry_backoff_base: 2,
    retry_backoff_max: 15,
    otp_timeout_seconds: 120,
    otp_poll_interval_seconds: 2,
  },
  output: {
    accounts_file: "accounts.txt",
    csv_file: "registered_accounts.csv",
    ak_file: "ak.txt",
    rk_file: "rk.txt",
    save_local: false,
  },
};

function toNumber(value: unknown, fallback: number): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function toString(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function toBoolean(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

export function normalizeBackendConfig(
  raw: Partial<BackendConfig> | Record<string, unknown>,
): BackendConfig {
  const source = raw ?? {};
  const cpaPoolsRaw = Array.isArray((source as Record<string, unknown>).cpa_pools)
    ? ((source as Record<string, unknown>).cpa_pools as unknown[])
    : [];
  const cpaPools = cpaPoolsRaw
    .map((item, idx) => {
      const pool = (item ?? {}) as Record<string, unknown>;
      const name = String(pool.name ?? `pool-${idx + 1}`).trim();
      const base_url = String(pool.base_url ?? "").trim();
      const token = String(pool.token ?? "").trim();
      const target_type = String(pool.target_type ?? "codex").trim() || "codex";
      const min_candidates = toNumber(pool.min_candidates, 50);
      const enabled = typeof pool.enabled === "boolean" ? pool.enabled : true;
      if (!name || !base_url) {
        return null;
      }
      return { name, base_url, token, target_type, min_candidates, enabled };
    })
    .filter(Boolean) as BackendConfig["cpa_pools"];

  const cfmail = (source.cfmail ?? {}) as Partial<BackendConfig["cfmail"]>;
  const clean = (source.clean ?? {}) as Partial<BackendConfig["clean"]>;
  const mail = (source.mail ?? {}) as Partial<BackendConfig["mail"]>;
  const luckmail = (source.luckmail ?? {}) as Partial<BackendConfig["luckmail"]>;
  const gmail = (source.gmail ?? {}) as Partial<BackendConfig["gmail"]>;
  const hotmail007 = (source.hotmail007 ?? {}) as Partial<BackendConfig["hotmail007"]>;
  const fileMail = (source.file_mail ?? {}) as Partial<BackendConfig["file_mail"]>;
  const maintainer = (source.maintainer ?? {}) as Partial<BackendConfig["maintainer"]>;
  const run = (source.run ?? {}) as Partial<BackendConfig["run"]>;
  const flow = (source.flow ?? {}) as Partial<BackendConfig["flow"]>;
  const registration = (source.registration ?? {}) as Partial<BackendConfig["registration"]>;
  const oauth = (source.oauth ?? {}) as Partial<BackendConfig["oauth"]>;
  const output = (source.output ?? {}) as Partial<BackendConfig["output"]>;

  return {
    cpa_pools: cpaPools.length ? cpaPools : defaultBackendConfig.cpa_pools,
    cfmail: {
      domain: toString(cfmail.domain, defaultBackendConfig.cfmail.domain),
      worker_base: toString(cfmail.worker_base, defaultBackendConfig.cfmail.worker_base),
      admin_password: toString(cfmail.admin_password, defaultBackendConfig.cfmail.admin_password),
    },
    clean: {
      base_url: toString(clean.base_url, defaultBackendConfig.clean.base_url),
      token: toString(clean.token, defaultBackendConfig.clean.token),
      target_type: toString(clean.target_type, defaultBackendConfig.clean.target_type),
      workers: toNumber(clean.workers, defaultBackendConfig.clean.workers),
      sample_size: toNumber(clean.sample_size, defaultBackendConfig.clean.sample_size),
      delete_workers: toNumber(clean.delete_workers, defaultBackendConfig.clean.delete_workers),
      timeout: toNumber(clean.timeout, defaultBackendConfig.clean.timeout),
      retries: toNumber(clean.retries, defaultBackendConfig.clean.retries),
      user_agent: toString(clean.user_agent, defaultBackendConfig.clean.user_agent ?? ""),
      used_percent_threshold: toNumber(
        clean.used_percent_threshold,
        defaultBackendConfig.clean.used_percent_threshold,
      ),
    },
    mail: {
      provider: toString(mail.provider, defaultBackendConfig.mail.provider),
      otp_timeout_seconds: toNumber(
        mail.otp_timeout_seconds,
        defaultBackendConfig.mail.otp_timeout_seconds,
      ),
      poll_interval_seconds: toNumber(
        mail.poll_interval_seconds,
        defaultBackendConfig.mail.poll_interval_seconds,
      ),
    },
    luckmail: {
      sdk_path: toString(luckmail.sdk_path, defaultBackendConfig.luckmail.sdk_path),
      api_base: toString(luckmail.api_base, defaultBackendConfig.luckmail.api_base),
      api_key: toString(luckmail.api_key, defaultBackendConfig.luckmail.api_key),
      project_code: toString(luckmail.project_code, defaultBackendConfig.luckmail.project_code),
      email_type: toString(luckmail.email_type, defaultBackendConfig.luckmail.email_type),
      domain: toString(luckmail.domain, defaultBackendConfig.luckmail.domain),
    },
    gmail: {
      base: toString(gmail.base, defaultBackendConfig.gmail.base),
    },
    hotmail007: {
      api_url: toString(hotmail007.api_url, defaultBackendConfig.hotmail007.api_url),
      api_key: toString(hotmail007.api_key, defaultBackendConfig.hotmail007.api_key),
      mail_type: toString(hotmail007.mail_type, defaultBackendConfig.hotmail007.mail_type),
      mail_mode: toString(hotmail007.mail_mode, defaultBackendConfig.hotmail007.mail_mode),
    },
    file_mail: {
      accounts_file: toString(fileMail.accounts_file, defaultBackendConfig.file_mail.accounts_file),
    },
    maintainer: {
      min_candidates: toNumber(maintainer.min_candidates, defaultBackendConfig.maintainer.min_candidates),
      loop_interval_seconds: toNumber(
        maintainer.loop_interval_seconds,
        defaultBackendConfig.maintainer.loop_interval_seconds,
      ),
    },
    run: {
      workers: toNumber(run.workers, defaultBackendConfig.run.workers),
      proxy: toString(run.proxy, defaultBackendConfig.run.proxy),
      proxy_file: toString(run.proxy_file, defaultBackendConfig.run.proxy_file),
      failure_threshold_for_cooldown: toNumber(
        run.failure_threshold_for_cooldown,
        defaultBackendConfig.run.failure_threshold_for_cooldown,
      ),
      failure_cooldown_seconds: toNumber(
        run.failure_cooldown_seconds,
        defaultBackendConfig.run.failure_cooldown_seconds,
      ),
      loop_jitter_min_seconds: toNumber(
        run.loop_jitter_min_seconds,
        defaultBackendConfig.run.loop_jitter_min_seconds,
      ),
      loop_jitter_max_seconds: toNumber(
        run.loop_jitter_max_seconds,
        defaultBackendConfig.run.loop_jitter_max_seconds,
      ),
    },
    flow: {
      step_retry_attempts: toNumber(flow.step_retry_attempts, defaultBackendConfig.flow.step_retry_attempts),
      step_retry_delay_base: toNumber(
        flow.step_retry_delay_base,
        defaultBackendConfig.flow.step_retry_delay_base,
      ),
      step_retry_delay_cap: toNumber(flow.step_retry_delay_cap, defaultBackendConfig.flow.step_retry_delay_cap),
      outer_retry_attempts: toNumber(flow.outer_retry_attempts, defaultBackendConfig.flow.outer_retry_attempts),
      oauth_local_retry_attempts: toNumber(
        flow.oauth_local_retry_attempts,
        defaultBackendConfig.flow.oauth_local_retry_attempts,
      ),
      transient_markers: toString(flow.transient_markers, defaultBackendConfig.flow.transient_markers),
      register_otp_validate_order: toString(
        flow.register_otp_validate_order,
        defaultBackendConfig.flow.register_otp_validate_order,
      ),
      oauth_otp_validate_order: toString(
        flow.oauth_otp_validate_order,
        defaultBackendConfig.flow.oauth_otp_validate_order,
      ),
      oauth_password_phone_action: toString(
        flow.oauth_password_phone_action,
        defaultBackendConfig.flow.oauth_password_phone_action,
      ),
      oauth_otp_phone_action: toString(
        flow.oauth_otp_phone_action,
        defaultBackendConfig.flow.oauth_otp_phone_action,
      ),
    },
    registration: {
      entry_mode: toString(registration.entry_mode, defaultBackendConfig.registration.entry_mode),
      entry_mode_fallback: toBoolean(
        registration.entry_mode_fallback,
        defaultBackendConfig.registration.entry_mode_fallback,
      ),
      chatgpt_base: toString(registration.chatgpt_base, defaultBackendConfig.registration.chatgpt_base),
      register_create_account_phone_action: toString(
        registration.register_create_account_phone_action,
        defaultBackendConfig.registration.register_create_account_phone_action,
      ),
      phone_verification_markers: toString(
        registration.phone_verification_markers,
        defaultBackendConfig.registration.phone_verification_markers,
      ),
    },
    oauth: {
      issuer: toString(oauth.issuer, defaultBackendConfig.oauth.issuer),
      client_id: toString(oauth.client_id, defaultBackendConfig.oauth.client_id),
      redirect_uri: toString(oauth.redirect_uri, defaultBackendConfig.oauth.redirect_uri),
      retry_attempts: toNumber(oauth.retry_attempts, defaultBackendConfig.oauth.retry_attempts),
      retry_backoff_base: toNumber(
        oauth.retry_backoff_base,
        defaultBackendConfig.oauth.retry_backoff_base,
      ),
      retry_backoff_max: toNumber(oauth.retry_backoff_max, defaultBackendConfig.oauth.retry_backoff_max),
      otp_timeout_seconds: toNumber(
        oauth.otp_timeout_seconds,
        defaultBackendConfig.oauth.otp_timeout_seconds,
      ),
      otp_poll_interval_seconds: toNumber(
        oauth.otp_poll_interval_seconds,
        defaultBackendConfig.oauth.otp_poll_interval_seconds,
      ),
    },
    output: {
      accounts_file: toString(output.accounts_file, defaultBackendConfig.output.accounts_file),
      csv_file: toString(output.csv_file, defaultBackendConfig.output.csv_file),
      ak_file: toString(output.ak_file, defaultBackendConfig.output.ak_file),
      rk_file: toString(output.rk_file, defaultBackendConfig.output.rk_file),
      save_local: toBoolean(output.save_local, defaultBackendConfig.output.save_local),
    },
  };
}

export function configToSections(config: BackendConfig): ConfigSection[] {
  const poolText = config.cpa_pools
    .map(
      (pool) =>
        [
          `name=${pool.name}`,
          `base_url=${pool.base_url}`,
          `token=${pool.token || ""}`,
          `target_type=${pool.target_type || "codex"}`,
          `min_candidates=${pool.min_candidates}`,
          `enabled=${pool.enabled ? "1" : "0"}`,
        ].join(";"),
    )
    .join("\n");

  return [
    {
      key: "priority",
      label: "核心配置",
      fields: [
        {
          key: "cpa_pools",
          label: "CPA号池列表",
          type: "textarea",
          value: poolText,
          hint: "每行一个号池: name=xxx;base_url=http://...;token=...;target_type=codex;min_candidates=50;enabled=1",
        },
        {
          key: "loop_interval_seconds",
          label: "循环检查间隔(秒)",
          type: "number",
          value: config.maintainer.loop_interval_seconds,
        },
        { key: "proxy", label: "单代理", type: "text", value: config.run.proxy },
        { key: "proxy_file", label: "代理文件", type: "text", value: config.run.proxy_file },
      ],
    },
    {
      key: "mail",
      label: "邮箱模式",
      columns: 2,
      fields: [
        {
          key: "provider",
          label: "邮箱提供方",
          type: "select",
          value: config.mail.provider,
          options: [
            { label: "luckmail", value: "luckmail" },
            { label: "gmail", value: "gmail" },
            { label: "hotmail007", value: "hotmail007" },
            { label: "file", value: "file" },
            { label: "cf", value: "cf" },
          ],
        },
        { key: "otp_timeout_seconds", label: "验证码超时", type: "number", value: config.mail.otp_timeout_seconds },
        {
          key: "poll_interval_seconds",
          label: "轮询间隔",
          type: "number",
          value: config.mail.poll_interval_seconds,
        },
      ],
    },
    {
      key: "luckmail",
      label: "LuckMail 配置",
      columns: 2,
      fields: [
        { key: "sdk_path", label: "SDK路径", type: "text", value: config.luckmail.sdk_path },
        { key: "api_base", label: "API地址", type: "text", value: config.luckmail.api_base },
        { key: "api_key", label: "API Key", type: "password", value: config.luckmail.api_key, sensitive: true },
        { key: "project_code", label: "项目代号", type: "text", value: config.luckmail.project_code },
        { key: "email_type", label: "邮箱类型", type: "text", value: config.luckmail.email_type },
        { key: "domain", label: "域名", type: "text", value: config.luckmail.domain },
      ],
    },
    {
      key: "gmail",
      label: "Gmail 配置",
      fields: [{ key: "base", label: "Gmail Base", type: "text", value: config.gmail.base }],
    },
    {
      key: "hotmail007",
      label: "Hotmail007 配置",
      columns: 2,
      fields: [
        { key: "api_url", label: "API地址", type: "text", value: config.hotmail007.api_url },
        { key: "api_key", label: "API Key", type: "password", value: config.hotmail007.api_key, sensitive: true },
        { key: "mail_type", label: "邮箱类型", type: "text", value: config.hotmail007.mail_type },
        {
          key: "mail_mode",
          label: "收信模式",
          type: "select",
          value: config.hotmail007.mail_mode,
          options: [
            { label: "imap", value: "imap" },
            { label: "graph", value: "graph" },
          ],
        },
      ],
    },
    {
      key: "file_mail",
      label: "文件邮箱配置",
      fields: [
        { key: "accounts_file", label: "邮箱文件", type: "text", value: config.file_mail.accounts_file },
      ],
    },
    {
      key: "cfmail",
      label: "Cloudflare 邮箱配置",
      columns: 2,
      fields: [
        { key: "domain", label: "域名", type: "text", value: config.cfmail.domain },
        { key: "worker_base", label: "Worker地址", type: "text", value: config.cfmail.worker_base },
        {
          key: "admin_password",
          label: "管理密码",
          type: "password",
          value: config.cfmail.admin_password,
          sensitive: true,
        },
      ],
    },
    {
      key: "run",
      label: "运行参数",
      columns: 2,
      fields: [
        { key: "workers", label: "并发线程", type: "number", value: config.run.workers },
        {
          key: "failure_threshold_for_cooldown",
          label: "失败阈值",
          type: "number",
          value: config.run.failure_threshold_for_cooldown,
        },
        {
          key: "failure_cooldown_seconds",
          label: "冷却秒数",
          type: "number",
          value: config.run.failure_cooldown_seconds,
        },
        {
          key: "loop_jitter_min_seconds",
          label: "循环抖动最小",
          type: "number",
          value: config.run.loop_jitter_min_seconds,
        },
        {
          key: "loop_jitter_max_seconds",
          label: "循环抖动最大",
          type: "number",
          value: config.run.loop_jitter_max_seconds,
        },
      ],
    },
    {
      key: "flow",
      label: "流程策略",
      columns: 2,
      fields: [
        { key: "step_retry_attempts", label: "步骤重试", type: "number", value: config.flow.step_retry_attempts },
        {
          key: "step_retry_delay_base",
          label: "重试基数",
          type: "number",
          value: config.flow.step_retry_delay_base,
        },
        { key: "step_retry_delay_cap", label: "重试上限", type: "number", value: config.flow.step_retry_delay_cap },
        {
          key: "outer_retry_attempts",
          label: "外层重试",
          type: "number",
          value: config.flow.outer_retry_attempts,
        },
        {
          key: "oauth_local_retry_attempts",
          label: "OAuth局部重试",
          type: "number",
          value: config.flow.oauth_local_retry_attempts,
        },
        {
          key: "register_otp_validate_order",
          label: "注册OTP顺序",
          type: "text",
          value: config.flow.register_otp_validate_order,
        },
        {
          key: "oauth_otp_validate_order",
          label: "OAuth OTP顺序",
          type: "text",
          value: config.flow.oauth_otp_validate_order,
        },
        {
          key: "oauth_password_phone_action",
          label: "OAuth密码阶段手机验证",
          type: "text",
          value: config.flow.oauth_password_phone_action,
        },
        {
          key: "oauth_otp_phone_action",
          label: "OAuth OTP阶段手机验证",
          type: "text",
          value: config.flow.oauth_otp_phone_action,
        },
        {
          key: "transient_markers",
          label: "瞬时错误关键词",
          type: "text",
          value: config.flow.transient_markers,
        },
      ],
    },
    {
      key: "registration",
      label: "注册策略",
      columns: 2,
      fields: [
        { key: "entry_mode", label: "入口模式", type: "text", value: config.registration.entry_mode },
        {
          key: "entry_mode_fallback",
          label: "入口回退",
          type: "checkbox",
          value: config.registration.entry_mode_fallback,
        },
        { key: "chatgpt_base", label: "ChatGPT基址", type: "text", value: config.registration.chatgpt_base },
        {
          key: "register_create_account_phone_action",
          label: "命中手机验证策略",
          type: "text",
          value: config.registration.register_create_account_phone_action,
        },
        {
          key: "phone_verification_markers",
          label: "手机验证关键词",
          type: "text",
          value: config.registration.phone_verification_markers,
        },
      ],
    },
    {
      key: "oauth",
      label: "OAuth 配置",
      columns: 2,
      fields: [
        { key: "issuer", label: "Issuer", type: "text", value: config.oauth.issuer },
        { key: "client_id", label: "Client ID", type: "text", value: config.oauth.client_id },
        { key: "redirect_uri", label: "Redirect URI", type: "text", value: config.oauth.redirect_uri },
        { key: "retry_attempts", label: "重试次数", type: "number", value: config.oauth.retry_attempts },
        {
          key: "retry_backoff_base",
          label: "退避基数",
          type: "number",
          value: config.oauth.retry_backoff_base,
        },
        { key: "retry_backoff_max", label: "退避上限", type: "number", value: config.oauth.retry_backoff_max },
        { key: "otp_timeout_seconds", label: "OTP超时", type: "number", value: config.oauth.otp_timeout_seconds },
        {
          key: "otp_poll_interval_seconds",
          label: "OTP轮询",
          type: "number",
          value: config.oauth.otp_poll_interval_seconds,
        },
      ],
    },
    {
      key: "output",
      label: "输出配置",
      columns: 2,
      fields: [
        { key: "accounts_file", label: "账号文件", type: "text", value: config.output.accounts_file },
        { key: "csv_file", label: "CSV文件", type: "text", value: config.output.csv_file },
        { key: "ak_file", label: "AK文件", type: "text", value: config.output.ak_file },
        { key: "rk_file", label: "RK文件", type: "text", value: config.output.rk_file },
        { key: "save_local", label: "本地保存", type: "checkbox", value: config.output.save_local },
      ],
    },
  ];
}

function parsePoolLine(line: string) {
  const parts = line
    .split(";")
    .map((item) => item.trim())
    .filter(Boolean);
  const out: Record<string, string> = {};
  for (const part of parts) {
    const idx = part.indexOf("=");
    if (idx < 0) continue;
    const key = part.slice(0, idx).trim();
    const value = part.slice(idx + 1).trim();
    if (!key) continue;
    out[key] = value;
  }
  return out;
}

export function sectionsToConfig(sections: ConfigSection[]): BackendConfig {
  const config = structuredClone(defaultBackendConfig);

  for (const section of sections) {
    if (section.key === "priority") {
      for (const field of section.fields) {
        if (field.key === "loop_interval_seconds") {
          config.maintainer.loop_interval_seconds = Number(field.value);
        } else if (field.key === "proxy") {
          config.run.proxy = String(field.value);
        } else if (field.key === "proxy_file") {
          config.run.proxy_file = String(field.value);
        } else if (field.key === "cpa_pools") {
          const lines = String(field.value)
            .split(/\r?\n/)
            .map((item) => item.trim())
            .filter(Boolean);
          const pools = lines
            .map((line, idx) => {
              const row = parsePoolLine(line);
              const base_url = String(row.base_url ?? "").trim();
              if (!base_url) return null;
              return {
                name: String(row.name ?? `pool-${idx + 1}`).trim() || `pool-${idx + 1}`,
                base_url,
                token: String(row.token ?? "").trim(),
                target_type: String(row.target_type ?? "codex").trim() || "codex",
                min_candidates: Number(row.min_candidates ?? 50) || 50,
                enabled: ["1", "true", "yes", "on"].includes(
                  String(row.enabled ?? "1").toLowerCase(),
                ),
              };
            })
            .filter(Boolean) as BackendConfig["cpa_pools"];
          if (pools.length) {
            config.cpa_pools = pools;
            const firstEnabled = pools.find((item) => item.enabled) || pools[0];
            config.clean.base_url = firstEnabled.base_url;
            config.clean.token = firstEnabled.token;
            config.clean.target_type = firstEnabled.target_type;
            config.maintainer.min_candidates = firstEnabled.min_candidates;
          }
        }
      }
      continue;
    }

    const target = config[section.key as keyof BackendConfig] as
      | Record<string, string | number | boolean>
      | undefined;
    if (!target) {
      continue;
    }
    for (const field of section.fields) {
      target[field.key] = field.value;
    }
  }

  return normalizeBackendConfig(config);
}
