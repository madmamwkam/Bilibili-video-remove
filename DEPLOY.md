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
python -m src.main
```

选择 `1. 首次设置`，按提示操作：

1. **扫码登录副账号**：用 B站App 扫描终端中的二维码（120秒超时）
2. **扫码登录主账号**：同上
3. **输入源收藏夹ID**：副账号中要迁移的收藏夹的 `media_id`
4. **输入目标收藏夹ID**：主账号中的目标收藏夹的 `media_id`

> **如何获取收藏夹ID**：在浏览器中打开收藏夹页面，URL 中 `fid=` 后的数字即为 `media_id`。
> 例如：`https://space.bilibili.com/xxx/favlist?fid=12345678` 中 `12345678` 即为 ID。

配置将保存到 `config.json`。

### 手动执行一次转移

```bash
python -m src.main
```

选择 `2. 立即执行一次转移`。

### 启动定时守护模式

```bash
python -m src.main
```

选择 `3. 启动定时守护模式`。默认每 24 小时自动执行一次（可在 `config.json` 中修改 `interval_hours`）。

按 `Ctrl+C` 停止守护进程。

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
  }
}
```

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
