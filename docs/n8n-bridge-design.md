# n8n Bridge 施工方案（V1）

本方案是修订后设计文档的施工化展开：冻结全部已决事项、按依赖排序分阶段、每个工件给出规格与验收标准，不包含代码。后续编码按 Phase 顺序执行，每个 Phase 完成即锁定（测试先行，验收通过才进入下一阶段）。
> **Construction status (this repository)**: Phase 0 (gateway
> scaffold), Phase 1 (minimal core: config/schemas/session/ambient/
> call-plane), Phase 3 (task plane), Phase 4 (HITL plane), Phase 5
> (examples/gateway_n8n reference registry + compose demo), Phase 7
> (composites) are COMPLETE and test-locked. Phase 2 (M1 spike, n8n
> repository) and Phase 6 (n8n nodes + real-endpoint E2E) are pending
> and live in the separate n8n-nodes-ordigovernance repository.
---

## 0. 冻结决策表

以下决策已闭合，施工中不再讨论，变更需回到设计评审：

| # | 决策 | 内容 |
|---|---|---|
| D1 | upstream 语义 | `submit_task` 对每个 `u ∈ upstream` 写 dependency edge（方向：child=本任务，parent=u，V1 全部 is_primary=True）。upstream 是**证据/图谱**，不是调度；调度由 n8n 自身承担，gateway 不引入 register/notify/wait_ready 链 |
| D2 | 无 run 调用的归宿 | gateway 启动时开启一个 **ambient run**（run_id 固定 `"ambient"`，永不 finish，shutdown 时 teardown），无 run_id 的 call-plane 请求路由进去。ambient 不计入"单一活跃 run" |
| D3 | vocabulary 端点 | `GET /runs/{id}/vocabulary` 列入正式 API spec，供 n8n 节点下拉填充 |
| D4 | 默认派生 | `budget_scope` 省略时按 run 派生唯一值；trace 布局固定 `trace_root/{run_id}/trace`（parent 为 run 专属目录，配合 build_run_context 的清理语义）；RunsRegistry 注册时直接携带真实 scope，不走 backfill |
| D5 | finish 语义 | 会话内任一已提交任务非终态 → 409 并列出任务清单；全部终态才 teardown + 登记 finish |
| D6 | registry 加载与自检 | entry points + 配置模块双通道；启动时对每个加载到的工厂做禁限命名空间自检（orditect_components / ordienterprise），命中则拒绝启动并列出违规者 |
| D7 | 重复 task_id | 同 run 内重复提交 → 一律 409；重跑走 HITL retry，不开第二通道 |
| D8 | call-plane 身份 | run 内已存在任务 → 读热记录取当前 eid；任务不存在 → 404；未提供 task_id → 临时铸造。seq 由会话按 (task_id, purpose) 单调分配，起始 SEQ_AGENT_BASE+1（agent band 语义） |
| D9 | 工具入参形态 | inputs 以 kwargs 形态传给 handler；命中 `_RESERVED_PAYLOAD_KEYS` → 422 并给出改名指引（与 runtime atoms 的报错文案同源） |
| D10 | composite 形态 | V1 为 **drive 级后台驱动**：注册表第三张表 composites，`POST /runs/{id}/composites` 启动，`GET .../composites/{cid}` 轮询；子任务挂 run root，证据经子任务闭合。supervisor-node 包装（pitfall 4/13.1 约束）明确推迟 V2 |
| D11 | n8n 多 item 纪律 | Task 节点对每个 item 各执行一次；求值后 task_id 冲突时自动追加 item 序号后缀，并在文档显式说明 |
| D12 | LLM 装配注入缝 | settings 暴露 make_clients 覆盖钩子：默认装配真实 GovernedLLMClient registry，测试注入脚本化客户端（gateway 测试不依赖真实端点） |
| D13 | memo body 默认实现 | gateway 自带默认 memory_read/write handler 对：redis 模式落 redis（独立 key 前缀），memory 模式落进程字典；均可经 registry/config 覆盖。不依赖 testing 包的 mock（生产路径不用测试夹具） |
| D14 | 历史读取边界 | gateway 只服务活跃 run 的热读取；历史 run 的证据读取走 viewer 冷路径（viewer 指向同一 trace_root），不在 gateway 开历史端点 |

---

## 1. 交付物与边界

| 交付物 | 位置 | 许可 | 说明 |
|---|---|---|---|
| `ordigovernance-gateway` | 本仓库 `packages/ordigovernance-gateway` | SUL-1.0 | HTTP 执行前端，唯一集成点 |
| `n8n-nodes-ordigovernance` | 独立仓库 | MIT | 低代码自定义节点，永不 import 任何 engine 包 |
| `examples/gateway_n8n/` | 本仓库 `examples/` | SUL-1.0 | 参考 registry 实现 + docker compose 演示 |

边界纪律：gateway 源码不得 import `examples`（import 边界门自动覆盖 `packages/*/src`）；参考实现经 entry points / 配置模块在**部署时**注入；n8n 仓库只讲 HTTP。

---

## 2. 总体施工顺序

```
Phase 0  脚手架（gateway 包 + n8n 仓库骨架 + run_tests.sh 注册）
Phase 1  gateway 最小核（config/schemas/session/ambient/call-plane/app）+ 单测
Phase 2  M1 spike：n8n TrackedChatModel 打通 ──────── 存在性关口（失败即停）
Phase 3  gateway task plane（registry/session.submit/runs 路由）+ 单测
Phase 4  gateway HITL plane + 单测
Phase 5  examples/gateway_n8n 参考实现 + compose 演示环境
Phase 6  n8n 其余节点（Run/Tool/Task/Approval）+ 真实端点端到端（M2/M3/M4）
Phase 7  composites + Composite 节点（M5）
Phase 8  文档收口、pitfalls 增补、验收矩阵全跑、发布卫生
```

并行性：Phase 3/4 与 Phase 5 可并行；Phase 6 的 Run/Tool 节点可在 Phase 3 完成后先行（Task 节点依赖 Phase 5 的参考 impl 才有真实可跑对象）。Phase 7 仅依赖 Phase 3。

---

## 3. Phase 0 — 脚手架

**目标**：两个空壳就位，CI 能看见它们。

工件：

1. `packages/ordigovernance-gateway/pyproject.toml`：PEP 420 命名空间 `ordigovernance.gateway`；依赖 `ordigovernance-runtime`、`ordigovernance-bridges-direct`、`fastapi`、`uvicorn`、`pydantic>=2`；可选 extras：`memory = ["ordigovernance-testing"]`（内存热路径模式）、`dev`（pytest、pytest-asyncio、httpx）；`py.typed`。
2. `packages/ordigovernance-gateway/README.md`：定位段 + 设计文档链接。
3. `run_tests.sh` 的 `SUITES` 追加 `packages/ordigovernance-gateway/tests`。
4. n8n 仓库骨架：`package.json`（n8n community node 约定：nodes/credentials 注册数组、构建脚本）、`tsconfig`、目录骨架（credentials/、nodes/）、README 放治理披露原文。
5. 根 README 的包表追加 gateway 行；`docs/n8n-bridge-design.md` 顶部加注"施工以本方案为准"。

peer 依赖纪律（写入 n8n 仓库 README 与 package.json）：`@langchain/core` 声明为 peerDependency，版本区间与目标 n8n 内置版本严格对齐，防止双份类定义导致 `instanceof BaseChatModel` 静默失败。

**验收**：`pip install -e "./packages/ordigovernance-gateway[dev,memory]" --config-settings editable_mode=compat` 成功；`./run_tests.sh gateway` 空套件通过；import 边界门绿。

---

## 4. Phase 1 — gateway 最小核（M1 的后端）

**目标**：提供恰好够 M1 spike 的 HTTP 面：call-plane 两个端点 + ambient run。

### 4.1 目录结构

```
packages/ordigovernance-gateway/
  pyproject.toml  README.md
  src/ordigovernance/gateway/
    __init__.py  py.typed
    app.py         # build_app(settings) 工厂 + lifespan（热路径、ambient、registry 装载与自检；shutdown 逆序 teardown）
    config.py      # GatewaySettings + .env 装载（内置解析器，启动打印来源/键数，pitfall 15.1）
    schemas.py     # 全部请求/响应模型（pydantic v2）
    session.py     # RunSession、AmbientRun、SessionManager（单活跃守卫）
    registry.py    # 本阶段只要空壳：loading + 自检 + 校验，task plane 在 Phase 3 填充
    memory.py      # D13 默认 memory handler 对
    security.py    # bearer token 依赖注入（execute scope）
    routes/
      __init__.py  governed.py  runs.py(本阶段仅 /healthz 级占位)  hitl.py(占位)
  tests/
    conftest.py    # testclient + 内存热路径 + 注入脚本化 LLM（D12）
    test_session.py  test_governed_routes.py  test_config.py
```

### 4.2 关键规格

**config.py（GatewaySettings）**：

| 配置项 | 默认 | 说明 |
|---|---|---|
| `redis_url` | None | None → 内存热路径（需 memory extra）；设置 → direct 桥 build_hot_path |
| `semaphores` | 内置演示表 | {name: limit}，与注册资源同表（pitfall 2） |
| `trace_root` | `data/gateway-runs` | 布局 `trace_root/{run_id}/trace`（D4） |
| `base_url` / `api_key` / `model_clients` | 必填（内存模式可空） | OPENAI_BASE_URL / OPENAI_API_KEY，接受 OPENAI_API_BASE 别名；model_clients = {name: {model, resource}} |
| `auth_token` | 必填 | bearer；V1 单 token 单 scope（execute） |
| `default_budget_max_units` / `ambient_budget_max_units` | 100000 / 10000000 | ambient 用尽 → 409 并提示（诚实降级，不静默） |
| `step_timeout` / `receipt_timeout` / `poll_interval` | 300 / 30 / 0.2 | 与 patterns 默认对齐 |
| `registry_module` | None | `"module:build_registry"` 形式（D6 通道之二） |
| `make_clients` | None | D12 注入缝；None 时走默认真实装配 |

**session.py**：

- `SessionManager`：app lifespan 启动时建热路径一次（全 session 共享同一热路径，信号量天然共享——这是正确的）；随后开启 ambient run。`start_user_run()` 实现单活跃守卫（有活跃 → None，路由层转 409）。
- `RunSession.open()`：装配链与 real_app.py 同构——`build_run_context(hot, trace_dir, budget_scope, task_factory, make_clients=...)`；root_id = run_id；初始化 root 为 succeeded 终态记录（pitfall 4）；RunsRegistry 登记（携带真实 scope，D4）。make_clients 优先取 settings 注入缝，否则默认真实装配（D12）。
- call-plane 解析规则（D8）：给定 run_id → 该 run 会话；缺省 → ambient。给定 task_id 且热记录存在 → 取当前 eid；task_id 给出但无记录 → 404；未给 → 铸造 `n8n-call-{uuid8}` 任务名与合成 eid。seq 由会话内 (task_id, purpose) 计数器单调分配，起点 agent band。
- ambient 的 call 只走**调用级治理**（信号量/预算/审计），不建热记录、不经 executor——与 probe layer 1 的形态一致。

**routes/governed.py**：

| 端点 | 请求字段 | 响应字段 | 错误映射 |
|---|---|---|---|
| `POST /governed/llm-chat` | run_id?, task_id?, client, purpose?, messages[], kwargs? | status, call_id, response（原始 OpenAI 形 dict）, usage? | 未知 client → 422 附注册名清单；预算尽 → 409；超时 → 504 |
| `POST /governed/tool-call` | run_id?, task_id?, tool, inputs{}, reuse? | status, call_id, result, origin（V1 恒 "executed"） | 未知 tool → 422 附清单；保留字碰撞 → 422 改名指引；其余同上 |

call_id 一律经 `make_call_id(purpose, task_id, eid, seq)` 组装修饰（命名纪律不变式 #4）。

### 4.3 测试锁定（testclient + 内存热路径 + 脚本化 LLM）

- ambient 在启动时存在且不计入活跃；无 run_id 的 chat 落 ambient trace，审计行 event_id 过 `check_call_id_shape`，携带 usage；
- run 内 chat：提交任务后（本阶段可用最小假任务预热记录）以其当前 eid 归属；task 不存在 → 404；未知 client → 422 且响应体列出注册名；
- tool-call：保留字（params/seq/call_id 等）→ 422；origin="executed"；内存热路径下 memsave/memget 不出现于纯 chat 路径；
- settings 注入缝生效（脚本化客户端收到转发 kwargs）；
- .env 装载：启动日志含来源与键数；既有进程环境优先。

**验收**：上述测试全绿；`POST /governed/llm-chat` 可用 curl 在内存模式跑通。此 Phase 交付即 M1 spike 所需的最小后端。

---

## 5. Phase 2 — M1 spike（存在性关口）

**目标**：证明 n8n 的 supply-data 机制接受我们的 JS shell，一次真实对话穿过治理面。**未过此关，Phase 3+ 暂停，回到设计评审。**

工件（n8n 仓库）：

1. `credentials/OrdigovernanceApi.credentials.ts`：{baseUrl, token}。
2. `nodes/OrdigovernanceChatModel/`：`TrackedChatModel`（JS BaseChatModel 子类）+ supply-data 节点壳。
   - `_generate`：LangChain 消息 → OpenAI 形 dict（assistant 的 tool_calls 与 `additional_kwargs["tool_calls"]` 回退双通道，镜射 Python 侧 `_raw_tool_calls` 纪律）；bind 的 tools/tool_choice/stop/其余 kwargs 每次调用原样转发（pitfall 14.2/14.3）；响应 → AIMessage，usage → usage_metadata。
   - `bindTools` 返回 clone，未绑定模型永不携带 specs（clone 纪律）。
3. spike 验收脚本/手工 runbook：本地起 gateway（内存或 redis + .env 真实端点）+ 本地 n8n（npm 全局或 docker），AI Agent 节点挂本模型节点。

**验收（全部满足才过关）**：

- `TrackedChatModel instanceof` n8n 内置 `BaseChatModel` 为 true（提前暴露 peer 版本错配）；
- n8n AI Agent 接受该模型节点并完成一轮对话；
- gateway 的 ambient trace 出现对应 llm_call 审计行，event_id 符合命名纪律且带 usage；
- 断 token / 错 baseUrl 的报错在 n8n UI 可读（不是裸堆栈）。

**回退预案**（写入风险登记）：若 supply-data 或版本错配无法逾越 → 退化为"Python sidecar 传输"（n8n 用普通 HTTP 节点 + 我们提供请求模板），此时 n8n 包缩为 Run/Task/Approval 三节点，重新评审 M2–M5 的用户体验影响。

---

## 6. Phase 3 — gateway task plane（M3 后端）

**目标**：descriptor → GovernedAgent → submit → 可轮询终态记录，证据链完整。

### 6.1 registry.py（填实）

- 三张表：`tools`（name → {factory, resource, event_type, side_effect, description}）、`impls`（name → {factory, description}）、`composites`（name → {factory, description}，Phase 7 启用）。
- 加载：entry points 组 `ordigovernance.gateway.impls` / `.tools` / `.composites`，叠加 `registry_module` 配置模块（merge，冲突以后者覆盖并告警）。
- 启动自检（D6）：遍历已加载工厂的 `__module__`，命中禁限命名空间 → 拒绝启动并逐条列出；规格完整性校验（resource/event_type 必填，side_effect 过 `normalize_side_effect`）。
- 工厂签名约定（写入 README，不写代码）：
  - ToolHandlerFactory：`(session) → async callable`；
  - ImplFactory：`(params: dict, surfaces) → AgentProtocol`。surfaces 携带：热记录存储（读上游结果用）、run_id。**GovernedAgent 由 gateway 统一装配**（per-task tool set、共享 llm registry、memo_scope=run 的 budget scope、开放层不注入 engine 工厂），工厂只回业务 impl；
  - CompositeFactory：`(params, session) → 后台驱动协程`（D10，Phase 7）。

### 6.2 session.submit_task(descriptor)

步骤序：校验 impl 存在（否则 422 附词汇表）→ 重复 task_id 检查（D7 → 409）→ 构造 per-task tool set（registry 全量工具，或 descriptor.tools 白名单过滤）→ ImplFactory 构造 impl → 装配 GovernedAgent → **对每个 upstream 写 dependency edge（D1）**→ `orchestrator.submit(task_id=…, parent_task_id=descriptor.parent_task_id 或 run root)`（pitfall 10）→ 会话登记 descriptor（task_id → descriptor，作为重建唯一事实源，pitfall 13.8）→ 返回受理。

### 6.3 routes/runs.py（全量）

| 端点 | 语义 |
|---|---|
| `POST /runs` | 409 活跃冲突；派生 run_id（复用 runtime 的 new_run_id）；开会话；返回 {run_id, status} |
| `GET /runs` | RunsRegistry list（ newest first ） |
| `GET /runs/{id}` | registry 条目；活跃时附会话内任务状态摘要 |
| `POST /runs/{id}/tasks` | 上节语义；201 {task_id, accepted} |
| `GET /runs/{id}/tasks/{tid}` | 活跃 run 热记录：{task_id, status, execution_id, previous_execution_ids[], result?}；历史 run → 404 并提示走 viewer（D14） |
| `POST /runs/{id}/finish` | D5：非终态 → 409 附清单；否则 teardown + registry finish |
| `GET /runs/{id}/vocabulary` | D3：{impls: [{name, description}], tools: [...], composites: [...]} |

### 6.4 测试锁定

- 单活跃守卫（第二个 POST /runs → 409）；finish 时运行中任务 → 409 清单内容正确；
- descriptor 全流程：提交 → 轮询到 succeeded；**dependency edges 断言**（经 store.dependency.read_graph 读回 child/parent 关系，方向正确）；默认 parent_task_id 落到 run root；
- 未知 impl → 422 且响应体含合法 impl 名清单；重复 task_id → 409；
- 参考测试 impl（**test 内定义，绝不 import examples**）归档自身 → 审计出现 `memsave-…-90` 行；
- registry 加载：entry points 与模块通道 merge；禁限命名空间自检拒绝启动；坏 side_effect → 启动错误；
- D12 注入缝下 llm 流量可断言。

**验收**：上述全绿 + import 边界门绿。

---

## 7. Phase 4 — gateway HITL plane（M4 后端）

**目标**：镜射 viewer/api/hitl.py 语义，run 作用域化。

| 端点 | 语义 |
|---|---|
| `POST /runs/{id}/hitl/pause {task_id}` | sink.pause_node → 受理 receipt |
| `POST /runs/{id}/hitl/resume {root_id}` | sink.resume_tree → 受理 receipt |
| `POST /runs/{id}/hitl/retry {task_id}` | 终态校验（非终态 409）→ orphan guard（deps_reader = 会话 store.dependency，活跃后代 409）→ reopen → 由会话 descriptor 记录重建任务（单一构造源）→ submit |
| `GET /runs/{id}/hitl/receipt/{action_id}` | 执行 receipt；pending → 404（双 receipt 纪律，pitfall 13.7） |
| 全部 | run 已结束 → 404（HITL 只在 run 存活期有效，pitfall 13.6） |

**测试锁定**：pause → 任务 settle cancelled；resume → 第二代（execution_id 变化、prevs 增长）；retry 于运行中任务 → 409；构造父子两任务、父 retry 且子 running → 409（orphan guard）；receipt pending → 404；finish 后 HITL → 404。

---

## 8. Phase 5 — examples/gateway_n8n 参考实现（M3 使能）

**目标**：给 n8n 一套真实可跑的 impl 词汇表 + 一键演示环境。

工件：

1. `examples/gateway_n8n/__init__.py` + `registry.py`：`build_registry()` 返回 GatewayRegistry，词汇表对齐 acceptance 叙事（保证 M3 证据可与 real_app.py 对照）：
   - impls：`researcher`（一次 world read + 一次 LLM 分析 + 自归档）、`writer`（读上游热记录/归档、pin、起草）、`reviewer`（评审 + 可解析分数）、`publisher`（pin 上游、发布）；
   - tools：`search`（确定性 mock handler，readonly）——V1 演示沿用确定性世界，真实 handler 由部署方注入；
   - 该包经 `GATEWAY_REGISTRY_MODULE=examples.gateway_n8n.registry:build_registry` 注入（演示配置模块通道），同时注册 entry points 示例（演示第一通道）。
2. `examples/gateway_n8n/compose/`：docker compose（redis + gateway + viewer 指向同一 trace_root + n8n），README runbook：从 n8n 画布到审计证据的完整路径。
3. 内存模式演示备选（无 docker 时）：单命令起 gateway（memory extra）+ 手工 n8n 接入说明。

**验收**：compose 起栈后，`POST /runs` → 提交 researcher/writer/reviewer/publisher descriptor 链 → 全部 succeeded；trace bundle 证据形状与 real_app.py 对照一致：generations、memsave-90 行、pins、deps edges 齐全；viewer（同 trace_root）validate 端点 available=True 且零 violation。

---

## 9. Phase 6 — n8n 节点全套 + 端到端（M2/M3/M4）

工件（n8n 仓库，全部 TypeScript）：

| 节点 | 形态 | 关键行为 |
|---|---|---|
| OrdigovernanceRun | action | POST /runs → item 输出 run_id；mode=finish 时 POST finish（409 透传任务清单为可读错误） |
| OrdigovernanceChatModel | supply-data | Phase 2 成果产品化：client 名、purpose、run_id/task_id 表达式参数 |
| OrdigovernanceTool | action + AI tool 双模 | 工具名下拉（vocabulary 端点动态加载）；inputs JSON；action 模式直接调用，AI tool 模式供 Agent 节点绑定 |
| OrdigovernanceTask | action | impl 下拉（vocabulary）；params JSON；upstream 列表；tools 白名单可选；submit → 按 poll_interval/timeout 轮询终态；D11 多 item 后缀纪律；终态记录整体输出 |
| OrdigovernanceApproval | action | pause/resume/retry 选择器 + 目标 id；受理后轮询执行 receipt（404=pending），行为对齐 viewer 的 hitl.js waitReceipt |

测试策略：n8n 节点测试基建 + mock gateway（请求/响应契约锁定）；错误路径（409/422/504）在 n8n UI 的可读性逐条验收。

**端到端验收（真实端点，.env 驱动，复用 probe 的环境装载纪律）**：

- **M2**：AI Agent（ChatModel + Tool 节点绑定）完成一次带工具调用的对话 → 审计流出现受治 tool call id 与每次 llm_call 的 usage；origin=executed；
- **M3**：n8n 画布编排 researcher×N → writer → reviewer → publisher（upstream 连边）→ 全部 succeeded；bundle 证据形状对照 real_app.py（generations、pins、memsave 带、deps）；viewer validate 零 violation；
- **M4**：Approval 节点 pause → 任务 cancelled；resume → 第二代；双 receipt 在 n8n 可观察。

---

## 10. Phase 7 — composites（M5）

gateway 侧：`composites` 表启用；`POST /runs/{id}/composites {name, params}` 在会话内启动后台驱动协程（子任务 parent_task_id=run root）；`GET /runs/{id}/composites/{cid}` 返回 {status, children[], outcome}。参考实现：`quality_gate_pair` 包装 QualityGatePattern（producer/judge 词汇来自同一 registry；root=run root，与 acceptance drive 层同构）。

n8n 侧：OrdigovernanceComposite 节点（名下拉、params、轮询上述端点）。

**M5 验收**：单个 Composite 节点跑通质量门 → 审计流出现 sink actor="quality-gate" 的重开迭代行；outcome 携带 scores/iterations。

---

## 11. Phase 8 — 收口

1. 文档：gateway README（完整 API spec、部署、披露段）、n8n README（披露原文 + 每节点文档 + peer 版本纪律）、examples runbook、docs/n8n-bridge-design.md 按本方案回写冻结决策；
2. `docs/pitfalls.md` 增补施工中新踩的坑（编号、附锁定测试名）——预计至少会有：entry-point 加载绕过静态门（D6 由来）、ambient run 与单活跃守卫的共存、composite drive 级形态对 pitfall 4/13.1 的规避理由；
3. 验收矩阵（下节）全量执行并记录证据；
4. 发布卫生：版本钉扎、LICENSE 文件核对（gateway=SUL，n8n 仓库=MIT）、两个仓库各自 CI 绿。

---

## 12. 验收矩阵（汇总，全部为证据型判据）

| 里程碑 | 判据 |
|---|---|
| M1 | instanceof=true；AI Agent 一轮对话；ambient trace 出现合规 llm_call + usage；错误可读 |
| M2 | 工具调用经治理面（call id 合规、usage 齐）；422 词汇表错误在 n8n 可读 |
| M3 | n8n 驱动参考 registry 全链 succeeded；bundle 与 real_app 证据形状一致；validate 零 violation |
| M4 | pause→cancelled、resume→第二代、双 receipt 可观察 |
| M5 | 单节点质量门收敛；审计出现 quality-gate 重开迭代 |

---

## 13. 纪律检查清单（每个 PR 必过）

1. `./run_tests.sh` 全绿（含 gateway 套件）；`check_import_boundary.py` 绿；
2. 新行为均有"无此改动必失败"的测试锁定；
3. `ordigovernance-api` 零改动或仅后向兼容新增；gateway 不 import examples；n8n 仓库零 Python 依赖；
4. 硬学的教训当日写入 pitfalls.md；
5. 许可边界：SUL/MIT 文件各就其位，bridge 代码无 engine import。

---

## 14. 风险登记与回退

| 风险 | 等级 | 缓解/回退 |
|---|---|---|
| supply-data / 内置 LangChain 版本错配 | 高 | Phase 2 关口前置；peer 钉扎；回退 sidecar 方案 |
| 长任务占用 n8n worker | 中 | submit-and-poll + 可配 timeout；V2 评估 putExecutionToWait |
| 动态 registry 绕过静态 import 检查 | 中 | D6 启动自检兜底；部署文档警示 |
| ambient 预算耗尽影响无 run 调用 | 低 | 409 明示 + 配置可调；引导正式用法走 Run 节点 |
| composite 证据无单一节点视图 | 低 | D10 已选 drive 级 + composites 轮询端点；V2 评估 node 包装 |

## 已完成 ✅

| Phase | 内容 | 状态 |
|---|---|---|
| Phase 0/1/3/4/7 | gateway 全部后端（call plane + task plane + HITL plane + composites） | 代码 + 测试锁定，47 passed，import 边界绿 |
| Phase 5 | examples/gateway_n8n/copy 参考 registry + compose + viewer + M3 演练测试 | 完成 |
| Phase 8（本仓部分） | 根 README 包表、gateway README API spec、pitfalls.md §16、设计文档状态回写 | 完成 |
| Phase 2（代码部分） | n8n 仓库骨架 + TrackedChatModelcopy + supply-data 节点壳 + 9 个契约测试 | build + test 全绿 |

**中途适配项**：gateway 配置已兼容根 .envcopy（REDIS_URLcopy 别名、PROBE_MODELcopy 回退、默认 llmcopy 资源）；gateway 真实端点链路已实测通过（healthz + llm-chat 返回合规 call_id + usage）。

## 进行中 ⏳

**Phase 2 — M1 spike 手工验收（存在性关口，当前所在位置）**

- [x] 节点包 build/test 通过、npm installcopy 到 ~/.n8n/customcopy
- [x] @langchain/corecopy 版本检查（本地 0.3.80，待与 n8n 内置版本比对）
- [ ] **n8n 本体安装中**（npm install -g n8ncopy，Node 22）
- [ ] n8n 启动 → 画布验收四项判据：
  1. instanceof（n8n 内置 BaseChatModel 与节点包同版本）
  2. AI Agent 挂 Ordigovernance Chat Model 完成一轮对话
  3. ambient trace 出现合规 llm_call + usage
  4. 错误 token/baseUrl 在 n8n UI 可读

## 待做 📋

- **Batch 6 = Phase 6**：n8n 其余四节点（Run / Tool / Task / Approval）+ mock gateway 契约测试 + M2/M3/M4 真实端到端
- **Phase 7 n8n 侧**：Composite 节点（M5，gateway 后端已就绪）
- **Phase 8 收口**：n8n 仓库 README/CI/发布卫生

**下一步**：等 n8n 安装完成 → n8n startcopy → 按 M1 runbook 验收，结果贴给我。