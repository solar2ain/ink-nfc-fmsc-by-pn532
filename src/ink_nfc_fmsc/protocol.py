"""FMSC APDUs used by the stored DB -> image slot -> D4 pipeline."""
from dataclasses import dataclass

import lzokay

D1 = bytes.fromhex("00D1000000")
D8 = bytes.fromhex("F0D8000005000000000E")
DE = bytes.fromhex("F0DE000001")
DB_CLEAR = bytes.fromhex("F0DB020000")
FACTORY_PIN = bytes.fromhex("5265699919")
BLOCK_BYTES = 2000

STATUS = {
    0x9000: "成功 / OK", 0x6401: "执行未完成，手册未定义 / undocumented execution error",
    0x6449: "配置区 EEPROM 错误", 0x6450: "屏幕初始化数据错误",
    0x6451: "屏幕初始化包含错误 TAG", 0x6452: "屏幕初始化内容错误",
    0x6453: "屏幕初始化缺少必需项", 0x6454: "屏幕刷新失败", 0x6455: "屏幕复位失败",
    0x646A: "TLV 数量超限", 0x6501: "中断导致 NVM 操作停止",
    0x6502: "低电压导致 NVM 停止，不是 PIN 错误", 0x6503: "NVM 地址无效",
    0x6506: "NVM 权限错误", 0x6508: "NVM 长度错误", 0x6509: "命令参数错误",
    0x650A: "执行冲突", 0x650F: "无执行权限", 0x6700: "Lc/Le 长度错误",
    0x6781: "分包总长度错误", 0x68C6: "屏幕复位失败或屏幕不存在",
    0x68CA: "低电压告警 / low voltage", 0x68CB: "刷新超时告警",
    0x6982: "安全状态不满足", 0x6985: "使用条件不满足；D8 通常需要 PIN",
    0x6986: "屏幕复位失败或屏幕不存在", 0x698A: "低电压告警 / low voltage",
    0x6991: "压缩算法未初始化", 0x6992: "解压失败", 0x6993: "解压长度错误",
    0x6994: "压缩长度错误", 0x6995: "图像分包序号不匹配",
    0x6A80: "数据参数错误", 0x6A86: "P1/P2 错误", 0x6A96: "图槽编号不匹配",
}


@dataclass(frozen=True)
class Reply:
    data: bytes
    sw: int

    @property
    def meaning(self):
        return STATUS.get(self.sw, "未定义 / unknown status")

    def require_ok(self, label):
        if self.sw != 0x9000:
            raise RuntimeError(f"{label}: SW={self.sw:04X} ({self.meaning}); stopped, no retry")
        return self


def reply(raw):
    if len(raw) < 2:
        raise ValueError("Missing APDU status word")
    return Reply(raw[:-2], int.from_bytes(raw[-2:], "big"))


def parse_tlv(raw):
    fields = {}
    offset = 0
    while offset < len(raw):
        if offset + 2 > len(raw):
            raise ValueError("Truncated D1 TLV header")
        tag, length = raw[offset:offset + 2]
        offset += 2
        if offset + length > len(raw) or tag in fields:
            raise ValueError("Truncated or duplicate D1 TLV")
        fields[tag] = raw[offset:offset + length]
        offset += length
    return fields


def board_info(raw, uid):
    fields = parse_tlv(reply(raw).require_ok("D1").data)
    if (fields.get(0xC1) != uid or len(uid) not in (4, 7, 10)
            or len(fields.get(0xC0, b"")) != 4 or len(fields.get(0xA0, b"")) != 7
            or len(fields.get(0xD1, b"")) != 7 or len(fields.get(0xB1, b"")) != 1):
        raise ValueError("D1 does not confirm UID/configuration/capability of this FMSC target")
    return fields


def pin_apdu(pin):
    if len(pin) != 5:
        raise ValueError("Known FMSC PIN must contain exactly 5 bytes")
    return bytes.fromhex("0020000105") + pin


def literal_lzo(raw):
    if not 1 <= len(raw) <= BLOCK_BYTES:
        raise ValueError("LZO block size must be 1..2000")
    stream = bytearray()
    if len(raw) <= 238:
        stream.append(17 + len(raw))
    else:
        stream.append(0)
        rest = len(raw) - 18
        while rest > 255:
            stream.append(0)
            rest -= 255
        stream.append(rest)
    return bytes(stream) + raw + b"\x11\x00\x00"


def image_packets(planes, transport="d3", compression="lzo"):
    if transport not in ("d2", "d3") or compression not in ("lzo", "literal"):
        raise ValueError("Unknown image transport/compression")
    if not planes or len(planes) > 2:
        raise ValueError("Only one or two image planes are supported")
    packets = []
    for slot, raw in enumerate(planes):
        limit = 64000 if transport == "d2" else BLOCK_BYTES * 256
        if not 1 <= len(raw) <= limit:
            raise ValueError(f"{transport.upper()} plane size exceeds its packet-index range")
        if transport == "d2":
            for offset in range(0, len(raw), 250):
                chunk = raw[offset:offset + 250]
                packets.append(bytes((0xF0, 0xD2, slot, offset // 250, len(chunk))) + chunk)
        else:
            for block, offset in enumerate(range(0, len(raw), BLOCK_BYTES)):
                part = raw[offset:offset + BLOCK_BYTES]
                stream = lzokay.compress(part) if compression == "lzo" else literal_lzo(part)
                if lzokay.decompress(stream, len(part)) != part:
                    raise RuntimeError("LZO round-trip failed")
                for sub, start in enumerate(range(0, len(stream), 250)):
                    chunk = stream[start:start + 250]
                    last = start + len(chunk) == len(stream)
                    packets.append(bytes((0xF0, 0xD3, slot, int(last), len(chunk) + 2, block, sub)) + chunk)
    return tuple(packets)
