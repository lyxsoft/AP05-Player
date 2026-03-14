"""
AP05 Integration 核心文件
包含集成初始化、配置条目管理、平台转发逻辑
"""

import json
import os
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.util.dt import utcnow
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.translation import async_get_translations  # 导入翻译工具
from homeassistant.core import callback

# 导入WS客户端类
from .websocket_client import AP05WSClient

# 集成核心常量
DOMAIN = "ap05"
DEFAULT_SERVER_IP = "192.168.0.225"  # 默认设备IP地址
PLATFORMS = ["switch"]


# 日志配置
_LOGGER = logging.getLogger(__name__)

# ========== 设备信息模板 ==========
def get_ap05_device_info(config_entry: ConfigEntry) -> DeviceInfo:
    """生成AP05设备统一的device_info（可在所有实体中复用）"""
    return DeviceInfo(
        identifiers={(DOMAIN, config_entry.entry_id)},
        name=config_entry.data.get("name") or config_entry.title,
        translation_key="ap05_device",
        manufacturer="Shiyun",
        model="AP05",
        sw_version="1.0.0"
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """初始化从UI配置的集成条目"""
    hass.data.setdefault(DOMAIN, {})


    # ========== 注册语言切换监听（确保执行） ==========
    @callback
    def _handle_language_change(event):
        hass.async_create_task(_clear_translation_cache(hass, event))
    
    entry.async_on_unload(
        hass.bus.async_listen("language_changed", _handle_language_change)
    )


    try:
        # 优先级：选项IP > 配置IP > 默认IP
        server_ip = entry.options.get(
            "server_ip",
            entry.data.get("server_ip", DEFAULT_SERVER_IP)
        )

        # ========== 初始化WebSocket客户端并建立连接 ==========
        ws_client = AP05WSClient(hass, server_ip)
        if not await ws_client.connect():
            error_msg = await _get_translation(
                hass, "error.cannot_connect", 
                {"server_ip": server_ip}  # 翻译占位符参数
            )
            _LOGGER.error(error_msg)
            raise ConfigEntryNotReady(error_msg)

        _LOGGER.info(f"WS客户端已连接，监听任务状态: {ws_client._listen_task}")

        # 存储核心数据（含IP + WS客户端实例）
        hass.data[DOMAIN][entry.entry_id] = {
            "config": entry.data,
            "server_ip": server_ip,
            "ws_client": ws_client  # 存入全局WS连接实例
        }

    except Exception as err:
        error_msg = await _get_translation(
            hass, "error.setup_failed",
            {"default_ip": DEFAULT_SERVER_IP, "error": str(err)}
        )
        _LOGGER.error(error_msg)
        raise ConfigEntryNotReady(error_msg) from err

    # 监听选项更新
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    # 加载平台
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """选项更新回调：修改配置后重新加载集成"""
    # 卸载前关闭旧的WS连接
    if entry.entry_id in hass.data.get(DOMAIN, {}):
        ws_client = hass.data[DOMAIN][entry.entry_id].get("ws_client")
        if ws_client:
            await ws_client.disconnect()
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载集成条目"""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # 关闭WS连接
        domain_data = hass.data.get(DOMAIN, {})
        entry_data = domain_data.get(entry.entry_id, {})
        ws_client = entry_data.get("ws_client")
        if ws_client is not None:
            try:
                await ws_client.stop_listen()
                await ws_client.disconnect()

                success_msg = await _get_translation(hass, "log.disconnect_success", None, "system")
                _LOGGER.info(success_msg)
            except Exception as err:
                error_msg = await _get_translation(
                    hass, "log.disconnect_failed",
                    {"error": str(err)},
                    "system"
                )
                _LOGGER.error(error_msg)
        # 移除条目数据
        domain_data.pop(entry.entry_id, None)
    return unload_ok

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True
    
# ========== 通用翻译工具函数 ==========
# 翻译缓存：key=(language, translation_type), value=翻译字典
_TRANSLATION_CACHE = {}
# 缓存过期时间（HA 2026版建议短缓存，防止语言切换不生效）
CACHE_TIMEOUT = timedelta(minutes=10)


import json
import os

_TRANSLATION_CACHE = {}

async def _get_translation(
    hass: HomeAssistant,
    translation_key: str,
    placeholders: dict = None,
    translation_type: str = "config"
) -> str:
    """
    直接读取翻译文件，永不返回空！
    适配 HA2026.02，彻底解决 translations={} 的问题
    """
    if placeholders is None:
        placeholders = {}

    # 1. 构建缓存key（语言 + 翻译类型 → 不同语言/类型缓存隔离）
    current_language = hass.config.language
    cache_key = (current_language, translation_type)

    # 从缓存拿
    if cache_key in _TRANSLATION_CACHE:
        translations = _TRANSLATION_CACHE[cache_key]
    else:
        # 自己读取翻译文件
        try:
            integration_path = os.path.dirname(__file__)
            trans_path = os.path.join(integration_path, "translations", f"{current_language}.json")


            # 定义同步读取文件的函数（放到线程池执行）
            def _sync_read_trans_file(file_path):
                # 检查文件是否存在
                if not os.path.exists(file_path):
                    _LOGGER.error(f"❌ 翻译文件不存在：{file_path}")
                    return None
                # 同步读取文件（但在线程池执行，不阻塞事件循环）
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            
            # 关键：用HA的异步执行器运行同步IO操作
            translations = await hass.async_add_executor_job(_sync_read_trans_file, trans_path)

            # 检查读取结果
            if translations is None:
                return translation_key

            _TRANSLATION_CACHE[cache_key] = translations
            _LOGGER.debug(f"✅ 自己读取翻译成功：{trans_path}")

        except json.JSONDecodeError as e:
            _LOGGER.error(f"❌ 翻译文件格式错误（非合法JSON）：{e}")
            return translation_key
        except Exception as e:
            _LOGGER.error(f"❌ 读取翻译文件失败：{e}")
            return translation_key

    # 3. 解析嵌套翻译键
    keys = translation_key.split(".")
    #raw_text = translations
    try:
        for key in keys:
            translations = translations[key]
    except (KeyError, TypeError):
        return translation_key  # 未命中则返回原键   

    # 4. 替换占位符
    try:
        translated_text = translations.format(**placeholders)
    except KeyError as e:
        _LOGGER.warning(f"翻译占位符缺失: {e} (key: {translation_key})，原始文本: {translations}")
        translated_text = translations
    
    return translated_text

# ========== 缓存清理函数（适配HA语言切换） ==========
@callback
async def _clear_translation_cache(hass: HomeAssistant, event):
    """HA语言切换时清空翻译缓存"""
    global _TRANSLATION_CACHE
    _TRANSLATION_CACHE.clear()
    _LOGGER.debug("HA语言已切换，翻译缓存已清空")

