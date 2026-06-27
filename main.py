import asyncio
import base64
import os
import re
import time
from pathlib import Path

import aiohttp
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig
from astrbot.api.message_components import Image, Plain, Video
from astrbot.core.utils.io import download_file


def _ensure_tmp_dir() -> Path:
    p = Path("data/plugins/astrbot_plugin_video_gen/tmp")
    p.mkdir(parents=True, exist_ok=True)
    return p


async def _image_to_data_url(img: Image) -> str:
    """将 Image 组件转为 data URL (base64)。如果已有 http(s) URL 则直接返回。"""
    # 优先使用 URL
    url = (img.url or "").strip()
    if url and url.startswith(("http://", "https://")):
        return url

    file_val = (img.file or "").strip()
    path_val = (img.path or "").strip()

    # 本地文件路径
    local_path = None
    if path_val and os.path.isfile(path_val):
        local_path = path_val
    elif file_val:
        if file_val.startswith("file://"):
            fp = file_val[7:]
            if os.path.isfile(fp):
                local_path = fp
        elif os.path.isfile(file_val):
            local_path = file_val

    if local_path:
        with open(local_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        ext = os.path.splitext(local_path)[1].lower().lstrip(".")
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext or 'png'}"
        return f"data:{mime};base64,{b64}"

    # base64 直接传入
    if file_val.startswith("base64://"):
        return f"data:image/png;base64,{file_val[9:]}"

    # 尝试当 URL
    if file_val.startswith(("http://", "https://")):
        return file_val

    raise ValueError(f"无法解析图片: file={file_val[:60]}, url={url[:60]}")


async def _download_to_local(url: str, dest_dir: Path, filename: str = None) -> str:
    """下载远程文件到本地，返回路径。"""
    if not filename:
        ext = ".mp4"
        if "?" in url:
            url_path = url.split("?")[0]
        else:
            url_path = url
        if "." in os.path.basename(url_path):
            ext = "." + url_path.rsplit(".", 1)[-1].lower()[:5]
        filename = f"vid_{int(time.time() * 1000)}{ext}"
    dest = dest_dir / filename
    await download_file(url, str(dest))
    return str(dest)


def _parse_params(text: str) -> dict:
    """从文本中提取可选参数，返回剩余 prompt 和参数字典。"""
    params = {}
    prompt = text

    # --ratio 16:9
    m = re.search(r"--ratio\s+(\S+)", prompt)
    if m:
        params["aspect_ratio"] = m.group(1)
        prompt = prompt[: m.start()] + prompt[m.end() :]

    # --seconds 5
    m = re.search(r"--seconds\s+(\d+)", prompt)
    if m:
        params["seconds"] = int(m.group(1))
        prompt = prompt[: m.start()] + prompt[m.end() :]

    # --resolution 720p
    m = re.search(r"--resolution\s+(\S+)", prompt)
    if m:
        params["resolution"] = m.group(1)
        prompt = prompt[: m.start()] + prompt[m.end() :]

    # --model xxx
    m = re.search(r"--model\s+(\S+)", prompt)
    if m:
        params["model"] = m.group(1)
        prompt = prompt[: m.start()] + prompt[m.end() :]

    return prompt.strip(), params


@register(
    "astrbot_plugin_video_gen",
    "astrbot",
    "基于 SD2 逆向 API 的视频生成插件",
    "1.0.0",
    "https://github.com/astrbot/astrbot_plugin_video_gen",
)
class VideoGenPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.base_url = config.get("base_url", "https://zexitongxue.com/v1").rstrip("/")
        self.api_key = config.get("api_key", "")
        self.default_model = config.get("model", "sora-v3-pro")
        self.default_ratio = config.get("default_aspect_ratio", "16:9")
        self.default_seconds = str(config.get("default_seconds", "5"))
        self.default_resolution = config.get("default_resolution", "720p")
        self.poll_interval = int(config.get("poll_interval", 8))
        self.max_poll = int(config.get("max_poll_attempts", 120))

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _build_body(self, prompt: str, extra: dict, **kwargs) -> dict:
        body = {
            "model": extra.get("model", self.default_model),
            "prompt": prompt,
        }
        if "aspect_ratio" not in extra:
            extra["aspect_ratio"] = self.default_ratio
        if "seconds" not in extra:
            extra["seconds"] = self.default_seconds
        if "resolution" not in extra:
            extra["resolution"] = self.default_resolution
        body.update(extra)
        # 合并 kwargs（image_url, first_frame_image 等）
        body.update(kwargs)
        return body

    async def _submit(self, body: dict) -> dict:
        """提交生成任务，返回 JSON 响应。"""
        url = f"{self.base_url}/video/generations"
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=body, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise Exception(f"提交失败 HTTP {resp.status}: {text[:500]}")
                import json

                return json.loads(text)

    async def _poll(self, task_id: str) -> dict:
        """轮询任务状态直到完成或超时。"""
        url = f"{self.base_url}/video/generations/{task_id}"
        async with aiohttp.ClientSession() as session:
            for i in range(self.max_poll):
                await asyncio.sleep(self.poll_interval)
                async with session.get(
                    url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        logger.warning(f"轮询 {task_id} 第{i+1}次 HTTP {resp.status}: {text[:200]}")
                        continue
                    import json

                    data = json.loads(text)
                    status = data.get("status", "").lower()

                    if status in ("completed", "succeeded", "success"):
                        return data
                    if status in ("failed", "error", "cancelled", "canceled"):
                        err = data.get("error", data.get("message", "未知错误"))
                        raise Exception(f"生成失败: {err}")
                    logger.debug(f"轮询 {task_id} 第{i+1}次: {status}")
        raise TimeoutError(f"轮询超时（{self.max_poll * self.poll_interval}秒）")

    def _extract_video_url(self, data: dict) -> str | None:
        """从任务完成响应中提取视频 URL。"""
        # 直接在顶层
        for key in ("video_url", "url", "output", "result"):
            val = data.get(key)
            if isinstance(val, str) and val.startswith(("http://", "https://")):
                return val
        # 嵌套
        for key in ("data", "result", "output", "video"):
            sub = data.get(key)
            if isinstance(sub, dict):
                for k2 in ("url", "video_url", "download_url"):
                    v = sub.get(k2)
                    if isinstance(v, str) and v.startswith(("http://", "https://")):
                        return v
            elif isinstance(sub, list) and len(sub) > 0:
                item = sub[0]
                if isinstance(item, str) and item.startswith(("http://", "https://")):
                    return item
                if isinstance(item, dict):
                    for k2 in ("url", "video_url", "download_url"):
                        v = item.get(k2)
                        if isinstance(v, str) and v.startswith(("http://", "https://")):
                            return v
        return None

    def _extract_task_id(self, data: dict) -> str | None:
        """从提交响应中提取任务 ID。"""
        for key in ("id", "task_id", "request_id", "generation_id"):
            val = data.get(key)
            if isinstance(val, str) and val:
                return val
        # 嵌套
        for key in ("data", "result"):
            sub = data.get(key)
            if isinstance(sub, dict):
                for k2 in ("id", "task_id", "request_id"):
                    v = sub.get(k2)
                    if isinstance(v, str) and v:
                        return v
        return None

    async def _do_generate(self, event: AstrMessageEvent, body: dict, hint: str = "") -> None:
        """完整的生成流程：提交 → 轮询 → 下载 → 发送视频。"""
        try:
            if hint:
                yield event.plain_result(f"{hint}")
            else:
                yield event.plain_result("正在提交生成任务...")

            resp = await self._submit(body)
            task_id = self._extract_task_id(resp)

            if not task_id:
                # 可能直接返回了视频 URL
                video_url = self._extract_video_url(resp)
                if video_url:
                    yield event.plain_result("生成完成，正在下载视频...")
                    tmp_dir = _ensure_tmp_dir()
                    local_path = await _download_to_local(video_url, tmp_dir)
                    yield event.chain_result([Video.fromFileSystem(local_path)])
                    return
                raise Exception(f"无法解析任务 ID，响应: {str(resp)[:300]}")

            yield event.plain_result(f"任务已提交，ID: {task_id}，开始等待生成...")

            result = await self._poll(task_id)
            video_url = self._extract_video_url(result)

            if not video_url:
                raise Exception(f"生成完成但未找到视频 URL，响应: {str(result)[:300]}")

            yield event.plain_result("生成完成，正在下载视频...")
            tmp_dir = _ensure_tmp_dir()
            local_path = await _download_to_local(video_url, tmp_dir)
            yield event.chain_result([Video.fromFileSystem(local_path)])

        except Exception as e:
            logger.error(f"视频生成失败: {e}")
            yield event.plain_result(f"生成失败: {e}")

    # ===== 指令 =====

    @filter.command("t2v", alias=["文生视频", "text2video"])
    async def text_to_video(self, event: AstrMessageEvent, prompt: str = ""):
        """文生视频: /t2v <描述> [--ratio 16:9] [--seconds 5] [--resolution 720p]"""
        if not prompt:
            yield event.plain_result("用法: /t2v <画面描述> 可选参数: --ratio --seconds --resolution --model")
            return

        clean_prompt, params = _parse_params(prompt)
        if not clean_prompt:
            yield event.plain_result("请输入画面描述")
            return

        body = self._build_body(clean_prompt, params)
        async for r in self._do_generate(event, body, "正在生成视频，请耐心等待..."):
            yield r

    @filter.command("i2v", alias=["图生视频", "image2video"])
    async def image_to_video(self, event: AstrMessageEvent, prompt: str = ""):
        """图生视频: 发送图片 + /i2v <描述>"""
        images = [c for c in event.get_messages() if isinstance(c, Image)]
        if not images:
            yield event.plain_result("请附上一张图片，并输入描述")
            return

        try:
            img_url = await _image_to_data_url(images[0])
        except Exception as e:
            yield event.plain_result(f"图片解析失败: {e}")
            return

        clean_prompt, params = _parse_params(prompt or "")
        body = self._build_body(clean_prompt or "让图片动起来", params, image_url=img_url)
        async for r in self._do_generate(event, body, "正在基于图片生成视频..."):
            yield r

    @filter.command("multi", alias=["多图参考", "multiimg"])
    async def multi_image_video(self, event: AstrMessageEvent, prompt: str = ""):
        """多图参考: 发送多张图片 + /multi <描述>"""
        images = [c for c in event.get_messages() if isinstance(c, Image)]
        if len(images) < 1:
            yield event.plain_result("请附上至少一张图片，并输入描述")
            return

        try:
            img_urls = [await _image_to_data_url(img) for img in images]
        except Exception as e:
            yield event.plain_result(f"图片解析失败: {e}")
            return

        clean_prompt, params = _parse_params(prompt or "")
        body = self._build_body(
            clean_prompt or "基于参考图片生成视频",
            params,
            referenced_images=img_urls,
        )
        async for r in self._do_generate(event, body, f"正在基于{len(img_urls)}张图片生成视频..."):
            yield r

    @filter.command("startend", alias=["首尾帧", "seframe"])
    async def start_end_frame(self, event: AstrMessageEvent, prompt: str = ""):
        """首尾帧: 发送两张图片（第一帧+最后帧）+ /startend <描述>"""
        images = [c for c in event.get_messages() if isinstance(c, Image)]
        if len(images) < 2:
            yield event.plain_result("请附上两张图片（首帧和尾帧），并输入描述")
            return

        try:
            first_url = await _image_to_data_url(images[0])
            last_url = await _image_to_data_url(images[1])
        except Exception as e:
            yield event.plain_result(f"图片解析失败: {e}")
            return

        clean_prompt, params = _parse_params(prompt or "")
        body = self._build_body(
            clean_prompt or "从首帧过渡到尾帧",
            params,
            first_frame_image=first_url,
            last_frame_image=last_url,
        )
        async for r in self._do_generate(event, body, "正在基于首尾帧生成视频..."):
            yield r

    @filter.command("vhelp", alias=["视频帮助"])
    async def video_help(self, event: AstrMessageEvent):
        """视频生成帮助"""
        help_text = (
            "视频生成插件使用说明\n"
            "━━━━━━━━━━━━━━━\n"
            "1. 文生视频\n"
            "  /t2v <描述>\n"
            "  例: /t2v 一只猫在草地上奔跑\n\n"
            "2. 图生视频（单图）\n"
            "  发送图片 + /i2v <描述>\n"
            "  例: /i2v 让画面中的水流起来\n\n"
            "3. 多图参考\n"
            "  发送多张图片 + /multi <描述>\n\n"
            "4. 首尾帧\n"
            "  发送两张图片(首帧+尾帧) + /startend <描述>\n\n"
            "━━━━━━━━━━━━━━━\n"
            "可选参数:\n"
            "  --ratio 16:9   画面比例\n"
            "  --seconds 5    时长(秒)\n"
            "  --resolution 720p  分辨率\n"
            "  --model xxx    指定模型"
        )
        yield event.plain_result(help_text)
