import asyncio
import aiocron
import io
import os
import openai
import requests
import socket
import base64
import time

from nonebot import get_plugin_config, on_command, on_message, get_driver, get_bot
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message, PrivateMessageEvent, GroupMessageEvent
from nonebot.params import CommandArg
from nonebot.rule import Rule
from nonebot.typing import T_State
from nonebot.log import logger
from typing import Dict
from .config import PRESETS_LOCATION, OPENAI_API_KEY, OPENAI_ENDPOINT, GPT_MODEL, MAX_TOKENS, Config

# 插件元数据
__plugin_meta__ = PluginMetadata(
    name="aiqqbot",
    description="A plugin that can recognize pictures and reply to chats with AI",
    usage="Send a picture or message",
    type="application",
    homepage="https://github.com/caoshuo2003/nonebot-plugin-aibot",
    config=Config,
    supported_adapters={"~onebot.v11"}
)

# 初始化 OpenAI API
openai.api_key = OPENAI_API_KEY
openai.api_base = OPENAI_ENDPOINT

# 初始化 session 存储
sessions = {}

# 读取预设
def read_presets_txt(preset_name):
    if preset_name != "default":
        file_path = PRESETS_LOCATION + preset_name + ".txt"
    else:
        file_path = PRESETS_LOCATION + "default.txt"
    # logger.info(f"加载文件名 {file_path}")
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            lines = file.readlines()
    except FileNotFoundError as e:
        logger.info(f"没有这个预设哦")
        return ""
    file_content = ""
    for line in lines:
        if line.strip():
            file_content += line.strip()
    prompts = {"role": "system", "content": file_content}
    return prompts

# 清理过期的会话
def clean_expired_sessions():
    """清理过期的会话"""
    current_time = time.time()
    expired_sessions = [session_id for session_id, data in sessions.items() if current_time - data['start_time'] > 3600]
    for session_id in expired_sessions:
        del sessions[session_id]

# 处理私聊消息
def is_private_message() -> Rule:
    return Rule(lambda bot, event: isinstance(event, PrivateMessageEvent))

private_message = on_message(rule=is_private_message(), priority=6, block=True)

@private_message.handle()
async def handle_private_message(bot:Bot, event: PrivateMessageEvent, presets="default"):
    user_id = str(event.user_id)
    await handle_message(bot, event, user_id, presets)

# 处理群聊消息
def is_group_message() -> Rule:
    return Rule(lambda bot, event: isinstance(event, GroupMessageEvent))

group_message = on_message(rule=is_group_message(), priority=6, block=True)

@group_message.handle()
async def handle_group_message(bot: Bot, event: GroupMessageEvent, presets="default"):
    if event.is_tome():
        group_id = str(event.group_id)
        await handle_message(bot, event, group_id, presets)

async def handle_message(bot: Bot, event: MessageEvent, session_id: str, presets: str):
    if session_id not in sessions:
        sessions[session_id] = {"messages": [], "contextual_memory": True, "start_time": time.time(), "presets": presets}
        sessions[session_id]["messages"].append(read_presets_txt(presets))

    if event.message[0].type == "image":
        image_url = event.message[0].data["url"]
        question = "请记住这张图片, 只需回复'我已经了解了这张图片，有什么问题吗？'"
        reply =  await analyze_image(image_url, question, session_id)
        await bot.send(event, reply)
    else:
        user_input = event.get_plaintext().strip()
        sessions[session_id]["messages"].append({"role": "user", "content": user_input})
        try:
            reply = await chat_openai(session_id)
            if reply:
                await bot.send(event, reply)
            else: 
                await bot.send(event, f"出错了，请尝试“重置会话”哦qvq")
        except Exception as e:
            logger.error(f"OpenAI API 请求失败: {e}")
            await bot.send(event, "目前无法回复您的问题。")

async def chat_openai(session_id: str) -> str:
    try:
        contextual_memory = sessions[session_id]["contextual_memory"]
        logger.info(f"contextual_memory: {contextual_memory}")
        logger.info(f"session_id: {session_id}")
        if contextual_memory:
            selected_messages = sessions[session_id]["messages"]
            #if not check_max_tokens(selected_messages, 4096):
             #   return ""
        else:
            selected_messages = [sessions[session_id]["messages"][-1]]
        
        # logger.info(selected_messages)
        response = openai.ChatCompletion.create(
            model=GPT_MODEL,
            messages=selected_messages,
            session_id=session_id,
            max_tokens=MAX_TOKENS
        )
        reply = response['choices'][0]["message"]['content']
        sessions[session_id]["messages"].append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        logger.error(f"Openai fail: {e}")

async def analyze_image(image_url: str, question: str, session_id: str) -> str:
    try:
        # 将图像编码为 base64
        base64_image =  await encode_image(image_url)

        # 调用API处理图像
        response = openai.ChatCompletion.create(
            model=GPT_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}",
                            },
                        },
                    ],
                                 }
            ],
            session_id=session_id,
            max_tokens=MAX_TOKENS
        )
        reply = response.choices[0].message['content']
        sessions[session_id]["messages"].append({"role": "user", "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}",
                            }
                        }
                    ]})
        return reply
    except Exception as e:
        logger.error(f"Error analysing image: {e}")

async def encode_image(image_url):
    async with httpx.AsyncClient() as client:
        for i in range(3):
            try:
                resp = await client.get(image_url, timeout=20)
                resp.raise_for_status()
                return base64.b64encode(resp.content).decode('utf-8')
            except Exception as e:
                logger.warning(f"Error downloading {image_url}, retry {i}/3: {e}")
                await asyncio.sleep(3)

# 清除会话
handle_clear_private_session = on_command("重置会话", rule=is_private_message(), priority=5, block=True)

@handle_clear_private_session.handle()
async def clear_private_session(bot: Bot, event: PrivateMessageEvent):
    user_id = str(event.user_id)
    await clear_session(user_id)
    await bot.send(event, "私聊会话清除。")

# 清除会话
handle_clear_group_session = on_command("重置会话", rule=is_group_message(), priority=5, block=True)

@handle_clear_group_session.handle()
async def clear_group_session(bot: Bot, event: GroupMessageEvent):
    group_id = str(event.group_id)
    await clear_session(group_id)
    await bot.send(event, "群聊会话清除。")

async def clear_session(session_id: str):
    if session_id in sessions:
        del sessions[session_id]
'''
handle_enable_private_memory = on_command("开启记忆", rule=is_private_message(), priority=5, block=True)

@handle_enable_private_memory.handle()
async def enable_private_memory(bot: Bot, event: PrivateMessageEvent):
    user_id = str(event.user_id)
    await enable_memory(user_id)
    await bot.send(event, "私聊记忆开启。")

# 开启记忆
handle_enable_group_memory = on_command("开启记忆", rule=is_group_message(), priority=5, block=True)

@handle_enable_group_memory.handle()
async def enable_group_memory(bot: Bot, event: GroupMessageEvent):
    group_id = str(event.group_id)
    await enable_memory(group_id)
    await bot.send(event, "Group记忆开启。")
'''

async def enable_memory(session_id):
    sessions[session_id] = {"messages": [], "contextual_memory": True, "start_time": time.time()}
    sessions[session_id]["messages"].append(read_presets_txt("default"))

# 加载预设
handle_presets_private_session = on_command("加载预设", rule=is_private_message(), priority=5, block=True)

@handle_presets_private_session.handle()
async def handle_preset_private_receive(bot: Bot, event: PrivateMessageEvent, args: Message = CommandArg()):
    # 获取命令的参数
    presets = args.extract_plain_text().strip()
    user_id = str(event.user_id)
    presets_content = read_presets_txt(presets)
    if not presets_content:
        await bot.send(event, f"预设加载失败, 请确定预设名称是否正确!")
        return
    
    if user_id not in sessions:
        sessions[user_id] = {"messages": [], "contextual_memory": True, "start_time": time.time(), "presets": presets}
        sessions[user_id]["messages"].append(presets_content)
    else:
        sessions[user_id]["messages"] = []
        sessions[user_id]["contextual_memory"] = True
        sessions[user_id]["messages"].append(presets_content)
    await bot.send(event, f"预设加载成功!")
            
# 加载预设
handle_presets_group_session = on_command("加载预设", rule=is_group_message(), priority=5, block=True)

@handle_presets_group_session.handle()
async def handle_preset_group_receive(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    # 获取命令的参数
    presets = args.extract_plain_text().strip() 
    if event.is_tome():
        group_id = str(event.group_id)
        presets_content = read_presets_txt(presets)
        if not presets_content:
            await bot.send(event, f"预设加载失败, 请确定预设名称是否正确!")
            return

        if group_id not in sessions:
            sessions[group_id] = {"messages": [], "contextual_memory": True, "start_time": time.time(), "presets": presets}
            sessions[group_id]["messages"].append(presets_content)
        else:
            sessions[group_id]["messages"] = []
            sessions[group_id]["contextual_memory"] = True
            sessions[group_id]["messages"].append(presets_content)
        await bot.send(event, f"预设加载成功!")

