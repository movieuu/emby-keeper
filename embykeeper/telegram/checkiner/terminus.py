import asyncio
import base64
import os
import random
import emoji
import httpx
from typing import Optional
from pyrogram.types import Message
from pyrogram.errors import RPCError

from . import AnswerBotCheckin

class TerminusCheckin(AnswerBotCheckin):
    name = "终点站"
    bot_username = "EmbyPublicBot"
    bot_checkin_cmd = ["/cancel", "/checkin"]
    bot_text_ignore = ["会话已取消", "没有活跃的会话"]
    bot_checked_keywords = ["今天已签到"]
    max_retries = 1
    bot_use_history = 3

    # 从环境变量获取智谱配置，建议在 Docker 部署时传入
    ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")
    ZHIPU_MODEL = os.environ.get("ZHIPU_VISION_MODEL", "glm-4v-flash")

    async def _zhipu_visual(self, message: Message, options: list[str]) -> Optional[str]:
        """调用智谱大模型识别验证码图片"""
        if not self.ZHIPU_API_KEY:
            self.log.error("未设置 ZHIPU_API_KEY，无法进行视觉识别.")
            return None

        # 下载图片到内存
        img_io = await self.client.download_media(message, in_memory=True)
        img_b64 = base64.b64encode(img_io.getvalue()).decode()

        prompt = (
            f"图片中展示了一个验证码，请从以下选项中选出最符合图片描述的内容：\n"
            f"{', '.join(options)}\n"
            "要求：只输出选项原文，不要输出任何解释或多余字符。"
        )

        payload = {
            "model": self.ZHIPU_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                        {"type": "text", "text": prompt}
                    ]
                }
            ],
            "temperature": 0.1,
            "max_tokens": 50
        }

        async with httpx.AsyncClient(timeout=30) as http_client:
            try:
                resp = await http_client.post(
                    "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                    headers={"Authorization": f"Bearer {self.ZHIPU_API_KEY}"},
                    json=payload
                )
                resp.raise_for_status()
                res_text = resp.json()["choices"][0]["message"]["content"].strip()
                # 清洗多余符号
                clean_res = res_text.replace('"', '').replace("'", "").replace("。", "").strip()
                self.log.debug(f"智谱识别原始结果: {res_text} -> 清洗后: {clean_res}")
                return clean_res
            except Exception as e:
                self.log.error(f"智谱 API 请求异常: {e}")
                return None

    async def on_photo(self, message: Message):
        if not message.reply_markup:
            return

        # 预处理选项：移除 Emoji 方便匹配
        clean_func = lambda o: emoji.replace_emoji(o, "").replace(" ", "").strip()
        
        # 提取按钮
        raw_options = [k.text for r in message.reply_markup.inline_keyboard for k in r]
        options_map = {clean_func(o): o for o in raw_options} # {清洗后: 原文}
        
        if len(raw_options) < 2:
            return

        for i in range(3):
            result_key = await self._zhipu_visual(message, list(options_map.keys()))
            
            # 尝试模糊匹配或完全匹配
            final_choice = None
            if result_key in options_map:
                final_choice = options_map[result_key]
            else:
                # 模糊匹配：看识别结果是否包含在某个选项里
                for k, v in options_map.items():
                    if k in result_key or result_key in k:
                        final_choice = v
                        break
            
            if final_choice:
                self.log.info(f"验证码匹配成功: {final_choice}")
                await asyncio.sleep(random.uniform(1, 2))
                try:
                    await message.click(final_choice)
                    return
                except RPCError as e:
                    self.log.warning(f"点击失败: {e}")
            
            self.log.warning(f"解析结果不匹配，重试 {i+1}/3...")
            await asyncio.sleep(3)

        self.log.error("验证码多次尝试失败.")
        await self.fail()
