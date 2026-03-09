"""
AP05 Integration WebSocket 客户端工具
封装持久化WebSocket连接、命令发送、广播监听逻辑
"""
import logging
import json
import asyncio
import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK, ConnectionClosedError
from datetime import datetime, timedelta

# 控制类指令
WS_CMD_CONTROL_LCD_ON_OFF = {"control":{"action":"lcdonoff"}}  # LCD开关
WS_CMD_CONTROL_PLAY = {"control":{"action":"play"}}            # 播放
WS_CMD_CONTROL_STOP = {"control":{"action":"stop"}}            # 停止

# 状态查询类指令
WS_CMD_WEBCONTROL_GET_STATUS = {"webcontrol":{"action":"getstatus"}}  # 获取设备状态


_LOGGER = logging.getLogger(__name__)

def find_first_key(data, key, defaultValue = None):
    if isinstance(data, dict):
        for k, v in data.items():
            if k == key:
                return v
            r = find_first_key(v, key)
            if r is not None:
                return r
    elif isinstance(data, list):
        for x in data:
            r = find_first_key(x, key)
            if r is not None:
                return r
    return defaultValue

def key_exists(data, key):
    if isinstance(data, dict):
        for k, v in data.items():
            if k == key:
                return True
            r = key_exists(v, key)
            if r is not None:
                return True
    elif isinstance(data, list):
        for x in data:
            r = key_exists(x, key)
            if r is not None:
                return True
    return False

class AP05WSClient:
    """AP05设备WebSocket持久化连接客户端"""
    def __init__(self, hass, server_ip: str, port: int = 80):
        self.hass = hass
        self.server_ip = server_ip
        self.port = port
        self.ws_url = f"ws://{server_ip}:{port}/websocket"
        self.websocket = None  # 持久化连接对象
        self.connected = False  # 连接状态
        self.reconnect_delay = 5  # 初始重连延迟（秒）
        self.max_reconnect_delay = 60  # 最大重连延迟
        self.lock = asyncio.Lock()  # 并发控制锁

        self._listen_task = None  # 广播监听任务
        self._stop_flag = asyncio.Event() # 停止标志（用于退出）
        self._get_message = asyncio.Event()

        self.lcd_on = None
        self.status_stop = None

    @property
    def is_connected(self):
        """判断当前是否处于连接状态"""
        if not self.connected:
            return False
        if self.websocket is None:
            return False
        # websockets库的状态枚举：CONNECTED/CLOSING/CLOSED
        return self.websocket.state == websockets.protocol.State.OPEN        

    async def _fire_connected_event(self):
        """抽离连接状态事件发送逻辑（复用）"""
        event_data = {
            "server_ip": self.server_ip,
            "connected": self.connected,
            "timestamp": datetime.now().isoformat()
        }
        self.hass.bus.async_fire(
            event_type="ap05_connected",
            event_data=event_data
        )
        _LOGGER.info(f"已发布HA事件 ap05_connected:{self.connected}")

    async def connect(self):
        """建立WebSocket连接（含重连逻辑）"""
        if self.is_connected:
            return True

        while True:
            try:
                async with self.lock:
                    if self.connected:
                        break
                    _LOGGER.info(f"尝试连接AP05 WebSocket: {self.ws_url}")
                    self.websocket = await websockets.connect(
                        self.ws_url,
                        open_timeout=10,  # 连接超时10秒
                        ping_interval=30,  # 心跳间隔30秒
                        ping_timeout=10    # 心跳超时10秒
                    )
                    self.connected = True
                    self.reconnect_delay = 5  # 重置重连延迟

                _LOGGER.info(f"成功连接到 {self.ws_url}，连接状态: {self.websocket.state}")
                await self._fire_connected_event()

                # 启动监听
                await self.start_listen()
                break
            except asyncio.TimeoutError:
                _LOGGER.error(f"连接 {self.ws_url} 超时，{self.reconnect_delay}秒后重试")
            except websockets.exceptions.ConnectionClosed:
                _LOGGER.error(f"{self.ws_url} 连接被关闭，{self.reconnect_delay}秒后重试")
            except Exception as err:
                _LOGGER.error(f"连接 {self.ws_url} 失败: {str(err)}，{self.reconnect_delay}秒后重试")
            
            # 指数退避重连
            await asyncio.sleep(self.reconnect_delay)
            self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)
        
        return self.connected

    async def start_listen(self):
        # 启动监听任务
        async with self.lock:
            if self._listen_task and not self._listen_task.done():
                self._listen_task.cancel()
            self._stop_flag.clear()
            self._listen_task = self.hass.async_create_task(self.listen_broadcast())

    async def stop_listen(self):
        #停止广播监听
        async with self.lock:
            if self._listen_task and not self._listen_task.done():
                # 1. 置位停止标志（防止循环重启）
                self._stop_flag.set()
                # 2. 取消接收任务（中断阻塞的recv()）
                self._listen_task.cancel()
                try:
                    # 等待任务完成（捕获CancelledError，避免告警）
                    await self._listen_task
                except asyncio.CancelledError:
                    pass
                except Exception as err:
                    _LOGGER.error(f"停止接收任务失败: {err}")
                finally:
                    self._listen_task = None  # 清空任务句柄


    async def _close_connection(self):
        if not self.websocket:
            return
        try:
            # 优雅关闭WS连接（发送关闭帧）
            await self.websocket.close()
            _LOGGER.info(f"已关闭到 {self.server_ip} 的WS连接")
        except Exception as err:
            _LOGGER.error(f"关闭连接异常: {err}")
        finally:
            self.connected = False
            self.websocket = None

        _LOGGER.info(f"已关闭 {self.ws_url} 连接")
        await self._fire_connected_event()

    async def disconnect(self):
        """关闭WebSocket连接"""
        await self.stop_listen()  # 先停止接收任务，再关闭连接
        async with self.lock:
            if self.websocket:
                await self._close_connection()

    async def send_command(self, command: dict):
        while True:
            if not self.is_connected:
                if not await self.connect():
                    _LOGGER.error("WS未连接，发送命令失败")
                    break
            try:
                self._get_message.clear ()

                async with self.lock:
                    json_cmd = json.dumps(command)
                    await self.websocket.send(json_cmd)
                    _LOGGER.info(f"向 {self.ws_url} 发送命令: {json_cmd}")
                    return True
            except Exception as err:
                _LOGGER.error(f"发送命令失败: {str(err)}")
                await self.disconnect()

        return False

    async def get_status(self, timeout: float = 10):
        await self.send_command (WS_CMD_WEBCONTROL_GET_STATUS)

        try:
            # wait_for：等待事件触发，超时抛出TimeoutError
            await asyncio.wait_for(self._get_message.wait(), timeout=10)
        except asyncio.TimeoutError:
            _LOGGER.info("等待状态更新超时（{timeout}秒）")

    async def listen_broadcast(self):
        #持续监听WebSocket广播消息
        _LOGGER.info(f"开始监听 {self.ws_url} 广播消息，监听数据变化事件")
        while not self._stop_flag.is_set():
            if not self.is_connected:
                await self.connect()
                if not self.is_connected:
                    _LOGGER.info(f"重连失败 {self.ws_url}")
                    await asyncio.sleep(self.reconnect_delay)
                    continue

            try:
                # 单次recv超时10秒，避免永久阻塞
                message = await asyncio.wait_for(self.websocket.recv(), timeout=10)
                try:
                    msg_data = json.loads(message)
                    if msg_data is None:
                        continue

                    # 处理信息
                    lcdon_value = find_first_key(msg_data, "lcdon", self.lcd_on)
                    stop_value = find_first_key(msg_data, "stop", self.status_stop)

                    if lcdon_value is not None and lcdon_value != self.lcd_on:
                        # 记录新值
                        # 直接用hass发布自定义事件（无需实体对象）
                        event_data = {
                            "server_ip": self.server_ip,
                            "old_lcdon_value": self.lcd_on,
                            "new_lcdon_value": lcdon_value,
                            "timestamp": datetime.now().isoformat()
                        }
                        self.hass.bus.async_fire(
                            event_type="ap05_lcdon_changed",
                            event_data=event_data
                        )
                        _LOGGER.info(f"检测到lcdon值变化: {self.lcd_on} -> {lcdon_value}，已发布HA事件 ap05_lcdon_changed")
                        self.lcd_on = lcdon_value

                    if stop_value is not None and stop_value != self.status_stop:
                        # 记录新值
                        # 直接用hass发布自定义事件（无需实体对象）
                        event_data = {
                            "server_ip": self.server_ip,
                            "old_stop_value": self.status_stop,
                            "new_stop_value": stop_value,
                            "timestamp": datetime.now().isoformat()
                        }
                        self.hass.bus.async_fire(
                            event_type="ap05_stop_changed",
                            event_data=event_data
                        )
                        _LOGGER.info(f"检测到stop值变化: {self.status_stop} -> {stop_value}，已发布HA事件 ap05_stop_changed")
                        self.status_stop = stop_value

                except json.JSONDecodeError:
                    _LOGGER.warning(f"非JSON格式消息: {message}")
                except Exception as err:
                    _LOGGER.error(f"解析广播消息失败: {str(err)}")

                self._get_message.set() #收到信息

            except asyncio.TimeoutError:
                _LOGGER.debug("WS接收消息超时，继续监听")
                continue
            except websockets.exceptions.ConnectionClosed:
                break                

