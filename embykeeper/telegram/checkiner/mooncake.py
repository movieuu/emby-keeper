import asyncio
import random
from io import BytesIO
from typing import Optional, Union

from embykeeper.ocr import CharRange, OCRService
from pyrogram.errors import RPCError, MessageIdInvalid
from pyrogram.types import Message

from ._templ_a import TemplateACheckin


class MooncakeCheckin(TemplateACheckin):
    """
    月饼签到:
    1. 发送 /start 后收到带有「签到」按钮的消息。
    2. 点击「签到」后，收到验证码图片，同时原消息被编辑为带数字按钮的消息。
    3. 识别图片中的一位数字，点击对应按钮。
    4. Bot 会多轮更新按钮/图片，直到签到完成或提示今日已签到。
    """

    name = "月饼"
    bot_username = "Moonkkbot"

    # 这里只保留“已经签到过”的判定
    bot_checked_keywords = ["今日已签到"]

    # 这里单独作为“本次签到成功”的判定
    bot_success_keywords = ["签到成功"]

    bot_text_ignore = ["签到验证", "请点击图片中显示的数字"]
    bot_captcha_len = [1]
    bot_use_captcha = True

    answer_wait_retries = 6
    answer_wait_interval = 1.2
    click_retry_retries = 3

    async def _request_ocr(self, image_data: Union[bytes, BytesIO]) -> str:
        """使用内置 OCRService 识别验证码。"""
        ocr = await OCRService.get(char_range=CharRange.NUMBER)
        with ocr:
            result = await ocr.run(image_data)
        return str(result or "").strip()

    @staticmethod
    def _extract_digit(text: str) -> Optional[str]:
        """从 OCR 结果中提取首个数字字符。"""
        for ch in text:
            if ch.isdigit():
                return ch
        return None

    @staticmethod
    def _flatten_keys(message: Message) -> list[str]:
        """提取消息中的所有按钮文本。"""
        if not (
            message.reply_markup
            and getattr(message.reply_markup, "inline_keyboard", None)
        ):
            return []
        return [
            button.text.strip()
            for row in message.reply_markup.inline_keyboard
            for button in row
        ]

    async def _find_answer_message(self) -> Optional[Message]:
        """
        查找最近一条带数字按钮的消息。
        月饼的按钮消息会不断被编辑，所以每次点击前都重新获取最新状态。
        """
        ident = self.chat_name or self.bot_username
        async for m in self.client.get_chat_history(ident, limit=10):
            keys = self._flatten_keys(m)
            if any(any(ch.isdigit() for ch in k) for k in keys):
                return m
        return None

    async def _find_target_message_and_key(
        self, digit: str
    ) -> tuple[Optional[Message], Optional[str]]:
        """
        多次尝试获取当前最新的按钮消息和对应的目标按钮。
        避免拿到旧消息或旧按钮快照。
        """
        for _ in range(self.answer_wait_retries):
            answer_msg = await self._find_answer_message()
            if answer_msg:
                keys = self._flatten_keys(answer_msg)

                self.log.debug(
                    f"[gray50]月饼当前按钮: {keys}, 目标数字: {digit}.[/]"
                )

                # 先精确匹配
                for key in keys:
                    if key == digit:
                        return answer_msg, key

                # 再兜底：从按钮文本中提取数字后匹配
                for key in keys:
                    digits_in_key = "".join(ch for ch in key if ch.isdigit())
                    if digits_in_key == digit:
                        return answer_msg, key

            await asyncio.sleep(self.answer_wait_interval)

        return None, None

    async def _click_digit_button(self, digit: str) -> bool:
        """
        点击当前轮次对应数字按钮。
        如果按钮消息正好在编辑中，就重新抓取后再试几次。
        """
        for attempt in range(1, self.click_retry_retries + 1):
            answer_msg, target_key = await self._find_target_message_and_key(digit)
            if not answer_msg or not target_key:
                self.log.debug(
                    f"[gray50]第 {attempt} 次点击前未找到可用按钮, 稍后重试.[/]"
                )
                await asyncio.sleep(self.answer_wait_interval)
                continue

            await asyncio.sleep(random.uniform(0.5, 1.5))

            try:
                await answer_msg.click(target_key)
                self.log.debug(
                    f"[gray50]月饼已点击按钮: {target_key} (第 {attempt} 次尝试).[/]"
                )
                return True
            except (RPCError, MessageIdInvalid) as e:
                self.log.debug(
                    f"[gray50]第 {attempt} 次按钮点击失败: {e.__class__.__name__}, 稍后重试.[/]"
                )
                await asyncio.sleep(self.answer_wait_interval)

        return False

    async def on_photo(self, message: Message):
        """
        覆盖模板 A 的图片处理逻辑，支持多轮验证码：
        1. 下载验证码图片，走内置 OCRService 识别。
        2. 提取其中的一位数字。
        3. 每次点击前重新抓取最新按钮消息并点击对应按钮。
        4. 适配第二轮图片更新后再次点击。
        """
        image_data = await self.client.download_media(message, in_memory=True)

        try:
            ocr_text = await self._request_ocr(image_data)
        except Exception:
            self.log.info("签到失败: 调用内置 OCR 失败, 正在重试.")
            await self.retry()
            return

        digit = self._extract_digit(ocr_text)
        if not digit:
            self.log.info("签到失败: 未能从验证码中识别出数字, 正在重试.")
            await self.retry()
            return

        self.log.debug(f"[gray50]月饼验证码识别结果: {digit}.[/]")

        # 稍微等一会，让 Bot 把按钮消息更新到最新状态，减少第一次点击失败的概率
        await asyncio.sleep(random.uniform(1.0, 1.5))

        clicked = await self._click_digit_button(digit)
        if not clicked:
            self.log.info(f'签到失败: 未能成功点击数字 "{digit}" 对应按钮, 正在重试.')
            await self.retry()
            return