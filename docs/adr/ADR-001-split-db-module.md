# ADR-001: 拆分巨型 db.py 模块

**状态**: 已接受 (Accepted)  
**日期**: 2026-05-15  
**决策者**: 架构重构团队  

---

## 背景 (Context)

`packages/python/javdb_platform/db.py` 是一个 **6,370 行、138 个函数**的巨型模块，混合了 8 个不同的职责：

1. 连接管理（SQLite/D1/Dual 后端路由）
2. 会话状态管理（Session ID、Run ID、Write Mode）
3. 历史记录写入（MovieHistory/TorrentHistory 的 upsert + audit）
4. 历史记录读取（查询和快照）
5. 报告会话管理（ReportSessions 的 CRUD）
6. 统计数据管理（SpiderStats, UploaderStats, PikpakStats）
7. 操作表管理（RcloneInventory, DedupRecords, PikpakHistory）
8. 回滚逻辑（pending 模式 + audit 模式）
9. 迁移助手（v5→v6, v6→v7, 列迁移）

### 问题

- **缺乏局部性 (Locality)**：理解一个操作（如"保存历史记录"）需要在 6,370 行中跳转
- **浅模块 (Shallow Module)**：接口复杂度接近实现复杂度，没有提供足够的杠杆 (Leverage)
- **难以测试**：大部分测试是集成测试，单元测试稀少
- **42 个导入点**：所有模块都依赖这个巨型模块

---

## 决策 (Decision)

我们决定将 `db.py` 拆分为 **9 个按功能职责划分的模块**：

1. **`db_connection.py`** — 连接池、后端路由、WAL 设置
2. **`db_session.py`** — Session ID、Run ID、Write Mode 状态管理
3. **`db_history_write.py`** — MovieHistory/TorrentHistory 写入（stage + commit）
4. **`db_history_read.py`** — MovieHistory/TorrentHistory 读取
5. **`db_reports.py`** — ReportSessions CRUD
6. **`db_stats.py`** — SpiderStats, UploaderStats, PikpakStats
7. **`db_operations.py`** — RcloneInventory, DedupRecords, PikpakHistory
8. **`db_rollback.py`** — 回滚协调器
9. **`db_migrations.py`** — 迁移助手

### 关键设计决策

#### 1. 按功能拆分 vs 按数据库文件拆分

**选择**：按功能拆分（方案 B）

**理由**：
- 更符合"深度"原则——每个模块的接口更小、职责更单一
- 读/写分离（`db_history_read.py` vs `db_history_write.py`）使测试更容易
- 回滚逻辑独立（`db_rollback.py`）避免与其他模块耦合

**权衡**：
- 模块数量更多（9 个 vs 6 个）
- 需要处理循环依赖（通过懒加载解决）

#### 2. 保留 db.py 门面 vs 强制更新导入

**选择**：强制更新导入（选项 2）

**理由**：
- 调用方明确知道自己在用哪个模块
- 避免 `from db import *` 的隐式依赖
- 长期来看更易维护

**权衡**：
- 破坏性变更，需要修改 42 个文件
- 需要分阶段迁移（Phase 1 → Phase 2 → Phase 3）

#### 3. 回滚逻辑的位置

**选择**：独立的 `db_rollback.py` 协调器（选项 1）

**理由**：
- 每个模块提供公开的 `rollback_*_for_session()` API
- `db_rollback.py` 协调跨表的回滚操作
- 清晰的职责边界

**权衡**：
- 增加了一个额外的模块
- 需要每个模块实现 rollback 接口

#### 4. 全局状态的处理

**选择**：渐进式策略（A → B → C）

**理由**：
- Phase 1：保持全局状态（最小改动）
- Phase 2：引入 `SessionContext` 对象（封装状态）
- Phase 3：参数传递（彻底消除全局状态）

**权衡**：
- 需要多次重构
- 但每次重构都是可验证的小步骤

#### 5. 测试策略

**选择**：使用 `unittest.mock.patch`（选项 A）

**理由**：
- 标准库，无需额外依赖
- 可以 mock `get_db()` 和 `get_active_session_id()`
- 单元测试运行快速（0.09-0.11 秒）

**权衡**：
- Mock 代码略显冗长
- 需要小心处理 mock 的生命周期

---

## 实现策略 (Implementation)

### Phase 1：创建新模块，保留 db.py 作为门面

1. 创建 9 个新模块
2. `db.py` 变成重导出层（~100 行）
3. 运行测试，确保功能不变
4. **此时 42 个导入点不需要改动**

### Phase 2：逐步迁移导入点

按包（package）逐个迁移：
- `javdb_spider/` 先迁移（最核心）
- `javdb_integrations/` 后迁移
- `javdb_migrations/` 最后迁移

每迁移一个包，运行该包的测试。

### Phase 3：删除 db.py 门面

- 确认所有导入点都已迁移
- 删除 `db.py`
- 运行全量测试

---

## 原型验证 (Prototype)

我们创建了两个原型模块来验证设计：

### 1. `db_stats.py`（370 行，9 个函数）

**验证内容**：
- 懒加载机制避免循环依赖 ✅
- 从 `db_connection.py` 导入（fallback 到 `db.py`）✅
- 单元测试策略（11 个测试，全部通过）✅

**关键发现**：
- 懒加载 + fallback 模式有效
- `unittest.mock.patch` 可以隔离依赖
- 需要 mock `_ensure_imports()` 避免导入错误

### 2. `db_connection.py`（310 行，8 个函数）

**验证内容**：
- 作为基础模块被其他模块依赖 ✅
- 连接池、后端路由、WAL 设置 ✅
- `db_stats.py` 成功从 `db_connection.py` 导入 ✅

**关键发现**：
- 基础模块应该零依赖（除了 `config_helper` 和 `logging_config`）
- 线程本地存储 (`threading.local()`) 用于连接池
- 后端路由逻辑清晰（sqlite/d1/dual）

---

## 经验教训 (Lessons Learned)

### 1. 懒加载是避免循环依赖的关键

```python
# db_stats.py
_get_db = None

def _ensure_imports():
    global _get_db
    if _get_db is None:
        try:
            from packages.python.javdb_platform.db_connection import get_db
            _get_db = get_db
        except ImportError:
            # Fallback to db.py during Phase 1
            from packages.python.javdb_platform.db import get_db
            _get_db = get_db
```

### 2. 原型优先，避免大规模返工

创建 `db_stats.py` 原型（最简单的模块）验证了：
- 拆分方案可行
- 测试策略有效
- 懒加载机制正确

如果直接创建所有 9 个模块，发现问题后返工成本会很高。

### 3. 测试中的 mock 需要小心处理

**问题**：直接设置 `db_stats._get_db = mock_get_db` 不生效，因为 `_ensure_imports()` 会覆盖。

**解决**：使用 `@patch('..._ensure_imports')` 阻止重新导入。

### 4. 模块边界应该基于职责，而非物理结构

**错误**：按数据库文件拆分（`db_history.py` 包含所有 history.db 操作）

**正确**：按功能拆分（`db_history_read.py` vs `db_history_write.py`）

理由：读/写分离使测试更容易，职责更清晰。

### 5. 破坏性变更需要分阶段迁移

强制更新 42 个导入点是破坏性变更，但通过分阶段迁移可以降低风险：
- Phase 1：创建新模块，保留门面（零破坏）
- Phase 2：逐包迁移（渐进式验证）
- Phase 3：删除门面（最终清理）

---

## 后果 (Consequences)

### 正面影响

1. **局部性提升**：每个模块职责单一，代码集中
2. **可测试性提升**：单元测试可以隔离依赖，运行快速
3. **可维护性提升**：新开发者更容易理解模块边界
4. **深度提升**：每个模块提供简单的接口，隐藏复杂的实现

### 负面影响

1. **模块数量增加**：从 1 个模块变成 9 个模块
2. **导入路径变长**：`from db import get_db` → `from db_connection import get_db`
3. **迁移成本**：需要修改 42 个文件
4. **学习曲线**：新开发者需要理解模块边界

### 风险

1. **循环依赖**：如果模块间依赖关系设计不当，可能出现循环依赖
   - **缓解**：使用懒加载 + fallback 机制
2. **测试覆盖率下降**：拆分过程中可能遗漏测试
   - **缓解**：每个新模块都创建单元测试
3. **性能回退**：懒加载可能增加首次调用的开销
   - **缓解**：懒加载只在首次调用时执行，后续调用无开销

---

## 相关决策 (Related Decisions)

- **ADR-002**（未来）：Session 状态管理的参数传递策略
- **ADR-003**（未来）：Pending Mode vs Audit Mode 的迁移计划

---

## 参考资料 (References)

- [CONTEXT.md](../../CONTEXT.md) — 领域术语词汇表
- [CLAUDE.md](../../CLAUDE.md) — 项目概览
- [docs/D1_ROLLBACK.md](../D1_ROLLBACK.md) — 存储后端架构
- [A Philosophy of Software Design](https://web.stanford.edu/~ouster/cgi-bin/book.php) — 深度模块理论

---

## 附录：模块依赖图

```
db_connection.py (基础模块，零依赖)
    ↓
db_session.py (依赖 db_connection)
    ↓
db_history_write.py (依赖 db_connection + db_session)
db_history_read.py (依赖 db_connection)
db_reports.py (依赖 db_connection + db_session)
db_stats.py (依赖 db_connection)
db_operations.py (依赖 db_connection)
    ↓
db_rollback.py (协调器，依赖上述所有模块)
    ↓
db_migrations.py (依赖 db_connection，用于初始化)
```

---

## 附录：测试覆盖率

| 模块 | 行数 | 函数数 | 测试数 | 覆盖率 |
|------|------|--------|--------|--------|
| `db_connection.py` | 310 | 8 | 待添加 | - |
| `db_stats.py` | 370 | 9 | 11 | 100% |
| 其他 7 个模块 | 待创建 | - | 待添加 | - |

---

## 附录：原型代码示例

### db_stats.py 的懒加载机制

```python
# Lazy imports to avoid circular dependencies
_get_db = None
_get_local_sqlite_db = None
_REPORTS_DB_PATH = None

def _ensure_imports():
    """Lazy import to avoid circular dependency with db_connection."""
    global _get_db, _get_local_sqlite_db, _REPORTS_DB_PATH
    if _get_db is None:
        try:
            from packages.python.javdb_platform.db_connection import (
                get_db,
                get_local_sqlite_db,
                REPORTS_DB_PATH,
            )
            _get_db = get_db
            _get_local_sqlite_db = get_local_sqlite_db
            _REPORTS_DB_PATH = REPORTS_DB_PATH
        except ImportError:
            # db_connection doesn't exist yet (e.g., during Phase 1)
            # Fall back to importing from db.py
            from packages.python.javdb_platform.db import (
                get_db,
                get_local_sqlite_db,
                REPORTS_DB_PATH,
            )
            _get_db = get_db
            _get_local_sqlite_db = get_local_sqlite_db
            _REPORTS_DB_PATH = REPORTS_DB_PATH
```

### 测试中的 mock 策略

```python
@patch('packages.python.javdb_platform.db_stats._ensure_imports')
def test_uses_local_sqlite_connection(self, mock_ensure_imports):
    """Should use get_local_sqlite_db() instead of get_db()"""
    mock_get_local_db = MagicMock()
    # Pre-load the lazy imports to avoid import error
    db_stats._get_local_sqlite_db = mock_get_local_db
    db_stats._REPORTS_DB_PATH = '/fake/path'
    
    # ... rest of test
```
