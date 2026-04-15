import asyncio
import base64
import os
import random
import httpx
from pyrogram.types import Message
from pyrogram.errors import RPCError
from ._templ_a import TemplateACheckin

class MooncakeCheckin(TemplateACheckin):
    name = "月饼"
    bot_username = "Moonkkbot"
    
    # --- 关键配置 ---
    bot_checkin_button = ["签到"] # 对应图二的按钮
    # 填入签到成功的关键字，用于最后一步识别
    bot_success_keywords = ["签到成功", "获得", "已签到"] 
    bot_fail_keywords = ["签到失败", "错误", "过期"]

    # 智谱配置 (从环境变量读取)
    ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")
    ZHIPU_MODEL = os.environ.get("ZHIPU_VISION_MODEL", "glm-4v-flash")

    async def _zhipu_identify_number(self, message: Message) -> str:
        """核心：调用智谱识别图片中的验证码数字"""
        if not self.ZHIPU_API_KEY:
            self.log.error("未设置 ZHIPU_API_KEY")
            return ""

        img_io = await self.client.download_media(message, in_memory=True)
        img_b64 = base64.b64encode(img_io.getvalue()).decode()

        # 精确的 Prompt 减少 AI 废话
        prompt = "图片中显示了一个大数字，请直接输出这个数字，不要任何解释。"
        
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
            "temperature": 0.1
        }

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.post(
                    "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                    headers={"Authorization": f"Bearer {self.ZHIPU_API_KEY}"},
                    json=payload
                )
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"]["content"].strip()
                digit = "".join(filter(str.isdigit, text))
                return digit
            except Exception as e:
                self.log.error(f"智谱请求失败: {e}")
                return ""

    async def on_photo(self, message: Message):
        """处理两步验证码图片消息"""
        # 判断是否为验证码消息（根据你提供的图一，文字通常在 caption 或 text 中）
        content = message.caption or message.text or ""
        if "点击图片中显示的数字" not in content:
            return

        if not message.reply_markup:
            return

        # 获取当前消息的所有按钮文本及其对象
        # 因为按钮是 1-9 的数字，我们建立一个映射
        btns = {k.text: k for r in message.reply_markup.inline_keyboard for k in r}
        
        # 识别
        for attempt in range(3):
            digit = await self._zhipu_identify_number(message)
            if digit in btns:
                # 识别到了且按钮存在
                current_step = "1/2" if "1步" in content else "2/2"
                self.log.info(f"正在处理第 {current_step} 步，识别数字: {digit}")
                
                await asyncio.sleep(random.uniform(1.0, 2.0))
                try:
                    await message.click(digit)
                    return # 成功点击后退出，等待下一条（即第2步或成功消息）
                except RPCError as e:
                    self.log.warning(f"点击失败: {e}")
            
            self.log.warning(f"识别失败或按钮匹配错位 ({digit})，重试 {attempt + 1}/3...")
            await asyncio.sleep(2)

    async def message_handler(self, client, message: Message):
        """
        扩展父类的消息处理：
        1. 识别并点击起始菜单的“签到”按钮
        2. 识别最终的签到结果（成功/失败）
        """
        # 1. 优先尝试点击初始界面的“签到”按钮（图二逻辑）
        # 这一步由父类 TemplateACheckin 的逻辑完成，我们不需要重写。
        # 但我们需要确保在最后一步（收到纯文本成功消息时）正确识别结果。

        text = message.text or message.caption or ""
        
        # 检查是否是最终成功/失败的文本消息
        if any(kw in text for kw in self.bot_success_keywords):
            self.log.success(f"签到任务最终确认成功: {text[:20]}...")
            return await self.success()
        
        if any(kw in text for kw in self.bot_fail_keywords):
            self.log.error(f"签到任务确认失败: {text[:20]}...")
            return await self.fail()

        # 调用父类处理（处理按钮点击逻辑）
        await super().message_handler(client, message)
