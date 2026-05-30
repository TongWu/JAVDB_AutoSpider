# ADR-041: Rust Core 回退策略 —— Best-Effort 镜像 vs Rust-Required 模块

| 字段       | 值                                                                    |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | Proposed —— 执行见 [IMP-ADR041-01](IMP-ADR041-01-demote-and-split.md)  |
| **日期**   | 2026-05-30                                                            |
| **作者**   | Ted                                                                   |
| **关联**   | [ADR-020](../_archive/ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.zh.md)(解析器接口;依赖本 ADR 修订的"冻结镜像 + parity 守卫"), [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.zh.md)(Rust scraper 是规范解析路径), [ADR-023](../ADR-023-Proxy-Recommendation-Policy/ADR-023-proxy-recommendation-policy.zh.md)(Selection Signal 插在 `ProxyPool.set_health_provider` 上), [ADR-011](../_archive/ADR-011-Parsing-Module/ADR-011-javdb-parsing-module.zh.md)(确立了 parsing 模块 + 冻结 Python 镜像) |

> 源自 2026-05-30 架构评审(候选 1 ——"哪些部分该用 Rust/Go 重构"):[architecture-review-2026-05-30.zh.html](../architecture/architecture-review-2026-05-30.zh.html)。评审发现 Rust 迁移只做了一半:若干 Rust 模块的接缝后面顶着一份**保持 value-parity 锁步的完整 Python 重实现**,外加一个**幽灵** Rust 适配器(HTTP requester)。本 ADR 确定这一回退层的稳态策略;幽灵 requester 单独跟踪(评审卡片 2)。

## 背景

Rust core(`javdb/rust_core/`,经 PyO3/maturin 安装为 `javdb.rust_core`)是 HTML 解析、magnet 归类、代理池/封禁管理、URL 工具与脱敏的高性能实现。它们每一个前面都有一个 Python 模块,遵循 **"优先 `javdb.rust_core`,`ImportError` 时回退到纯 Python 镜像"** 的模式 —— [ADR-020](../_archive/ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.zh.md)(D2)称之为"layer-legal 且惯用"。

2026-05-30 评审量化了这套惯用法当前形态的代价:

- **约 2,826 行 Python 在这些接缝后重实现了 Rust**:解析器 `javdb/parsing/fallback/`(1,070)、代理 `javdb/proxy/pool.py` + `ban_manager.py`(942)、以及 `javdb/spider/url_helper.py` + `javdb/parsing/magnet_categorize.py` + `javdb/infra/masking.py`(814)。
- **两份实现被要求完全一致**,由 **value-parity 测试**强制 —— `tests/parity/test_parser_parity.py`(368)与 `tests/unit/test_magnet_parity.py`(105)。[ADR-020](../_archive/ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.zh.md)(D6)把 `test_magnet_parity.py` 定为迁移"全程保持绿色"的守卫。
- **两个适配器只有在行为跨接缝有差异时才构成真正的接缝**。这里行为*不允许*有差异 —— parity 即契约 —— 所以接缝顶着的是重复的局部性(locality),不是变化;代价是一份永久的双语言锁步。
- **生产从不跑这份回退**。`docker/Dockerfile` 与 `docker/Dockerfile.api` 都用 `maturin build --release` 构建 wheel;CI 经 `setup-python-env` → `install-rust-wheel` 安装。Python 镜像只在 wheel 缺失时运行 —— 即没有 Rust 工具链的本地开发。[ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.zh.md) 已把 **Rust scraper 视为规范解析路径**(其 parse contract 与 fill-rate 遥测观测的是 Rust 输出)。
- **部分模块的回退是静默的**。解析器和代理池在回退时记 `WARNING`;`javdb/parsing/magnet_categorize.py` 只记 `logger.debug` —— 所以 magnet 归类的降级是看不见的。
- **各回退风险并不相等**。parser/magnet/url/masking 的回退即便偏差,输出是*可检查的* —— 开发者会看到解析错了。但 **代理池与 ban manager 是有状态的**(选择、冷却、封禁):一份悄悄偏差的副本会以无法肉眼发现的方式行为异常,而这恰恰可能是开发者本地正在调的东西。"best-effort 的代理选择"是调试陷阱,不是便利。

决策空间(来自评审 grilling):纯 Python 回退**仍然**被需要,作为免 Rust 工具链的本地开发路径 —— 但它应停止充当 value-parity 维护锚点,且不应在偏差不可检测之处悄悄偏离。

## 决策

把统一的"Rust-first + value-parity Python 镜像"策略,替换为**按可检查性划分的两层回退策略**,并让每个回退都"喊一声"。

### 设计决策

**D1. 两层回退。**

- **Best-Effort Fallback** —— `javdb.parsing`(index/detail/tag 解析器)、`javdb/parsing/magnet_categorize.py`、`javdb/spider/url_helper.py`、`javdb/infra/masking.py`(以及 `javdb/proxy/pool.py` 中并置的 `mask_proxy_url` 辅助函数)。纯 Python 镜像**保留**,作为免工具链的本地开发便利。其输出可检查,偏差自证。它是 **shape-contracted(形状契约),而非 value-parity-guaranteed(逐值保证)**。
- **Rust-Required Module** —— `ProxyPool` 与 `ProxyBanManager`。它们有状态且不可检查。Python 重实现**移除**;无 `javdb.rust_core` 时构造它们会抛出清晰错误。

**D2. Best-Effort 层:用形状契约取代 value parity。** 删除 value-parity 套件 `tests/parity/test_parser_parity.py` 与 `tests/unit/test_magnet_parity.py`。代之以一个薄 **shape/smoke** 测试,断言 Python 回退(a)能 import、(b)对小夹具返回正确的*形状* —— 与 Rust 对象暴露相同的访问器/键(如 `MovieDetail.get_magnets_as_legacy()`、`subtitle/hacked_subtitle/hacked_no_subtitle/no_subtitle` 字典键),**而非**与 Rust 逐字节相等。这保住了调用方真正依赖的唯一契约(形状,见 [ADR-020](../_archive/ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.zh.md) D2 的 `get_magnets_as_legacy()` 一致性),同时丢掉双语言逐值锁步。

**D3. 回退要响、要一致。** 每次 Best-Effort 回退恰好记一条 `WARNING`:*"Rust core unavailable — pure-Python `<area>` fallback is best-effort and may diverge from production."* 把 `javdb/parsing/magnet_categorize.py` 的 `logger.debug` 提升为 `logger.warning`,使没有任何回退是静默的。这直接化解了 [ADR-020](../_archive/ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.zh.md) D6 的顾虑(静默掉到慢 Python 路径):parity 去掉后,是*响度* —— 而非逐值相等测试 —— 来阻止 Rust 静默旁路被忽视。

**D4. Rust-Required 守卫放在构造 chokepoint,而非 import。** 守卫位于每个调用方都会经过的两个工厂 —— `create_proxy_pool_from_config(...)` 与 `get_ban_manager()` —— Rust 不可用时抛出清晰错误(`RuntimeError`,信息:"proxy pool requires the Rust core (`javdb.rust_core`); install the wheel")。它**不是** import 时失败:`import javdb.proxy.pool` 对无害共享符号(`ProxyInfo`、`is_proxy_usable`、`mask_proxy_url`)仍安全,而从不构造池的 `--no-proxy` 运行无需 Rust 仍可工作。移除有状态的 Python `ProxyPool` 选择/封禁主体与 `ProxyBanManager` Python 类;折叠 `RUST_PROXY_AVAILABLE` 分支。

**D5. 保留无害的共享模型/辅助(它们属 Best-Effort,不是池状态)。** `ProxyInfo`(纯 dataclass,被 `javdb/proxy/policy.py`、`apps/cli/ops/profile_hot_paths.py`、测试引用)、`is_proxy_usable`(`javdb/proxy/policy.py`)与 `mask_proxy_url`(脱敏)**不是**有状态选择逻辑 —— 它们保留,`mask_proxy_url` 保留其纯 Python 主体。"Rust-Required"针对的是池/封禁*行为*,而非文件里并置的每个符号。

**D5a. 代理行为测试面迁移到 Rust 池 —— 不是删除(实现期修订)。** 代理池/封禁管理器**没有任何 Rust 端测试**(`pool.rs` / `ban_manager.rs` 的 `#[test]` 为零),因此 `tests/unit/test_proxy_pool.py` + `tests/unit/test_proxy_ban_manager.py`(~1,000 行)是选择/冷却/health 加权/ban-skip/session 级封禁的**唯一**行为规格。这些测试大多直接构造 *Python* `ProxyPool()` / `ProxyBanManager()`,所以删 Python 类会把代理行为覆盖降到接近零。因此把行为测试**改指向**经工厂 `create_proxy_pool_from_config(...)` / `get_ban_manager()` 构造(本环境下返回 Rust 池),并针对 Rust 实现断言同一行为契约 —— **保留并升级**测试面(它现在通过真正的接口测试生产实现;接口即测试面)。依赖 Python-only 形状的直接 API 测试(`add_proxy()` 单数、直接 `ProxyBanManager()` singleton 语义)适配到 Rust API(`add_proxies_from_list` / 工厂),或在 Rust 面确有差异处舍弃。`apps/cli/ops/profile_hot_paths.py` 的两个 Python 池微基准(`bench_get_next_proxy_rr`、`bench_get_next_proxy_weighted`)失去对象,删除;`ProxyInfo` / `is_proxy_usable` 基准保留。

**D6. 生产不受影响;本变更只改无 Rust 路径。** Docker 与 CI 总是携带 wheel,所以生产从未跑过回退、也永不触发 D4 错误。本变更删除了一个维护锚点(双语言逐值 parity),并在本地开发中拒绝了一条不可检测的偏差路径(代理池)。这与 [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.zh.md)(Rust 是规范解析路径)和 [ADR-020](../_archive/ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.zh.md)(Rust-first 分派)一致。

**D7. 与 ADR-020 / ADR-011 的关系 —— 修订,不是 supersede。** ADR-020 的解析器接口合并与 ADR-011 的 parsing 模块原样保留。本 ADR 只修订它们依赖的*回退策略*维度:ADR-020 D6 把 value parity 当作**迁移守卫**(现已 Implemented);ADR-041 把 Best-Effort 层的**稳态**守卫设为*形状而非逐值*,并对 Rust-Required 层整体移除镜像。会在 ADR-020 的 Status Log 加一条回指。

## 后果

### 正面

- **删除约 2,826 行** Python 重实现(代理池/封禁)外加 473 行 value-parity 测试,扣除保留的小块 Best-Effort 镜像与新增 shape/smoke 测试后仍为净删减。
- **局部性(locality)** —— 代理选择/封禁行为只存在于一处(Rust);没有第二份副本要锁步。
- **没有不可检测的偏差** —— 唯一可能无形异常的回退(代理池)被以清晰错误拒绝,而非静默运行。
- **没有静默降级** —— D3 让每个保留的回退都自报家门;Rust 旁路现在无需 parity 测试也可见。
- **反向删除测试(deletion test)成立** —— 删掉代理 Python 镜像后复杂度*不会*重现(生产由 Rust 覆盖);删掉 parity 套件不会丢失调用方依赖的契约(形状由 D2 保住)。

### 负面

- **无 Rust 工具链的本地开发失去演练代理池的能力** —— `--use-proxy` 池运行现在需要 wheel。缓解:`--no-proxy` 本地开发不受影响(D4),构建 wheel 仅需一步 `maturin develop`。
- **Best-Effort 镜像可能偏离 Rust** —— 这是有意为之,它不再逐值保证。缓解:它从不在生产运行(D6)、它很响(D3)、其形状仍被测试(D2)。
- **~1,000 行代理行为测试须改指向、而非删除** —— `tests/unit/test_proxy_pool.py` + `tests/unit/test_proxy_ban_manager.py` 是唯一行为规格(无 Rust 端测试),故迁移到工厂/Rust 池(D5a)。这是本阶段最大的单块工作,且在 Rust 面有差异处需适配/舍弃。
- **`apps/cli/ops/profile_hot_paths.py` 失去两个微基准** —— 其 Python 池选择基准被删除(D5a)。

### 风险

- **某调用方在意料之外的无 Rust 路径上构造池**,现在会抛错而非静默降级。缓解:守卫位于两个工厂 chokepoint,信息清晰可操作;生产总有 Rust。
- **某 Python `ProxyPool` 类引用方被打断。** IMP Task 0 已枚举:工厂 `create_proxy_pool_from_config` 是公开入口(调用方不动);*类符号*仅用作 `javdb/legacy/_spider_legacy.py` + `javdb/spider/runtime/state.py` 的类型注解(改写为 `Optional[Any]`)、pikpak/qb service 的未用 import(从 import 行删除)、`profile_hot_paths.py` 的基准对象(D5a)、以及两个行为测试文件(D5a)。全部在 IMP-ADR041-01 中处理。

## 实施路线图

| 阶段 | IMP | 交付 | 推迟 |
| --- | --- | --- | --- |
| Phase 1 —— 降级与拆分 | [IMP-ADR041-01](IMP-ADR041-01-demote-and-split.md) | Best-Effort 层(去掉 value parity → shape/smoke 测试;响亮 `WARNING`,含 magnet `debug→warning`);Rust-Required 层(守卫 `create_proxy_pool_from_config` + `get_ban_manager`;移除 Python `ProxyPool`/`ProxyBanManager` 主体;保留 `ProxyInfo`/`is_proxy_usable`/`mask_proxy_url`);CONTEXT.md 术语;ADR-020 回指 | 幽灵 HTTP requester(评审卡片 2 —— 独立 ADR/PR) |

### 明确的非目标(YAGNI)

- **不删除 Best-Effort 镜像** —— 免工具链本地开发路径仍被需要(评审 grilling 结论)。
- **不动 HTTP requester** —— 幽灵 Rust requester 是评审卡片 2;单独跟踪。
- **不把 Rust wheel 设为整个包的硬安装依赖** —— 只有池/封禁构造需要它;其余皆 best-effort 降级。

## 领域语言(CONTEXT.md 新增)

- **Best-Effort Fallback** —— Rust 模块(解析器、magnet 归类、URL 工具、脱敏)的纯 Python 镜像,为免工具链本地开发保留。形状契约,**不**逐值保证;启用时记 `WARNING`;从不在生产运行(Docker/CI 总是携带 Rust wheel)。
- **Rust-Required Module** —— 行为有状态且不可检查的模块(`ProxyPool`、`ProxyBanManager`),因此**没有** Python 回退。无 `javdb.rust_core` 时在构造 chokepoint(`create_proxy_pool_from_config`、`get_ban_manager`)抛出清晰错误。

## 考虑过的替代方案

- **完全删除 Python 回退(到处把 Rust 设为硬依赖)** —— 否决:可检查模块的免 Rust 工具链本地开发路径仍被需要。
- **维持现状(六块全 value parity)** —— 否决:一份不允许有差异的双语言逐值锁步是维护锚点,且代理镜像可能不可检测地偏离。
- **六块统一 best-effort(保留代理镜像、去 parity、warn)** —— 否决:代理/封禁偏差不可检查;"best-effort 代理选择"是陷阱而非便利。
- **代理模块 import 时硬失败** —— 否决:打断 `import javdb.proxy.pool` 取无害共享辅助,并打断无 Rust 的 `--no-proxy` 本地开发。

## 参考

- [ADR-020 — Parser Interface Consolidation](../_archive/ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.zh.md)
- [ADR-035 — Site-Contract Drift Sentinel](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.zh.md)
- [ADR-023 — Proxy Recommendation Policy](../ADR-023-Proxy-Recommendation-Policy/ADR-023-proxy-recommendation-policy.zh.md)
- [ADR-011 — JavDB Parsing Module](../_archive/ADR-011-Parsing-Module/ADR-011-javdb-parsing-module.zh.md)
- 2026-05-30 架构评审:[architecture-review-2026-05-30.zh.html](../architecture/architecture-review-2026-05-30.zh.html)

## 状态日志

- 2026-05-30: Proposed(源自 2026-05-30 架构评审,候选 1 grilling)。层级拆分(Best-Effort vs Rust-Required)、parity→形状、响亮回退、构造 chokepoint 守卫。IMP-ADR041-01 待执行。
- 2026-05-30: 实现期修订(Task 0 引用方枚举)。发现代理池/封禁**无 Rust 端测试**,故那 ~1,000 行 Python 池/封禁测试是唯一行为规格。新增 **D5a**:行为测试**改指向工厂/Rust 池**,不删除(保留并升级测试面);`profile_hot_paths.py` 的 Python 池基准删除;`ProxyPool` 作类型注解处(`legacy`、`state.py`)改写为 `Optional[Any]`。Negative/Risks 相应更新。
