# BFR-036：`exclude_paths` 一改动，整个镜像历史就被重新编号

**Status**: Fixed
**Date**: 2026-08-15
**Severity**: High
**Affected**: `.github/workflows/publish-to-public.yml`、`.publish-config.yml`、`.github/scripts/publish_mirror.py`
**Related**: [PR #157](https://github.com/TongWu/JAVDB_AutoSpider/pull/157)、[PR #154](https://github.com/TongWu/JAVDB_AutoSpider/pull/154)、[BFR-034](../BFR-034-Sqlite-Lastrowid-As-D1-Foreign-Key/BFR-034-sqlite-lastrowid-as-d1-foreign-key.zh.md)

---

## Symptom

公开仓库的 `dev → main` 晋升 PR（PR #157）显示 **1450 个文件变更、约 +408K 行**，
而真实内容差异只有 **103 个文件 / +10298 / −245**。下游工具随之全面失效：

- CodeRabbit 直接跳过审查（超过其 150 文件上限）。
- CodeQL 把历史遗留问题当成新增告警，它自己的检查结果里也写了这句话：
  *"Alerts not introduced by this pull request might have been detected because
  the code changes were too large."*
- 合并时需要手工解决数十个 `add/add` 冲突——同一份内容在两条互不相关的历史上
  各自"新增"了一遍。

`git merge-base origin/dev origin/main` 返回空：两个分支**完全没有共同祖先**。

一周前的 PR #154 出现过较轻的同类现象，其 PR body 把原因归结为「重写历史是发布
固有的代价」。

## Root Cause

发布流程每次都对**整个**私有历史跑一遍 `git filter-repo`，再把结果强推到公开仓库
的 `dev` 分支。

PR #154 body 里的最初判断——「filter-repo 每次运行都会重新生成全新 SHA，这是发布
无法避免的结构性代价」——是**错的**，而且这个错误判断本身有害：它把一个可修复的
缺陷描述成了既定事实。`git filter-repo` 是确定性的。对同一份历史用同一组参数独立
运行两次，2621 个重写 commit 的输出逐字节一致。（该计数针对私有 `main`；下文的 2618
是同一份历史截断到 `868ce5ff` 后的结果，即两组参数做对比时所用的 commit。）

真正的触发因素是：**`exclude_paths` 本身就是那组参数之一**。改动这个列表，历史中
每个 commit 的 tree 都会变，于是每个 commit 都被重新编号，发布出的历史与上一次发布
的历史再无任何祖先关系。

用同一个固定的私有 commit（`868ce5ff`，即 *"fix(ops): stop the reconciler deleting
missingFiles torrents unverified (#275)"*）分别在两组参数下重放，验证如下：

| Filter 参数 | 产出 SHA | 出现在哪里 |
| --- | --- | --- |
| `7c6f3f8c` 之前 | `fcebe702` | 公开 `main`（2026-08-09 发布） |
| `7c6f3f8c` 之后 | `c28e40c3` | 公开 `dev`（2026-08-15 发布） |

触发点是 `7c6f3f8c`（2026-08-12），即夹带在 BFR-034 PR 中的 *"make publish exclusion
globs effective"* 改动。它把 glob 条目从 `--path` 改路由到 `--path-glob`，并替换了
dotenv 模式集——这两处修改本身都是正确的，但都没有提示它会重置整个镜像。

有两个细节让代价格外清晰：

- **2618 个 commit 中有 2612 个**被重新编号，分歧点一直回溯到 2025-06-27。
- 发布出的 **tree 逐字节相同**——两组参数在发布快照处都产出 tree `efe0185c`。
  也就是说，整条祖先链的损失换来的内容变化是**零**。

`exclude_paths` 自 2026-01 以来已改动 **7 次**，所以这是个会周期性复发的陷阱，
而非偶发故障。

### 第二个慢性缺陷

即便不改配置，workflow 每次都会强推一条「从未见过公开仓库」的历史。两个后果：

1. 上一轮的 `Sync from private repository` commit 每次都被孤立。
2. 直接提交在公开仓库上的 commit（例如 `044e5a71 Delete
   .github/workflows/ReconcileLibrary.yml`）留在 `main` 上，却被从 `dev` 抹掉。

于是 `main` 永远不可能成为 `dev` 的祖先，每次晋升都会产生幽灵冲突。PR #154 遇到的
就是这个。更深层的问题是：**`dev` 是一条被强推的镜像分支，却被当成可以发 PR 的普通
分支**——镜像与基于合并的晋升流程在架构上不兼容。

## Fix

发布流程改为 **append-only（只追加）**。`git filter-repo` 已完全移除；所有擦除与
改写逻辑集中在 `.github/scripts/publish_mirror.py`。

每个尚未进入镜像的私有 commit，会被重放为一个公开 commit，接在镜像**当前 tip**
之上，过程中剥离排除路径并应用 workflow 改写。因此每次发布都是 fast-forward：

- 改动 `exclude_paths` 只影响此后发布的 commit，绝不扰动已发布历史；
- 晋升 PR 永远展示真实差异；
- 正常路径下**绝不强推**公开仓库——推送被拒会作为错误暴露出来，而不是强行覆盖。

关键设计：

- **同步游标（sync cursor）。** 每个重放出的 commit 携带 `Private-Commit: <sha>`
  trailer。从镜像 tip 可达的最新一条即为续跑起点，无需任何旁路状态文件。它记录的是
  「最后一个**产出了**公开 commit 的私有 commit」；擦除后为空的 commit（例如只动
  `reports/` 的 `Auto-commit`）不产出任何东西，下次运行时重新检查一遍即可。
- **保留追溯性擦除能力。** append-only 无法从已发布的 commit 中移除某个路径，因此
  保留了 `--full`（workflow 输入 `full_remirror`）：按今天的 `exclude_paths` 从根
  重建每一个 commit，然后强推。同一套引擎，因此"排除"只有一个定义。它只走
  first-parent，所以重建后的镜像是线性的。
- **tree 通过 index 构建**，全程不落工作区。早期草稿曾把每个 commit 展开到磁盘再跑
  `git add -A`；而 `git add` 会遵守 `.gitignore`，导致 `.dockerignore`（本仓库中它
  既被 track、又被 `.gitignore` 列出）被静默地从镜像中丢弃。这个 bug 是被下文的
  tree 等价性校验抓到的，现在由一个测试和一条完整性断言
  （`assert_tree_complete`）共同锁定——只要构建出的 tree 丢失了任何未被要求排除的
  路径，任务立即失败。

### 评审过程中补充的三道防护

append-only 模型有三种从外部被破坏的方式，现在都由代码强制而非仅靠文档约定：

- **从功能分支发布。** 游标记录的是源 commit，因此从分支发布会写入一个 squash 或
  rebase 合并后不在默认分支里的 SHA——此后每次发布都会以 "not an ancestor" 中止。
  workflow 现在拒绝从任何非默认分支执行真发布；dry-run 仍放行，因为它不推送任何东西。
- **直接在镜像分支上提交。** fast-forward 并不能证明没有内容被破坏：每个 tree 都是
  完整快照，所以直接提交会「既 fast-forward 又消失」。构建步骤现在以最新一个带
  `Private-Commit` trailer 的 commit 为锚点，报告其上的任何提交——但仅在那些内容
  确实会丢失时才拦截。不改动 tree 的提交（把公开 `main` 合回镜像）会放行；其改动已被
  本次发布重现的提交也会放行（即按错误提示把修改移入了私有仓库）。以来源为锚点而非
  重建 tree 做对比很关键：重建树对比会在每次 `exclude_paths` 变更时误报，因为旧配置
  发布出的 tree 与新配置产出的 tree 本就不同。`allow_public_drift` 可在核对路径后放行。
- **晋升 PR 用 squash 或 rebase。** 两者都会让 `main` 得到 `dev` 不含的 SHA，于是
  merge base 停在上一次晋升处，已晋升的改动重新出现——正是本次修复要消除的膨胀 diff。
  现已在 handbook 中写成明确规则，并记录了「把 `main` 合回 `dev`」的补救方式。

### 验证

上线前已针对真实仓库验证：

- 把 PR #157 背后的私有 commit 重放到 #157 之前的公开 `main` 上，产出的 tree 与
  filter-repo 当初发布的**完全一致**（`31ce935e9a4a…`），相对 `main` 的差异正好是
  真实的 **103 files / +10298 / −245**。
- `--full` 对整个私有历史重建，产出**同一个 tree**，耗时 177 秒。
- 重放具备幂等性，且输出确定。

### 变更文件

| 文件 | 变更 |
| --- | --- |
| `.github/scripts/publish_mirror.py` | 新增——完整的镜像构建器 |
| `.github/workflows/publish-to-public.yml` | 重写；19 步 → 11 步，移除 filter-repo 及四个内联 sed/python 改写步骤 |
| `.publish-config.yml` | 条目语义改为指向脚本；说明改动现在是廉价的；脚本自身加入 `exclude_paths` |
| `tests/unit/test_publish_mirror_append_only.py` | 新增——BFR-036 回归测试套件 |
| `tests/unit/test_publish_config_exclusion_globs.py` | 改为调用生产匹配器，不再自带副本 |
| `docs/handbook/{en,zh}/developer/publish-to-public.md` | 按 append-only 重写 |

## Side Effects

- **公开镜像此后的历史形态会变化。** commit 按 first-parent 重放，一个私有 commit
  对应一个公开 commit。私有历史中的 merge commit 会被压平，而不是被复现。
- **`--full` 按设计就是破坏性的。** 它会替换已发布历史，因此任何从旧历史分叉出来的
  分支（包括公开 `main`）都会失去共同祖先。这是追溯性擦除的固有代价，现在它是一个
  需要显式勾选的 workflow 输入，而不再是默认行为。
- **append-only 之前发布的镜像没有游标。** 本次改动落地后的第一次发布必须传入
  `bootstrap_private_commit`，值为镜像 tip 对应的私有 commit。脚本拒绝猜测。
- **末尾被剪枝的 commit 每次都会重扫。** 无害（每个 commit 耗时毫秒级，且不产出任何
  东西），并且能让 trailer 保持诚实。
- 发布内容本身没有变化：擦除产出的 tree 与此前一致。

## Follow-Up

- [x] 用 append-only 重放替换全历史重写
- [x] 正常路径下停止强推公开仓库
- [x] 以显式 `full_remirror` 输入保留追溯性擦除能力
- [x] 为重新编号不变量与 `.gitignore` 丢文件问题补充回归测试
- [x] 更新 EN/ZH 发布手册
- [ ] 本次改动落地后的首次发布必须**从 `main` 分支**触发，并传入
      `bootstrap_private_commit`（`b60de9f1e9e0e25d0ee0ff42fd3026177885d57a`，
      即公开 `dev` 当前对应的私有 commit）
- [ ] 决定如何处理 bootstrap 门禁在实际镜像上报出的两个陈旧路径。
      `docs/design/BFR-023-Concurrent-Run-Cross-Session-Commit/` 在公开 `main` 上
      仍以本 BFR 家族改号前的编号存在（后来改为 BFR-035）；由于 `main` 从未被强推，
      旧副本得以留存，并被 PR #157 合并进了 `dev`。发布会将其移除，这是正确的——
      确认后传一次 `allow_public_drift` 即可
- [ ] 考虑对公开 `dev` 分支加保护，禁止直接推送，避免 fast-forward 保证被对侧破坏
