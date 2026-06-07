# ADR-025：用户偏好模型

| 字段        | 值                                                                    |
| ----------- | --------------------------------------------------------------------- |
| **状态**    | Proposed                                                              |
| **日期**    | 2026-05-27                                                            |
| **作者**    | Ted                                                                   |
| **关联**    | [ADR-022](../_archive/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.zh.md), [ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.zh.md) |

## 背景

[ADR-022](../_archive/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.zh.md)
已经为用户偏好建模创建了数据基础：`MovieMetadata`、`MovieRatings` 和
`ContentPreferences`。它有意把模型本身推迟为后续设计，因为有意义的模型设计需要足够的
显式用户评分和偏好标注。

第一版模型不应成为控制所有 pipeline 决策的泛化推荐大脑。Torrent 质量现在由
[ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.zh.md)
负责；重复度和新颖度判断也已经有 history、inventory、dedup 等确定性信号。把这些职责全部混入
第一版偏好模型，会让训练和解释都变得更困难。

ADR-025 的目标是把第一版偏好模型定义为结构化、可解释的 `preference_score`。它后续可以参与
更大的 `download_utility_score`，但默认不控制生产 ingestion。

## 决策

构建一个离线训练、D1 canonical、可解释的用户偏好模型，用 ADR-022 数据预测
`preference_score`。第一版使用显式评分和内容偏好作为强信号，把隐式行为作为弱旁路证据，
持久化版本化预测，并把结果暴露给 Web/API 以及 pipeline 的 shadow/assist 流程。

ADR-025 替代 ADR-022 中提到的模型占位。

### 设计决策

D1. **长期优化 download utility，第一版先实现 preference** - 长期目标是
`download_utility_score`，但 Phase 1 只实现 `preference_score`。`quality_score` 继续归
ADR-024 所有，`novelty_or_redundancy_score` 先作为确定性策略存在。

D2. **显式评分是强标签** - `MovieRatings` 中的 1-5 分和显式标签是主要标签。
`ContentPreferences` 中的维度偏好是强特征，也可成为 label prior。下载完成、保留、dedup、
重新下载和手动操作仅作为弱信号。

D3. **第一版模型保持结构化和可解释** - 第一版可训练模型应使用 regularized regression、
ordinal regression、gradient boosted trees 或其他轻量结构化模型。在线 LLM 推理不进入 hot path。

D4. **数据不足时不训练** - 在至少有 200 条显式影片评分前，继续使用版本化规则分数。更复杂模型应等待
更多数据，例如 500+ 条显式评分。

D5. **特征输入优先来自 ADR-022 数据** - Phase 1 特征限定为 `MovieMetadata`、
`MovieRatings` 和 `ContentPreferences`：演员、片商、发行商、导演、系列、类别、tags、
JavDB 分数、想看/看过人数、发行新鲜度、用户评分和维度偏好。

D6. **隐式行为是旁路信号** - 保留、删除、dedup、手动重新下载和下游刮削结果可以保存为
`implicit_signal_summary` 或用于 backtesting，但不驱动 Phase 1 的核心训练标签。

D7. **持久化预测，不在请求 hot path 计算** - Web/API 和 pipeline 消费者从 D1 读取
`MoviePreferencePredictions`。训练和批量预测通过 CLI 或 workflow 离线运行。

D8. **模型 artifact 不作为 D1 blob 存放** - D1 保存模型 registry 元数据、指标、状态和
artifact URI。Artifact 本身是 JSON model artifact，放在对象存储、GitHub Actions artifact
storage 或其他 repo 外 artifact 位置。极小的 JSON 规则 artifact 如有必要可以存在 D1，但 D1
不作为模型 blob store。

D9. **Candidate 模型可并存，生产只读一个 primary** - Model registry 行可使用
`candidate`、`primary`、`archived`、`failed` 等状态。生产消费者在每个 policy scope 只读取一个
primary 模型，同时 candidate 可以生成 shadow predictions 供比较。

D10. **晋升需要 gate 和人工确认** - Candidate 模型必须通过离线指标、时间切分验证、排序质量检查、
校准检查、回归保护和 shadow 分歧审查，然后由显式 CLI/API 操作晋升为 primary。

D11. **Pipeline 消费必须有 gate** - Web/API 可以直接展示分数，但 ingestion 从
`PREFERENCE_POLICY_MODE=shadow` 或 `assist` 开始。自动跳过或优先级改变需要后续
`enforce` rollout gate。

### 目标函数

系统级 utility 可以表示为：

```text
download_utility_score =
  combine(preference_score,
          quality_score,
          novelty_or_redundancy_score,
          policy_constraints)
```

ADR-025 只拥有 `preference_score`。

- `preference_score`：影片内容是否符合用户偏好？
- `quality_score`：可用 torrent 是否足够好？归 ADR-024 所有。
- `novelty_or_redundancy_score`：相对于 history、inventory、dedup 状态是否新颖或有用？初始为规则。
- `policy_constraints`：存储、分类、安全和 operator 控制的 gate。

### 标签策略

归一化训练目标为 `[0, 1]` 内的 `utility_label`，但 Phase 1 主要从显式偏好数据映射：

- 1-5 分 `MovieRatings` 映射为有序偏好标签。
- 显式 like/dislike 标签和维度级 `hearted` 偏好补充 label 与 feature context。
- 隐式行为只可作为低置信度弱证据补充缺口，并必须标明较低 confidence。

第一版可训练模型应使用时间切分验证，而不是随机切分，因为生产问题是用过去评分预测未来偏好。

### 预测契约

每条预测都必须足够可解释，供 Web UI、API 客户端和 pipeline 日志使用：

- `score`；
- `confidence`；
- `model_version`；
- `feature_schema_version`；
- `top_positive_reasons_json`；
- `top_negative_reasons_json`；
- `feature_group_scores_json`；
- `computed_at`；
- `input_snapshot_hash`。

原因分组示例包括演员、片商、发行商、导演、系列、tags、类别、JavDB 评分和显式用户偏好。

### 数据模型

`MoviePreferencePredictions` 保存版本化预测：

- `movie_href`、`video_code`；
- `model_version`、`feature_schema_version`；
- `score`、`confidence`；
- `top_positive_reasons_json`、`top_negative_reasons_json`；
- `feature_group_scores_json`；
- `input_snapshot_hash`、`computed_at`；
- `prediction_status`，例如 `ready`、`missing_inputs` 或 `stale`。

`PreferenceModelRegistry` 保存模型元数据：

- `model_version`、`policy_scope`；
- `status`，例如 `candidate`、`primary`、`archived` 或 `failed`；
- `algorithm`、`feature_schema_version`；
- `trained_at`、`training_data_cutoff`；
- `artifact_uri`、`artifact_sha256`；
- `metrics_json`、`promotion_notes_json`。

D1 是 canonical source。SQLite 只作为本地调试镜像。

### 训练与服务流程

训练和预测都是离线操作：

1. 从 D1 抽取训练行。
2. 使用版本化 feature schema 构建特征向量。
3. 训练 candidate 模型或版本化规则 artifact。
4. 保存 artifact，并写入 `PreferenceModelRegistry` candidate 行。
5. 批量生成预测，写入 `MoviePreferencePredictions`。
6. 对比 candidate prediction 与 primary model。
7. 只有通过显式操作才能把 candidate 晋升为 primary。

Web/API 和 pipeline 路径不训练模型。如果预测缺失或过期，它们返回该状态，而不是在线训练。

### 晋升 Gate

Candidate 晋升必须满足：

- 最小显式评分数量；
- 时间切分验证；
- 排序质量，例如 NDCG@20 或 Precision@20；
- 按分数 bucket 的校准检查；
- 对已知高分和低分影片的回归保护；
- candidate 与 primary 的 top disagreement review；
- 通过 CLI/API 显式人工确认。

训练任务不得自动切换 primary 模型。

## 后果

### 正面

- 偏好建模专注于用户口味，不吞并 torrent 质量和 dedup 决策。
- 预测结果可解释、可缓存、可审计、可版本化。
- Web/API 可以在 pipeline enforce 前先获得有用评分。
- Candidate 模型可以在晋升前安全比较。
- 离线训练避免给请求和 ingestion hot path 增加延迟或不稳定性。

### 负面

- Phase 1 需要足够显式评分后，可训练模型才会比规则更有价值。
- 批量预测会引入 stale/missing prediction 状态，消费者必须处理。
- 第一版模型不会从所有隐式 pipeline 行为中学习。
- Artifact 存储和 registry 管理增加了运维面。

### 风险

- **标签稀疏导致模型过拟合少数偏好演员** - 缓解方式：保持 200+ 评分训练 gate，使用正则化，并报告 confidence。
- **隐式行为污染偏好标签** - 缓解方式：隐式行为不进入 Phase 1 核心 label 路径。
- **模型升级悄悄改变 pipeline 行为** - 缓解方式：生产只读一个 primary model，晋升必须显式，pipeline 从 shadow 或 assist 模式开始。
- **解释误导使用者** - 缓解方式：每条 prediction 持久化 feature schema version 和结构化 feature-group contribution。

## 实施路线图

| 阶段 | IMP | 交付内容 | 推迟内容 |
| --- | --- | --- | --- |
| Phase 1 | Future IMP | D1 prediction 和 model registry schema、版本化规则 baseline、批量预测、Web/API 读取路径 | 可训练模型和 pipeline enforce |
| Phase 2 | Future IMP | 离线训练 CLI/workflow、candidate model artifact、metrics、shadow 对比 | 自动晋升 |
| Phase 3 | Future IMP | Pipeline shadow/assist 消费和晋升流程 | 默认 enforce mode |

## 参考

- [ADR-022](../_archive/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.zh.md) - metadata、ratings、content preferences 的数据基础。
- [ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.zh.md) - torrent 质量证据和 `quality_score` 归属。
- `docs/design/ADR-022-User-Preference-Foundation/IMP-ADR022-01-db-schema.md` - 偏好数据 D1-first schema 模式。
- `docs/design/ADR-022-User-Preference-Foundation/IMP-ADR022-03-preference-repo.md` - rating/preference 写入的 repository 和 API 形态。

## 状态日志

- 2026-05-27：以 ADR-025 提出。
