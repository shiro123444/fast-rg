# GPT 注册机 OSS 修复版

这是一个“大工程移植版”：

- 前端面板和运行形态参考 `OSS修复3.30`（配置面板 + 监控台 + 循环补号）
- 注册核心逻辑使用当前仓库的 `gpt.py`（保留你现有 luckmail/gmail/hotmail007/file/cf 注册能力）
- 后端提供 OSS 风格 API：`api_server.py`
- 自动补号执行器：`auto_pool_maintainer.py`

## 目录结构

- `gpt.py`: 你的核心注册逻辑（未替换）
- `api_server.py`: 控制台后端 API（含管理令牌鉴权）
- `auto_pool_maintainer.py`: 号池维护/循环补号调度
- `config.json`: 当前运行配置
- `config.example.json`: 配置模板
- `frontend/`: OSS 风格前端（Preact + Vite）
- `dev_services.sh`: 一键启动前后端

## 支持的邮箱模式

在前端 `mail.provider` 可选：

- `luckmail`
- `gmail`
- `hotmail007`
- `file`
- `cf`

这些模式最终都映射到 `gpt.py --email-mode ...`，确保注册流程仍然走你的原始逻辑。

## 多 CPA 号池（已支持）

- 在前端的“核心配置 -> CPA号池列表”里可添加多个号池
- 每个号池可设置：名称、CPA地址、访问令牌、target_type、最小阈值、是否启用
- 循环补号模式会按“号池列表顺序”逐个维护，单轮内顺序执行，避免同时打爆风控
- 每个号池支持“测试连接”按钮，可即时检查 candidates/total

格式示例（每行一个）：

```text
name=main;base_url=http://206.189.45.0:8317;token=xxxx;target_type=codex;min_candidates=50;enabled=1
name=backup;base_url=http://1.2.3.4:8317;token=yyyy;target_type=codex;min_candidates=20;enabled=1
```

## 自动清理 401（已支持）

每轮补号开始前，维护器会先执行清理探测：

- 通过 `v0/management/api-call` 探测账号可用性
- `401` 失效账号自动删除
- `used_percent` 超阈值账号自动禁用
- 健康且禁用账号自动恢复启用

然后再计算缺口并补号。

## 快速启动

1) 安装依赖

```bash
pip install curl_cffi
cd frontend
pnpm install
pnpm build
cd ..
```

2) 初始化配置（避免提交敏感信息）

```bash
cp .env.example .env
cp config.example.json config.json
```

3) 启动后端 + 前端开发服务

```bash
chmod +x dev_services.sh
./dev_services.sh fg
```

默认地址：

- 后端 API: `http://127.0.0.1:8318`
- 前端 dev: `http://127.0.0.1:8173`

也可以只跑后端（直接使用打包后的前端静态文件）：

```bash
python api_server.py
```

打开：`http://127.0.0.1:8318`

## 管理令牌登录

首次启动 `api_server.py` 会生成：

- `admin_token.txt`

把文件内 token 粘贴到前端登录框。

## 运行机制（重要）

- `api_server.py` 提供：
  - `POST /api/runtime/start` 单次维护
  - `POST /api/runtime/start-loop` 循环补号
  - `POST /api/runtime/stop` 停止维护
  - `GET /api/runtime/status` 实时状态和日志解析
- `auto_pool_maintainer.py` 执行流程：
  1. 查询 CPA 候选号数量
  2. 低于阈值则计算差额
  3. 调起 `gpt.py --count 差额 --threads N ...`
  4. 输出 OSS 风格日志（前端直接解析展示）

## 配置说明

核心配置在 `config.json`：

- `clean.base_url` / `clean.token`: CPA 管理接口地址和密钥
- `maintainer.min_candidates`: 最小候选号池
- `maintainer.loop_interval_seconds`: 循环补号间隔
- `run.workers`: 补号时传给 `gpt.py` 的并发线程
- `run.proxy` / `run.proxy_file`: 代理
- `mail.provider`: 邮箱模式
- `luckmail/gmail/hotmail007/file_mail/cfmail`: 对应模式参数

## 说明

- 这个版本的“自动维护与循环补号”已经接入你的 `gpt.py`。
- 前端已做适配，支持 luckmail/gmail 等你要求的模式。
- 若你需要我继续做“与旧 OSS 完全字段一比一兼容”（包括所有旧 provider 字段保留映射），我可以在此基础上再做一轮兼容层。
