# 发布到公开仓库指南

代码如何从私有仓库同步到公开镜像。

## 目录

- [概览](#概览)
- [工作原理](#工作原理)
- [配置](#配置)
- [触发方式与输入](#触发方式与输入)
- [所需 Secrets](#所需-secrets)
- [从 dev 晋升到 main](#从-dev-晋升到-main)
- [全量重镜像（破坏性）](#全量重镜像破坏性)
- [FAQ](#faq)

## 概览

```text
Private repo (main branch)
    ↓ (手动触发)
GitHub Actions: .github/scripts/publish_mirror.py
    ↓ (擦除排除路径，应用 workflow 改写)
Private repo (public-sync branch)
    ↓ (fast-forward push)
Public repo (dev branch)
    ↓ (晋升 PR)
Public repo (main branch)
```

镜像以 **append-only（只追加）** 方式构建。每个尚未进入镜像的私有 commit，会被重放
为一个公开 commit，接在镜像当前 tip 之上。每次发布都是 fast-forward，正常路径下
绝不强推公开仓库。

## 工作原理

1. **从同步游标续跑。** 每个已发布的 commit 都携带 `Private-Commit: <sha>`
   trailer，记录它由哪个私有 commit 构建而来。从镜像 tip 可达的最新一条即告诉下次
   运行从何处续跑——没有独立的状态文件。

2. **重放每个新的私有 commit。** 按 first-parent 顺序，对每一个：
   - 通过 git index 读入该 commit 的 tree（全程不落工作区）；
   - 丢弃所有匹配 `exclude_paths` 的路径；
   - 应用配置好的 workflow 改写；
   - 把结果提交到上一个公开 commit 之上，保留原始作者与日期。

3. **剪掉擦除后为空的 commit。** 只动了 `reports/` 的 `Auto-commit` 不会产出任何
   公开 commit。

4. **推送前校验。** 两道闸门，均为致命：
   - 任何新 commit 中都不得出现匹配 `exclude_paths` 的路径；
   - 不得**丢失**任何擦除规则并未要求丢弃的路径。

5. **Fast-forward 推送**到公开目标分支。推送被拒即任务失败——这意味着镜像在我们脚下
   发生了变化，而静默覆盖它正是本设计要防止的事。

> **为什么改成 append-only？** 此前发布流程用 `git filter-repo` 重写整个历史再强推。
> filter-repo 是确定性的，但 `exclude_paths` 本身就是它的输入之一——因此改动这个列表
> 会让每个 commit 重新编号，并摧毁与公开 `main` 的 merge base。详见
> [BFR-036](../../../design/BFR-036-Mirror-Rewrite-Rekeys-History/BFR-036-mirror-rewrite-rekeys-history.zh.md)。

## 配置

所有发布配置集中在 `.publish-config.yml`：

```yaml
# 不进入公开仓库的文件/目录
exclude_paths:
  - "reports/"
  - "logs/"
  - "config.py"          # 已解析的密钥 —— 绝不发布
  - "CLAUDE.md"
  - "AGENTS.md"
  - "CONTEXT.md"
  - "*.db"
  - ".github/workflows/publish-to-public.yml"
  - ".github/scripts/publish_mirror.py"
  - ".publish-config.yml"
  # ... 完整列表见 .publish-config.yml

# 应用到公开镜像的逐 workflow 改写
workflow_modifications:
  disable_schedule:        # 注释掉 `schedule:` 触发器
    - ".github/workflows/DailyIngestion.yml"
  enable_push_trigger:     # 取消注释公开仓库的 push 触发器
    - ".github/workflows/docker-publish-ghcr.yml"
  disable_push_trigger:    # 注释掉 PRIVATE_ONLY_PUSH 区块
    - ".github/workflows/TestIngestion.yml"
  disable_all_triggers: [] # 注释掉整个 `on:` 区块

branches:
  publish_branch: "public-sync"
  public_target_branch: "dev"
```

### 条目语义

| 条目形态 | 匹配方式 |
|---|---|
| 不含 glob 元字符（`*`、`?`、`[`） | 精确路径；若指向目录，则其下所有路径 |
| 含 glob 元字符 | `fnmatch`，其中 `*` 也跨越 `/` —— 所以 `*.env` 能匹配任意深度的 `.env` |

以 `**/` 开头的 glob 要求前面必须有一个 `/`，因此它永远覆盖不到根层级的情况。
需要搭配一个字面量条目（`secrets/` 与 `**/secrets/` 并列）。

### 改动 `exclude_paths` 是廉价的——但不追溯

新增条目只影响**此后发布的 commit**。它**不会**把该路径从已发布的历史中移除；
那需要[全量重镜像](#全量重镜像破坏性)。

## 触发方式与输入

仅支持手动触发：GitHub Actions → "Publish to Public Repository" → "Run workflow"。

| 输入 | 用途 |
|---|---|
| `dry_run` | 构建镜像 commit 并跑完两道闸门，但不推送 |
| `target_branch` | 覆盖公开目标分支（默认取自配置） |
| `full_remirror` | **破坏性。** 重建全部历史并强推——见下文 |
| `bootstrap_private_commit` | 镜像 tip 对应的私有 commit。仅在镜像尚无 `Private-Commit` trailer 时需要传一次 |
| `allow_public_drift` | 覆盖镜像分支上存在、但本次发布不会重现的内容。仅在看过构建步骤列出的路径后使用 |

## 所需 Secrets

| Secret | 说明 |
|--------|------|
| `DEPLOY_KEY` | 访问私有仓库的 SSH key |
| `GIT_USERNAME` | 公开仓库认证用的 Git 用户名 |
| `GIT_PASSWORD` | 公开仓库的 PAT。需要 workflow 权限（classic：`repo` + `workflow`），因为 `GITHUB_TOKEN` 无法更新 `.github/workflows/**` |
| `GIT_REPO_URL_REMOTE` | 公开仓库 URL（HTTPS 格式） |

## 从 dev 晋升到 main

公开仓库的 `dev` 分支接收每一次发布；`main` 通过 pull request 从它晋升。

由于发布是 append-only 的，`dev` 始终是上一次晋升状态的后代，因此晋升 PR 展示的是
真实差异，并且能干净合并。

> **晋升 PR 必须用 merge commit 合并，绝不能用 squash 或 rebase。**
> merge commit 会让 `dev` 的 tip 成为 `main` 的祖先，于是下一次晋升的 merge base
> 就是你刚晋升的那个 commit，差异中只包含此后新发布的内容。squash 和 rebase 都会
> 给 `main` 产生 `dev` 中不存在的全新 SHA，导致 merge base 停留在**上上次**晋升处
> ——已晋升的改动会在下一个 PR 中再次出现，正是本设计要消除的那种膨胀 diff。

另外还有两件事会破坏这个保证——都要避免：

- **直接向公开 `dev` 提交。** 每个发布出的 commit，其 tree 是过滤后私有 commit 的
  完整快照而非补丁；因此直接提交在下次发布时**仍然是 fast-forward**，但它的内容会从
  tree 里消失。构建步骤会直接拒绝，并列出每一个将被丢失的路径。正确做法是把该改动
  移入私有仓库再从那里发布；只有在你已经看过那份清单、并确认要以镜像为准时，才使用
  `allow_public_drift`。
- **在两次晋升之间跑全量重镜像。** 它会替换 `dev` 的历史，于是 `main` 会失去与它的
  共同祖先。

万一某次晋升不慎用了 squash 或 rebase，补救办法是在下次晋升前把 `main` 合回 `dev`
（`sync-main-to-dev.yml`，手动触发）。这样能恢复共同祖先，且不重写任何历史。

## 全量重镜像（破坏性）

`full_remirror: true` 会按今天的 `exclude_paths` 从根重建每一个 commit，并**强推**
结果。

**仅用于把某个东西追溯性地从已发布历史中擦除**——即那种本就不该发布、且在旧 commit
中仍然可见的路径。

代价：

- 已发布历史被替换。任何从它分叉出来的分支——包括公开 `main`——都会失去共同祖先，
  于是下一个晋升 PR 会变成整仓库差异，并需要手工解决冲突。
- 重建出的历史是线性的：它只走 first-parent，因此私有仓库的 merge commit 会被压平。

全量重镜像之后，应当用新的 `dev` 重置公开 `main`，而不是对一条它已不再共享的历史
发起晋升 PR。

## FAQ

### Q：如何新增一个要排除的文件/目录？

把路径加到 `.publish-config.yml` 的 `exclude_paths` 中。它从下一次发布开始生效。
若还需要把它从已发布历史中移除，请执行[全量重镜像](#全量重镜像破坏性)。

### Q：推送到公开仓库被拒了，怎么办？

镜像分支不是所构建内容的 fast-forward——说明有人对它强推过或直接提交过。请手工核对
调和。**不要强推**，除非你打算做全量重镜像并接受随后重置 `main`。

### Q：任务提示没有同步游标，我该传什么？

该镜像是在 append-only 模式之前发布的，不带 `Private-Commit` trailer。找到它的 tip
对应的那个私有 commit，作为 `bootstrap_private_commit` 传入。脚本拒绝猜测，因为猜错
会静默地丢失或重复 commit。

### Q：如何让 self-hosted 的 job 在公开仓库上也能跑？

公开仓库无法访问私有的 self-hosted runner 池，所以任何 `runs-on: self-hosted` 的 job
在那边都会一直挂起。在该行内联标注：

```yaml
build-arm:
  runs-on: [self-hosted, ARM64]  # PUBLIC_RUNNER: ubuntu-24.04-arm
```

所有 `.github/workflows/*.yml` 中带此标记的 `runs-on:` 行都会被改写为
`runs-on: <name>`，因此 `.publish-config.yml` 里无需任何条目。单 token 形式与锁定
架构的数组形式都支持；替换值始终是单个 token。像 `${{ matrix.runner }}` 这样的表达式
形式不带标记，会原样保留。

### Q：某个 workflow 契约测试在本仓库通过，却在公开仓库失败，为什么？

因为镜像是一棵*被改写过*的树，不是拷贝。任何对 `.github/` 内容做断言的测试，看到的树
都可能与你提交的不同：

- 位于 `exclude_paths` 中的 workflow（例如 `publish-to-public.yml`）**不存在**，
  读取它会抛 `FileNotFoundError`；
- 带 `# PUBLIC_RUNNER` 标记的 `runs-on:` 行**已经被改写**。

请根据当前 checkout 是否为镜像来保护这类断言。`publish-to-public.yml` 的缺失就是判据：

```python
IS_PUBLIC_MIRROR = not (WORKFLOWS_DIR / "publish-to-public.yml").exists()

@pytest.mark.skipif(IS_PUBLIC_MIRROR, reason="rewritten on the public mirror")
def test_jobs_run_self_hosted():
    ...
```

实际用法见 `tests/unit/test_workflow_public_runner_markers.py`。

### Q：如何修改目标分支？

要么修改 `.publish-config.yml` 中的 `branches.public_target_branch`，要么在触发
workflow 时传 `target_branch` 做一次性覆盖。

若该分支在公开仓库上尚不存在，构建会从默认分支分叉出它，以保证有正常的 merge base、
始终可合并。分支由推送创建，不需要预先建好。

### Q：workflow 失败了，如何排查？

1. 查看运行日志——构建步骤会逐条列出它重放、跳过或拒绝的每个 commit。
2. 用 `dry_run: true` 重跑，可在不推送的前提下走完两道校验闸门。
3. 确认所有 secrets 配置正确。
