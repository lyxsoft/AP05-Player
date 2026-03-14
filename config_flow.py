"""
AP05 Integration 配置流
处理UI配置、选项修改逻辑（含IP/更新间隔）
"""
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from . import DOMAIN, DEFAULT_SERVER_IP, _get_translation

class AP05IntegrationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """AP05集成配置流主类"""
    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL


    translation_domain = DOMAIN  # 等同于manifest的domain: ap05
    translation_key = "config_flow"  # 对应翻译文件的config_flow根节点

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """用户配置步骤（集成添加入口）"""
        errors = {}

        if user_input is not None:
            # 设置唯一ID，防止重复配置
            await self.async_set_unique_id("AP05_integration_unique_id")
            self._abort_if_unique_id_configured()

            # 创建配置条目（含IP/设备名）
            entry_title = await _get_translation(
                self.hass,
                translation_key=f"{self.translation_key}.title",
                translation_type="config"  # 对应翻译文件的config节点
            )
            return self.async_create_entry(
                title=entry_title,
                data=user_input
            )

        # 配置表单（默认设备名+默认IP）
        # 设备名称默认值的翻译
        name_default = await _get_translation(
            self.hass,
            translation_key=f"{self.translation_key}.step.user.data.name_default",
            translation_type="config",
            placeholders={}  # 无占位符时传空字典
        ) or "AP05 播放器"  # 兜底值        
        data_schema = vol.Schema({
            vol.Required("name", default=name_default): str,
            vol.Required("server_ip", default=DEFAULT_SERVER_IP): str,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """获取选项配置流（修改IP/更新间隔）"""
        return AP05IntegrationOptionsFlow(config_entry)


class AP05IntegrationOptionsFlow(config_entries.OptionsFlow):
    """AP05集成选项配置流"""
    def __init__(self, config_entry):
        self._config_entry = config_entry
        self.translation_domain = DOMAIN  # 选项流也关联翻译
        self.translation_key = "config_flow.options"  # 选项流翻译键

    async def async_step_init(self, user_input=None) -> FlowResult:
        return await self.async_step_user(user_input)

    async def async_step_user(self, user_input=None) -> FlowResult:
        """选项配置步骤"""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # 选项表单（更新间隔+IP，保留当前配置值）
        data_schema = vol.Schema({
            vol.Optional(
                "server_ip",
                default=self._config_entry.options.get(
                    "server_ip",
                    self._config_entry.data.get("server_ip", DEFAULT_SERVER_IP)
                )
            ): str,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema
        )
