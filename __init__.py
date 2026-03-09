"""
AP05 Integration 核心文件
包含集成初始化、配置条目管理、平台转发逻辑
"""
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.util.dt import utcnow
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.translation import async_get_translations  # 导入翻译工具

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
        # 翻译键：对应 translations/zh-Hans.json 中的 "device.name"
        name=config_entry.data.get("name") or config_entry.title,
        translation_key="ap05_device",
        manufacturer="Shiyun",
        model="AP05",
        sw_version="1.0.0"
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """初始化从UI配置的集成条目"""
    hass.data.setdefault(DOMAIN, {})

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
                hass, "config.error.cannot_connect", 
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
            hass, "config.error.setup_failed",
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

                success_msg = await _get_translation(hass, "system.log.disconnect_success", None, "system")
                _LOGGER.info(success_msg)
            except Exception as err:
                error_msg = await _get_translation(
                    hass, "system.log.disconnect_failed",
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
async def _get_translation(
    hass: HomeAssistant,
    translation_key: str,
    placeholders: dict = None,
    translation_type: str = "config"
) -> str:
    """
    获取指定键的翻译文本
    :param hass: HA核心实例
    :param translation_key: 翻译键（对应zh-Hans.json中的路径）
    :param placeholders: 翻译文本中的占位符替换字典
    :return: 翻译后的文本（无翻译则返回原键）
    """
    if placeholders is None:
        placeholders = {}

    # 适配不同HA版本的async_get_translations参数
    try:
        # 新版本（支持domain参数）
        translations = await async_get_translations(
            hass,
            hass.config.language,
            translation_type,
            domain=DOMAIN
        )
    except TypeError:
        # 旧版本（无domain参数）
        translations = await async_get_translations(
            hass,
            hass.config.language,
            translation_type
        )

    # 获取原始翻译文本
    raw_text = translations.get(translation_key, translation_key)
    
    # 替换占位符
    try:
        translated_text = raw_text.format(**placeholders)
    except KeyError as e:
        _LOGGER.warning(f"翻译占位符缺失: {e} (key: {translation_key})")
        translated_text = raw_text
    
    return translated_text
    