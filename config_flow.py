"""
AP05 Integration 配置流
处理UI配置、选项修改逻辑（含IP）
"""
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from . import DOMAIN, DEFAULT_SERVER_IP, _get_translation

class AP05IntegrationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """AP05集成配置流主类"""
    VERSION = 1
    #translation_domain = DOMAIN
    #translation_key = "config"

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """用户配置步骤（集成添加入口）"""
        if user_input is not None:
            # 设置唯一ID，防止重复配置 
            myUniqueID = (user_input["server_ip"]).replace(".", "_")
            await self.async_set_unique_id(f"ap05_integration_{myUniqueID}")
            self._abort_if_unique_id_configured()

            # 创建配置条目（含IP/设备名）
            entry_title = await _get_translation(
                self.hass,
                translation_key="config.title",
                translation_type="config"  # 对应翻译文件的config节点
            )
            return self.async_create_entry(
                title="",
                data=user_input
            )

        # 配置表单（默认设备名+默认IP）
        # 设备名称默认值的翻译
        name_default = await _get_translation(
            self.hass,
            translation_key="config.step.user.data.name_default",
            translation_type="config",
            placeholders={}  # 无占位符时传空字典
        ) or "AP05 播放器"  # 兜底值        
        data_schema = vol.Schema({
            vol.Required("name", default=name_default): str,
            vol.Required("server_ip", default=DEFAULT_SERVER_IP): str,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """获取选项配置流（修改IP/更新间隔）"""
        return AP05IntegrationOptionsFlow(config_entry)


class AP05IntegrationOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None) -> FlowResult:
        return await self.async_step_user(user_input)

    async def async_step_user(self, user_input=None) -> FlowResult:
        """选项配置步骤"""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # 选项表单
        data_schema = vol.Schema({
            vol.Required("server_ip", default=self._config_entry.options.get("server_ip", DEFAULT_SERVER_IP)): str,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema
        )
