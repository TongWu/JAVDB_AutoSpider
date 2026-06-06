# 媒体服务器设置

本页说明如何接入 Emby 和 Plex 媒体服务器，使 ADR-033 **消费轮次（consumption
pass）** 能够拉取播放/评分证据并写入 `ConsumptionSignal` 行。

## 概述

消费轮次是媒体闭环对账（`apps.cli.ops.reconcile --pass consumption`）的一部分。它
轮询每个已配置的媒体服务器实例，通过高/中/低置信度 join-key 阶梯将媒体库条目标题
解析为 `video_code`，并写入：

- **`ConsumptionSignal`** — 每个成功解析的 `(video_code, instance, library)` 写入一行。
- **`UnresolvedMediaItem`** — 无法解析 `video_code` 的条目写入一行；**不会被静默丢弃**。

`--pass all`（默认值）会在 acquisition 和 ownership 轮次之后包含 consumption 轮次。

## 配置结构

`config.py` 中的 `MEDIA_SERVERS` 是一个 dict 列表。完整字段说明参见
[`config.py.example`](../../../../config.py.example)（搜索 `MEDIA_SERVERS`）。字段速览：

| 字段 | 必填 | 说明 |
|------|------|------|
| `type` | 是 | 媒体服务器类型。可选：`'emby'`、`'plex'`。 |
| `instance` | 是 | 唯一连接标识符；写入 `ConsumptionSignal.instance`。所有条目中必须唯一。 |
| `base_url` | 是 | 服务器基础 URL，如 `'http://192.168.1.50:8096'`。无需尾部斜线。 |
| `token` | 是 | API 令牌（见下方各节）。在所有日志输出中均被脱敏。 |
| `libraries` | 否 | 要扫描的媒体库**名称**列表。省略（或设为 `[]`）时扫描该服务器上所有媒体库。 |

令牌以明文形式存储在 `config.py` 中，文件在静态存储时已加密（存为 `config.py.enc`），
**绝不提交到仓库**。

## 获取凭据

### Emby API 密钥

1. 打开 Emby 管理后台，进入 **管理 → API 密钥**。
2. 点击 **新建 API 密钥**，输入描述（如 `javdb-autospider`）后确认。
3. 复制生成的密钥，作为 `token` 的值填入。
4. 适配器会将该密钥作为 `X-Emby-Token` 请求头发送。

### Plex 令牌

1. 访问 `https://app.plex.tv` 并登录 Plex Web。
2. 打开任意媒体条目，点击 **⋮**（更多）菜单，选择 **获取信息**。
3. 点击信息面板底部的 **查看 XML**。
4. 在打开的 URL 中，复制 `X-Plex-Token` 查询参数的值。

也可参考官方指南：
`https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/`

适配器会将该令牌作为 `X-Plex-Token` 查询参数发送。

## 配置示例

### 单个 Emby 实例，指定媒体库

```python
MEDIA_SERVERS = [
    {
        'type': 'emby',
        'instance': 'emby-nas',
        'base_url': 'http://192.168.1.50:8096',
        'token': 'your_emby_api_key',
        'libraries': ['JAV'],  # 只扫描名为 "JAV" 的媒体库
    },
]
```

### 单个 Plex 实例，扫描全部媒体库

```python
MEDIA_SERVERS = [
    {
        'type': 'plex',
        'instance': 'plex-home',
        'base_url': 'http://192.168.1.51:32400',
        'token': 'your_plex_token',
        # 省略 'libraries' -> 扫描所有媒体库
    },
]
```

### 多实例配置

```python
MEDIA_SERVERS = [
    {
        'type': 'emby',
        'instance': 'emby-nas',
        'base_url': 'http://192.168.1.50:8096',
        'token': 'your_emby_api_key',
        'libraries': ['JAV'],
    },
    {
        'type': 'plex',
        'instance': 'plex-home',
        'base_url': 'http://192.168.1.51:32400',
        'token': 'your_plex_token',
    },
]
```

`instance` 值在所有条目中必须唯一。消费轮次对每个实例独立运行；某个实例失败会被
记录并跳过，不会中止其他实例的处理。

## 媒体库名称解析

设置了 `libraries` 时，适配器会从服务器拉取完整媒体库列表，按名称精确匹配。如果某
个列表中的名称在该服务器上不存在任何媒体库，则跳过并记录警告——本次运行不会失败。

省略 `libraries` 时，扫描该服务器上的所有媒体库。

## 运行消费轮次

### 本地运行

```bash
STORAGE_BACKEND=d1 python3 -m apps.cli.ops.reconcile --pass consumption --json
```

若 `MEDIA_SERVERS` 为空，此命令为 no-op。若 `MEDIA_SERVERS` 格式有误，CLI 会将
错误信息输出到 stderr 并以退出码 `1` 结束。

### 通过 GitHub Actions 运行

`ReconcileLibrary.yml` 每小时执行一次 `--pass all`，其中包含消费轮次。`MEDIA_SERVERS_JSON`
secret 在运行时提供服务器列表（详见 [GitHub Actions 设置](github-actions-setup.md#media-servers-secret)）。

## 参见

- [CLI 参考 — 采集结果对账 CLI](../developer/cli-reference.md#采集结果对账-cli)
- [GitHub Actions 设置](github-actions-setup.md)
- [`config.py.example`](../../../../config.py.example) — 含注释的完整 `MEDIA_SERVERS` 配置块
