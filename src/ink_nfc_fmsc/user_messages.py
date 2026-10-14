"""Actionable error summaries without guessing a failed panel's electrical state."""
import re

import serial

from .isodep import IsoDepTimeout
from .pn532 import Pn532Timeout, RfTransportError, STATUS_NAMES
from .protocol import STATUS


def failure_description(error):
    original = str(error)
    match = re.search(r"SW=([0-9A-Fa-f]{4})", original)
    code = None
    if isinstance(error, RfTransportError):
        code = f"RF={error.code:02X} ({STATUS_NAMES.get(error.code, 'UNKNOWN')})"
        message = "通信中断：未收到有效的驱动板回包。"
        advice = "固定线圈位置、缩短间距后再试。无回包不能直接判定为掉电；本次不会自动重刷。"
    elif isinstance(error, Pn532Timeout):
        code = "READER_TIMEOUT"
        message = "读写器响应超时，操作已停止。"
        advice = "检查 USB 连接和线圈位置；不要同时运行其他占用串口的程序。"
    elif isinstance(error, IsoDepTimeout):
        code = "COMMAND_TIMEOUT"
        message = "驱动板指令回包超时，操作已停止。"
        advice = "检查屏幕是否有变化和线圈是否稳定；延时请求不能延长指令期限，本次不会自动重刷。"
    elif isinstance(error, serial.SerialException):
        code = "PORT_UNAVAILABLE"
        message = "无法打开读写器串口。"
        advice = "检查串口名称与 USB 连接，并关闭占用该串口的其他程序。"
    elif match:
        sw = int(match[1], 16)
        code = f"SW={sw:04X}"
        message = STATUS.get(sw, "驱动板返回未定义的状态码。")
        if sw in (0x68CA, 0x698A, 0x6502):
            advice = "驱动板报告低电压。调整线圈耦合位置，保持设备不动后再试。"
        elif sw in (0x6982, 0x6985) or sw & 0xFFF0 == 0x63C0:
            advice = "访问权限未满足。检查所选驱动板类型及已知 PIN；程序不会猜测或连续重试 PIN。"
        elif sw in (0x6450, 0x6451, 0x6452, 0x6453, 0x646A):
            advice = "驱动板拒绝屏幕配置。核对屏幕型号，不要仅修改分辨率；本次未继续传图。"
        else:
            advice = "操作已停止。保留本次日志，以便检查失败位置；不要把返回成功等同于画面正确。"
    elif "activation failed" in original:
        code = "TAG_NOT_FOUND"
        message = "没有找到驱动板。"
        advice = "把驱动板线圈放在读写器感应区，保持接线不变，微调位置后再试。"
    elif "No BUSY" in original:
        code = "REFRESH_NOT_OBSERVED"
        message = "没有观察到屏幕开始刷新，已停止后续分段。"
        advice = "检查屏幕型号、排线方向和连接；尚不能确认驱动板实际启动了刷新。"
    elif "observation deadline" in original:
        code = "REFRESH_TIMEOUT"
        message = "等待刷新超时，操作已停止。"
        advice = "保持接线，检查屏幕是否有变化和线圈是否稳定；不要自动重复刷新。"
    elif original == "D1 does not declare D3 support; no configuration written":
        code = "COMPRESSION_UNSUPPORTED"
        message = "驱动板未声明支持压缩传图，本次没有写入配置或图片。"
        advice = "可显式添加 --no-compress 使用不压缩传图；程序不会自动换方式重传。"
    else:
        known = {
            "Existing D1 geometry does not match this screen; no D4 sent": "驱动板已存配置与所选屏幕不一致，请先用 send 传图刷新一次。",
            "D1 UID/configuration/slots mismatch; no image or D4 sent": "驱动板配置与所选屏幕不一致，已停止传图。请核对屏幕型号。",
        }
        message = known.get(original, original)
        advice = "请核对命令参数；详细原因已保存在本次日志。"
    return {"message": message, "code": code, "advice": advice, "original": original}


def stage_label(name):
    if "config" in name:
        return "设置或核对屏幕配置"
    if "D4" in name:
        return "启动屏幕刷新"
    if "DE" in name:
        return "等待屏幕刷新"
    if "D8" in name or "PIN" in name:
        return "检查访问权限"
    if "D1" in name:
        return "读取驱动板信息"
    if re.search(r"\bD[23]\b", name):
        return "传输图片"
    return name or "连接或准备图片"
