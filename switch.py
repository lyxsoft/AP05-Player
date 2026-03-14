"""开关平台"""
import logging
import json
import asyncio
from datetime import datetime, timedelta

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN
from .__init__ import get_ap05_device_info
from .__init__ import _get_translation  # 导入翻译函数

from .websocket_client import (
    WS_CMD_CONTROL_LCD_ON_OFF,
    WS_CMD_CONTROL_PLAY,
    WS_CMD_CONTROL_STOP,
    WS_CMD_WEBCONTROL_GET_STATUS
)


_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """设置开关平台"""
    async_add_entities([
        AP05PowerOn(hass, entry),
        AP05Playing(hass, entry)
    ])

class AP05Playing(SwitchEntity):
    """自定义开关实体"""
    _attr_has_entity_name = True


    translation_domain = DOMAIN  # 等同于manifest的domain: ap05
    translation_key = "ap05_playing"  # 对应翻译文件的config_flow根节点
    #_attr_translation_key = "ap05_playing"
    #_attr_name = "AP05 Playing"
    _attr_icon = "mdi:play-box"

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry):
        """初始化开关"""
        self.hass = hass
        self.config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_ap05_playing"

        self.ws_client = self.hass.data[DOMAIN][config_entry.entry_id]["ws_client"]
        self._attr_device_info = get_ap05_device_info(config_entry)
        self._listeners = []

        self._attr_available = False  # 初始可用性为不可用
        self._attr_is_on = False  # 初始状态为关闭
        
        # 初始化任务对象
        self._attr_available = self.ws_client.is_connected

    @property
    def icon(self):
        """根据开关状态返回不同图标"""
        if self._attr_is_on:
            return "mdi:pause-box"  # 开启时显示暂停图标
        return "mdi:play-box"       # 关闭时显示播放图标

    @property
    def device_info(self):
        """关联到统一的设备信息（核心！）"""
        return get_ap05_device_info(self.config_entry)


    async def async_added_to_hass(self) -> None:
        """实体被添加到Home Assistant时启动任务"""
        await super().async_added_to_hass()

        # 注册事件监听器并保存取消句柄
        stop_listen = self.hass.bus.async_listen(
            "ap05_connected",
            self._handle_connected
        )
        self._listeners.append(stop_listen)

        stop_listen = self.hass.bus.async_listen(
            "ap05_stop_changed",
            self._handle_stop_changed
        )
        self._listeners.append(stop_listen)

    async def async_will_remove_from_hass(self) -> None:
        """实体被移除时取消所有任务"""
        await super().async_will_remove_from_hass()
        # 取消所有注册的事件监听器
        for stop_listen in self._listeners:
            stop_listen()  # 调用取消句柄
        self._listeners.clear()

    async def _handle_connected(self, event):
        """处理connect值变化事件"""
        #if event.data["server_ip"] != self._client.server_ip:
        #    return
        self._attr_available = event.data["connected"]
        self.async_write_ha_state()

    async def _handle_stop_changed(self, event):
        """处理stop值变化事件"""
        self._attr_is_on = not event.data["new_stop_value"]
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        """打开开关：LCD状态检查+控制逻辑，再发送play命令"""
        if not self._attr_available:
            return

        # LCD状态检查和控制逻辑
        max_retry_seconds = 10  # 最大重试时间（避免无限循环）
        start_time = datetime.now()
        lcd_ready = False
        lcd_on_cmd = False
        
        await self.ws_client.get_status ()

        while (datetime.now() - start_time).total_seconds() < max_retry_seconds:
            # 1. 是否有lcdon信息
            if not self.ws_client.lcd_on is None:
                # 2. 检查lcd_on
                if self.ws_client.lcd_on:
                    lcd_ready = True
                    break

                # 3. lcdon≠1，发送lcdonoff命令（复用全局WS连接）
                if not lcd_on_cmd:
                    _LOGGER.info("LCD未开启，发送lcdonoff命令")
                    await self.ws_client.send_command(WS_CMD_CONTROL_LCD_ON_OFF)
                    lcd_on_cmd = True
            
            # 4. 重新获取信息，再次检查
            await self.ws_client.send_command(WS_CMD_WEBCONTROL_GET_STATUS)
            await asyncio.sleep(1)

        if not lcd_ready:
            _LOGGER.error(f"超过{max_retry_seconds}秒未检测到LCD开启（lcdon=1），终止打开操作")
            return

        stop_value = self.ws_client.status_stop
        if stop_value is None or not stop_value:
            _LOGGER.error(f"正在播放，终止打开操作")
            self._attr_is_on = True
            self.async_write_ha_state()
            return

        _LOGGER.info("准备打开AP05开关，发送play命令")
        await self.ws_client.send_command(WS_CMD_CONTROL_PLAY)

    async def async_turn_off(self, **kwargs):
        """关闭开关：发送stop命令"""
        if not self._attr_available:
            _LOGGER.error("开关当前不可用（WS监听异常），无法执行关闭操作")
            return
        
        _LOGGER.info("准备关闭AP05开关，发送stop命令")
        await self.ws_client.send_command(WS_CMD_CONTROL_STOP)

    @property
    def is_on(self):
        """返回开关状态"""
        return self._attr_is_on

    @property
    def available(self):
        """返回开关可用性状态"""
        return self._attr_available

    @property
    def extra_state_attributes(self):
        """返回额外属性"""
        return {
            "switch_type": "custom",
            "last_action": "turned_on" if self._attr_is_on else "turned_off",
            "integration": DOMAIN,
            "device_id": self.config_entry.entry_id,
            "available": self._attr_available,
        }

class AP05PowerOn(SwitchEntity):
    """AP05电源开关（控制LCD屏幕）"""
    _attr_has_entity_name = True
    translation_domain = DOMAIN  # 等同于manifest的domain: ap05
    translation_key = "ap05_poweron"  # 对应翻译文件的config_flow根节点
    #_attr_translation_key = "ap05_poweron"
    #_attr_name = "AP05 Power On"
    _attr_icon = "mdi:power"  # 电源图标

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry):
        """初始化电源开关"""
        self.hass = hass
        self.config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_ap05_power_on"  # 唯一ID（避免冲突）

        # 关联WS客户端和设备信息
        self.ws_client = self.hass.data[DOMAIN][config_entry.entry_id]["ws_client"]
        self._attr_device_info = get_ap05_device_info(config_entry)
        self._listeners = []

        # 初始状态
        self._attr_available = self.ws_client.is_connected
        self._attr_is_on = self.ws_client.lcd_on or False  # 初始化为LCD状态

    @property
    def device_info(self):
        """关联到统一的设备信息"""
        return get_ap05_device_info(self.config_entry)

    async def async_added_to_hass(self) -> None:
        """实体被添加到HA时注册事件监听"""
        await super().async_added_to_hass()

        # 监听连接状态事件
        stop_listen = self.hass.bus.async_listen(
            "ap05_connected",
            self._handle_connected
        )
        self._listeners.append(stop_listen)

        # 监听LCD状态变化事件
        stop_listen = self.hass.bus.async_listen(
            "ap05_lcdon_changed",
            self._handle_lcdon_changed
        )
        self._listeners.append(stop_listen)

    async def async_will_remove_from_hass(self) -> None:
        """实体被移除时取消监听"""
        await super().async_will_remove_from_hass()
        for stop_listen in self._listeners:
            stop_listen()
        self._listeners.clear()

    async def _handle_connected(self, event):
        """处理连接状态变化"""
        self._attr_available = event.data["connected"]
        self.async_write_ha_state()

    async def _handle_lcdon_changed(self, event):
        """处理LCD状态变化"""
        self._attr_is_on = event.data["new_lcdon_value"]
        self.async_write_ha_state()
        _LOGGER.info(f"AP05PowerOn 状态更新为: {self._attr_is_on} (LCD状态变化)")

    async def async_turn_on(self, **kwargs):
        """打开电源：发送LCD开启命令"""
        if not self._attr_available:
            _LOGGER.error("电源开关不可用（WS未连接），无法执行开启操作")
            return

        await self.ws_client.get_status ()

        # 如果已经开启，直接返回
        if self._attr_is_on:
            _LOGGER.info("LCD已开启，无需重复操作")
            return

        if not self.ws_client.lcd_on:
            _LOGGER.info("发送LCD开启命令（ap05_power_on）")
            # 发送LCD开关命令（lcdonoff 为切换指令，需先确认状态）
            await self.ws_client.send_command(WS_CMD_CONTROL_LCD_ON_OFF)

        # 主动刷新状态（可选：等待1秒后获取最新状态）
        await asyncio.sleep(1)
        await self.ws_client.send_command(WS_CMD_WEBCONTROL_GET_STATUS)

    async def async_turn_off(self, **kwargs):
        """关闭电源：发送LCD关闭命令"""
        if not self._attr_available:
            _LOGGER.error("电源开关不可用（WS未连接），无法执行关闭操作")
            return

        await self.ws_client.get_status ()

        # 如果已经关闭，直接返回
        if not self._attr_is_on:
            _LOGGER.info("LCD已关闭，无需重复操作")
            return

        if self.ws_client.lcd_on:
            _LOGGER.info("发送LCD关闭命令（ap05_power_off）")
            # 发送LCD开关命令（切换为关闭）
            await self.ws_client.send_command(WS_CMD_CONTROL_LCD_ON_OFF)

        # 主动刷新状态
        await asyncio.sleep(1)
        await self.ws_client.send_command(WS_CMD_WEBCONTROL_GET_STATUS)

    @property
    def is_on(self):
        """返回电源开关状态（LCD是否开启）"""
        return self._attr_is_on

    @property
    def available(self):
        """返回开关可用性"""
        return self._attr_available

    @property
    def extra_state_attributes(self):
        """返回额外属性"""
        return {
            "switch_type": "power",
            "lcd_on": self._attr_is_on,
            "integration": DOMAIN,
            "device_id": self.config_entry.entry_id,
            "available": self._attr_available,
            "last_updated": datetime.now().isoformat()
        }