# 部署指南 - B站收藏夹跨端迁移工具

## 前置条件

- Python 3.10+ 已安装
- 终端支持 UTF-8 编码（用于显示 QR 码）
- B站主账号和副账号各一个

## 安装步骤

### 1. 激活虚拟环境

```bash
# Windows
bilibili-videos-remove\Scripts\activate

# Linux/macOS (如适用)
source bilibili-videos-remove/bin/activate
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

## 使用方式

### 首次设置

```bash
python -m src.main setup
```

按提示操作：

1. **扫码登录副账号**：用 B站App 扫描终端中的二维码（120秒超时）
2. **扫码登录主账号**：同上
3. **输入源收藏夹ID**：副账号中要迁移的收藏夹的 `media_id`
4. **输入目标收藏夹ID**：主账号中的目标收藏夹的 `media_id`

> **如何获取收藏夹ID**：在浏览器中打开收藏夹页面，URL 中 `fid=` 后的数字即为 `media_id`。
> 例如：`https://space.bilibili.com/xxx/favlist?fid=12345678` 中 `12345678` 即为 ID。

配置将保存到 `config.json`。

### 手动执行一次转移

```bash
python -m src.main transfer
```

### 启动定时守护模式

```bash
python -m src.main daemon
```

立即执行一次后，按 `config.json` 中的 `interval_hours` 间隔重复执行。按 `Ctrl+C` 停止。

### 指定配置文件路径

```bash
python -m src.main --config /path/to/config.json transfer
```

## 日志

- 控制台输出：INFO 级别
- 文件日志：`logs/transfer_YYYY-MM-DD.log`（DEBUG 级别）
- 日志轮转：单文件 10MB，保留 7 天

## config.json 结构

```json
{
  "sub_account": {
    "cookie": "SESSDATA=xxx; bili_jct=yyy; DedeUserID=zzz",
    "refresh_token": "...",
    "source_media_id": "副账号源收藏夹ID"
  },
  "main_account": {
    "cookie": "SESSDATA=xxx; bili_jct=yyy; DedeUserID=zzz",
    "refresh_token": "...",
    "target_media_id": "主账号目标收藏夹ID"
  },
  "task_schedule": {
    "interval_hours": 24
  },
  "anti_ban": {
    "read_delay_min": 3.0,
    "read_delay_max": 5.0,
    "write_delay_min": 10.0,
    "write_delay_max": 20.0
  }
}
```

### anti_ban 字段说明

B站会对高频操作触发风控，`anti_ban` 控制每次请求之间的随机休眠时长，是防封号的核心参数。

| 字段 | 默认值 | 含义 |
|------|--------|------|
| `read_delay_min` | `3.0` | 每翻一页收藏夹后，最少等待秒数 |
| `read_delay_max` | `5.0` | 每翻一页收藏夹后，最多等待秒数 |
| `write_delay_min` | `10.0` | 每次添加/删除操作后，最少等待秒数 |
| `write_delay_max` | `20.0` | 每次添加/删除操作后，最多等待秒数 |

实际等待时间在 `min` 和 `max` 之间随机取值，避免固定间隔被识别为机器人行为。

> **建议**：不要将写操作延迟设置过低。官方 App 的正常收藏操作间隔约为数秒，`write_delay_min` 建议不低于 `5.0`。

## 常见问题

### Cookie 过期
工具会自动检查并刷新 Cookie。如果刷新失败，重新运行首次设置即可。

### 风控熔断（ERROR: Circuit breaker TRIPPED）
B站检测到异常请求，工具将自动暂停 4 小时后重试。无需人工干预。

### QR 码显示异常
确保终端编码为 UTF-8。Windows 用户可在终端执行 `chcp 65001`。

### 视频"已存在"日志
正常现象。工具采用"全量直推"策略，已存在的视频会被 API 自动跳过，无需担心重复。

### 转移后自动删除源收藏夹
每条视频成功添加到目标收藏夹（或确认已存在）后，工具会自动将其从副账号源收藏夹中删除。删除失败不会中断流程，仅记录警告日志，该视频下次运行时会重新处理。
