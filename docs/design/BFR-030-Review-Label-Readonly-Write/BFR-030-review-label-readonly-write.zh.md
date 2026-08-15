# BFR-030：review-label 写入接受 readonly JWT，与「仅 admin」的文档不符

**Status**: Fixed
**Date**: 2026-08-09
**Severity**: High
**Affected**: `apps/api/routers/quality.py`、`server/routes/quality.ts`（`TongWu/JAVDB_AutoSpider_Web`）
**Related**: [ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.zh.md)、[IMP-ADR024-08](../ADR-024-Torrent-Quality-Evidence/IMP-ADR024-08-phase2-assist.md)、[ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.zh.md)、issue #270

---

## Symptom

没有实际事故，也没有已知的被利用记录。该缺陷是在公开镜像仓库执行 `dev` → `main` 提升时由 review 发现的（TongWu/JAVDB_AutoSpider#153），记录为 issue #270，并已对照代码确认。

`POST /api/quality/review-labels` 接受 **readonly** JWT。任何已认证的 readonly 用户都可以对任意评估写入、覆盖或翻转运维的 accept/reject/skip 标注。

## Root Cause

路由声明了错误的依赖：

```python
def write_review_label(
    body: ReviewLabelRequest,
    _user=Depends(_require_auth),      # 只做认证，不校验 role
) -> ReviewLabelResponse:
```

`_require_auth` 会解码 JWT、拒绝非 access token 并施加限流，但它从不检查 `payload["role"]`。角色校验位于**另一个**依赖 `require_role("admin")` 中——它内部包裹 `_require_auth` 并追加检查。

而 API reference 从该端点上线起就写着「仅 admin」：

> `POST /api/quality/review-labels` — admin-only. …

所以这不是一个尚未决定的策略，而是**已明文声明、但接线从未实现**的意图。没有任何机制能发现这类漂移：文档是散文，而该路由的所有测试都以 admin 身份认证，readonly 调用者从未被覆盖到。

有两点让它不只是「一行笔误」：

1. **被保护的资源是共享的，而非用户私有的。** review 标注正是 ADR-024 Phase 3 用来设定质量阈值的训练/调优数据集。一个 readonly 用户的编辑，会改变系统日后对**所有人**的自动决策。大多数 readonly/admin 缺口泄露的是「某个人的数据」；而这一处让低权限调用者能够操纵未来的自动化行为。
2. **它在两个后端被重复实现错了。** 按 ADR-017，该端点有两份实现。TypeScript Worker 仅依赖全局 `requireAuth()` 中间件，还附了一条「认证已处理」的注释——同样的错误假设，却是各自独立得出的。一个只写在散文里的角色决策，会被每个后端各错一遍。

## Fix

Python —— 替换依赖；`require_role` 内部依赖 `_require_auth`，因此 reviewer 身份读取的 payload 不变，OpenAPI 的 `BearerAuth` 要求也照常传播：

```python
    _user=Depends(require_role("admin")),
```

TypeScript —— 加上该仓库所有其他 admin 变更操作都在用的中间件：

```ts
qualityRoutes.post("/review-labels", requireRole("admin"), async (c) => {
```

两侧都新增了 readonly 调用者测试，断言返回 `403` **且**没有写入任何行；两者在各自修复前的路由上均会失败。`docs/api/openapi.json` 已重新生成。

## Side Effects

此前能写入标注的 readonly 用户现在会收到 `403`。这正是既定契约，API reference 本就如此声明，因此无需改文档——是代码向文档靠拢。

无需数据迁移：已有标注保留其存储时的 `reviewer` 值。如果确实曾用 readonly 账号进行过标注，这些行与 admin 写入的行无法区分；`reviewer` 列记录的是 JWT subject，若有需要可按用户名审计。

## Follow-Up

- [x] Python：路由改用 `require_role("admin")` 并补回归测试。
- [x] TypeScript：`requireRole("admin")` 并补回归测试（`TongWu/JAVDB_AutoSpider_Web` 的 `claude/verify-fix-open-issues-n8srgf` 分支）。
- [ ] 考虑增加一个契约测试，将文档声明的角色要求与实际依赖接线绑定校验，避免「只写在散文里」的角色决策再次漂移。本次未纳入范围。
