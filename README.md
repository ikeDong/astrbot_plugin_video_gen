# astrbot_plugin_video_gen

AstrBot 视频生成插件，基于视频生成 API，支持文生视频、图生视频、多图参考和首尾帧生成。

## 功能

- **文生视频**：通过文字描述生成视频
- **图生视频**：上传单张图片 + 文字描述生成视频
- **多图参考**：上传多张参考图片生成视频
- **首尾帧**：上传首帧和尾帧图片，生成过渡视频

## 前置条件

- AstrBot >= 4.16
- Python 依赖：`aiohttp >= 3.8.0`
- 需要配置视频生成 API 的 Base URL 和 API Key

## 安装

将本插件放入 AstrBot 的 `data/plugins/` 目录，重启 AstrBot 即可自动加载。

## 配置

在 AstrBot WebUI 的插件配置页面中设置以下参数：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `base_url` | API Base URL | `https://zexitongxue.com/v1` |
| `api_key` | API Key（Bearer 认证） | （空） |
| `model` | 默认模型名称 | `sora-v3-pro` |
| `default_aspect_ratio` | 默认画面比例 | `16:9` |
| `default_seconds` | 默认视频时长（秒） | `5` |
| `default_resolution` | 默认分辨率 | `720p` |
| `poll_interval` | 轮询间隔（秒） | `8` |
| `max_poll_attempts` | 最大轮询次数 | `120` |

## 使用方法

### 1. 文生视频

```
/t2v <画面描述>
```

示例：

```
/t2v 一只猫在草地上奔跑
/t2v 海浪拍打沙滩，夕阳西下 --ratio 21:9 --seconds 10
```

### 2. 图生视频（单图）

发送一张图片，然后输入：

```
/i2v <描述>
```

示例：

```
/i2v 让画面中的水流起来
```

### 3. 多图参考

发送多张图片，然后输入：

```
/multi <描述>
```

### 4. 首尾帧

发送两张图片（第一张为首帧，第二张为尾帧），然后输入：

```
/startend <描述>
```

### 可选参数

所有指令均支持以下可选参数，追加在描述后面即可：

| 参数 | 说明 | 示例 |
|------|------|------|
| `--ratio` | 画面比例 | `--ratio 9:16` |
| `--seconds` | 视频时长（秒） | `--seconds 10` |
| `--resolution` | 分辨率 | `--resolution 1080p` |
| `--model` | 指定模型 | `--model sora-v3-pro` |

### 查看帮助

```
/vhelp
```

## 支持的画面比例

`16:9`、`9:16`、`4:3`、`3:4`、`1:1`、`21:9`、`adaptive`

## License

MIT
