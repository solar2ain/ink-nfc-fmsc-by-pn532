"""Bounded FMSC transactions with no automatic write or refresh replay."""
import json
import math
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .console import format_message
from .isodep import IsoDepSession
from .pn532 import Pn532, hex_bytes
from .protocol import D1, D8, DE, FACTORY_PIN, board_info, pin_apdu, reply, stored_scan_compatible

BOARD_NAMES = {
    "huawei": "华为Nova墨水屏手机壳NFC驱动板",
    "iphone": "FMSC墨水屏NFC驱动板",
}


class RunLog:
    def __init__(self, directory):
        base = Path(directory).expanduser()
        base.mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix=datetime.now().strftime("%Y%m%d_%H%M%S_%f_"), dir=base))
        self.details = self.directory / "details"
        self.details.mkdir()
        self.path = self.details / "events.jsonl"
        self.text_path = self.directory / "run.log"
        self.started = time.monotonic()
        self.summary = {"status": "running", "optically_verified": False}

    def message(self, text, *, level="info", stage=None, detail=False):
        line = format_message(text, level=level, stage=stage, detail=detail)
        with self.text_path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        return line

    def update(self, **values):
        self.summary.update(values)
        (self.directory / "summary.json").write_text(json.dumps(self.summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def finish(self, status, **values):
        self.update(status=status, elapsed_seconds=round(time.monotonic() - self.started, 2), **values)

    def event(self, kind, details):
        row = {"time": datetime.now(timezone.utc).isoformat(), "event": kind, **details}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=True) + "\n")

    def serial_event(self, kind, details):
        # No raw wire dump by default: custom PIN bytes must not enter exported logs.
        if kind not in ("pn532_tx", "pn532_rx", "stale_rx"):
            self.event(kind, {k: v for k, v in details.items() if k not in ("hex", "tx")})


@dataclass(frozen=True)
class Settings:
    port: str
    board: str = "auto"
    reader: str = "auto"
    baud: int = 115200
    precharge_ms: int = 1000
    packet_delay_ms: int = 0
    pre_refresh_ms: int = 0
    first_de_delay_ms: int = 0
    de_hz: float = 9.0
    rf_timeout_ms: int = 1000
    d4_timeout: float = 8.0
    de_timeout: float = 5.0
    expected_uid: bytes | None = None
    pin: bytes | None = None
    verbose: bool = False

    def __post_init__(self):
        if re.fullmatch(r"COM[0-9]+", self.port, re.IGNORECASE | re.ASCII):
            object.__setattr__(self, "port", self.port.upper())
        if self.board not in ("auto", "huawei", "iphone") or self.reader not in ("auto", "pn532", "pcr532"):
            raise ValueError("Unknown board/reader selection")
        for name in ("precharge_ms", "pre_refresh_ms", "first_de_delay_ms"):
            if not 0 <= getattr(self, name) <= 30000:
                raise ValueError(f"{name} must be 0..30000")
        if not 0 <= self.packet_delay_ms <= 5000 or not 100 <= self.rf_timeout_ms <= 3000:
            raise ValueError("Invalid packet gap or RF timeout")
        if not math.isfinite(self.de_hz) or not 0.1 <= self.de_hz <= 20:
            raise ValueError("DE target frequency must be 0.1..20 Hz")
        if not 2 <= self.d4_timeout <= 60 or not 1 <= self.baud <= 921600:
            raise ValueError("Invalid D4 timeout or baud rate")
        if not 2 <= self.de_timeout <= 60:
            raise ValueError("DE response timeout must be within 2..60 s")
        if self.expected_uid is not None and len(self.expected_uid) not in (4, 7, 10):
            raise ValueError("Expected UID must be 4, 7 or 10 bytes")
        if self.pin is not None and len(self.pin) != 5:
            raise ValueError("PIN must contain 5 known bytes")
        if self.board == "iphone" and self.pin is not None:
            raise ValueError("iPhone board mode never sends a PIN")


class Device:
    def __init__(self, settings, logger, *, reader_factory=Pn532, session_factory=IsoDepSession,
                 clock=time.monotonic, sleep=time.sleep, output=print):
        self.settings, self.log = settings, logger
        self.clock, self.sleep, self.output = clock, sleep, output
        wake = {"auto": "auto", "huawei": "private", "iphone": "standard"}[settings.board]
        self.reader = reader_factory(settings.port, settings.baud, logger.serial_event, wake_mode=wake)
        self.session_factory = session_factory
        self.session = None
        self.fields = None
        self.activation = None
        self.board = None
        self.stage = "连接"

    def report(self, message, *, level="info", detail=True):
        self.output(self.log.message(message, level=level, stage=self.stage, detail=detail))

    def begin_stage(self, stage, message):
        self.stage = stage
        self.report(message, detail=False)

    def __enter__(self):
        try:
            self.begin_stage("连接", f"连接读写器 {self.settings.port}，正在寻找驱动板...")
            self.reader.open()
            self.activation = self.reader.activate(off_ms=300, first_wait_ms=200)
            if self.settings.expected_uid is not None and self.activation.uid != self.settings.expected_uid:
                raise ValueError(f"UID mismatch: expected {hex_bytes(self.settings.expected_uid)}, got {hex_bytes(self.activation.uid)}")
            # PCR532 may not accept extended UART frames. Fragment APDUs at ISO-DEP,
            # NEVER shorten the fixed 250-byte FMSC D2/D3 application stride.
            limit = 253 if self.settings.reader == "pn532" else 240
            self.session = self.session_factory(self.reader, self.activation, max_inf_bytes=limit)
            self.pause(self.settings.precharge_ms)
            response = self.exchange("D1", D1, quiet=True)
            self.fields = board_info(response.data + b"\x90\x00", self.activation.uid)
            appid = self.fields[0xC0]
            private = self.activation.wake_command in (0x7A, 0x7D)
            if appid not in (bytes.fromhex("00000001"), bytes.fromhex("201EA001")):
                raise ValueError(f"Unregistered FMSC APPID {appid.hex().upper()}; stopped without writes/PIN")
            detected = "huawei" if private else "iphone"
            if self.settings.board != "auto" and self.settings.board != detected:
                raise ValueError("Board wake mode does not match the explicitly selected board")
            self.board = detected
            self.log.event("connected", {"uid": self.activation.uid.hex().upper(), "appid": appid.hex().upper(),
                "board": self.board, "board_name": BOARD_NAMES[self.board], "reader_policy": self.settings.reader, "max_inf_bytes": limit,
                "wake": f"{self.activation.wake_command:02X}", "atqa": self.activation.atqa.hex().upper(),
                "sak": f"{self.activation.sak:02X}", "ats": self.activation.ats.raw.hex().upper(),
                "fields": {f"{tag:02X}": data.hex().upper() for tag, data in self.fields.items()}})
            label = BOARD_NAMES[self.board]
            self.log.update(board=self.board, board_name=label, uid=self.activation.uid.hex().upper(), port=self.settings.port,
                            reader_policy=self.settings.reader, de_hz=self.settings.de_hz, de_timeout=self.settings.de_timeout)
            self.report(f"已连接 {label}，UID {hex_bytes(self.activation.uid)}（D1=9000）。", level="success")
            if self.settings.verbose:
                self.report(f"APPID {hex_bytes(appid)}; wake={self.activation.wake_command:02X} / 7-bit; INF<={limit}")
            return self
        except BaseException:
            self.reader.close()
            raise

    def __exit__(self, _type, _value, _traceback):
        self.reader.close()

    @property
    def capacity(self):
        # These are explicit conservative budgets, not a D1 flash-capacity measurement.
        return 80000 if self.board == "iphone" else 200000

    def pause(self, milliseconds):
        until = self.clock() + milliseconds / 1000
        while (remaining := until - self.clock()) > 0:
            self.sleep(min(0.1, remaining))

    def exchange(self, name, apdu, *, timeout=5.0, require_ok=True, quiet=False, secret=False, on_wtx=None):
        started = self.clock()
        self.log.summary["last_stage"] = name
        try:
            raw = self.session.transceive(apdu, timeout_s=timeout,
                    minimum_wait_ms=self.settings.rf_timeout_ms,
                    max_wtx=max(256, int(timeout * 100) + 1), on_wtx=on_wtx)
            result = reply(raw)
        except Exception as exc:
            self.log.event("apdu_error", {"stage": name, "tx": "[PIN REDACTED]" if secret else apdu.hex().upper(),
                "error": str(exc), "elapsed_ms": round((self.clock() - started) * 1000), "retry": False})
            self.log.update(last_stage=name)
            raise
        self.log.event("apdu", {"stage": name, "tx": "[PIN REDACTED]" if secret else apdu.hex().upper(),
            "data": "[REDACTED]" if secret else result.data.hex().upper(), "sw": f"{result.sw:04X}", "meaning": result.meaning,
            "elapsed_ms": round((self.clock() - started) * 1000)})
        if result.sw != 0x9000 and require_ok:
            self.log.update(last_stage=name, last_sw=f"{result.sw:04X}")
        if (self.settings.verbose and not quiet) or (result.sw != 0x9000 and require_ok):
            self.report(f"{name}: SW={result.sw:04X} ({result.meaning})" +
                        (f"; DATA={hex_bytes(result.data)}" if result.data and not secret and self.settings.verbose else ""),
                        level="success" if result.sw == 0x9000 else "error" if require_ok else "warning")
        return result.require_ok(name) if require_ok else result

    def authenticate(self):
        self.begin_stage("权限", "检查访问权限...")
        access = self.exchange("D8", D8, require_ok=False)
        if access.sw == 0x9000:
            self.report("访问权限已确认，无需 PIN（D8=9000）。", level="success")
            return
        if access.sw != 0x6985 or self.board != "huawei":
            access.require_ok("D8; no PIN attempted")
        self.report("驱动板需要认证（D8=6985），正在验证已知 PIN...", level="warning")
        self.exchange("PIN", pin_apdu(self.settings.pin or FACTORY_PIN), secret=True)
        self.exchange("D8 after PIN", D8)
        self.report("认证通过（PIN=9000，D8=9000）。", level="success")

    def check_config(self, job, *, keep_driver=False):
        result = self.exchange("D1 config", D1)
        fields = board_info(result.data + b"\x90\x00", self.activation.uid)
        same = fields[0xA0] == job.geometry and fields[0xA1] == job.scan
        if keep_driver:
            same = fields[0xA0] == job.geometry and stored_scan_compatible(fields, job.geometry, job.scan)
        self.log.event("configuration_check", {
            "keep_driver": keep_driver, "geometry": fields[0xA0].hex().upper(),
            "expected_geometry": job.geometry.hex().upper(), "scan": fields[0xA1].hex().upper(),
            "expected_scan": job.scan.hex().upper(), "compatible": same,
            "appid": fields[0xC0].hex().upper(), "expected_appid": self.fields[0xC0].hex().upper(),
            "slots": fields[0xB1][0], "required_slots": len(job.planes)})
        if not same or fields[0xC0] != self.fields[0xC0] or fields[0xB1][0] < len(job.planes):
            self.report(f"D1 核对失败：几何字段 A0={fields[0xA0].hex().upper()}"
                        f"（期望 {job.geometry.hex().upper()}）；扫描字段 A1={fields[0xA1].hex().upper()}"
                        f"（期望 {job.scan.hex().upper()}）；APPID={fields[0xC0].hex().upper()}"
                        f"（期望 {self.fields[0xC0].hex().upper()}）；图槽 {fields[0xB1][0]}/{len(job.planes)}。",
                        level="error")
            raise ValueError("D1 UID/configuration/slots mismatch; no image or D4 sent")
        if keep_driver and fields[0xA1] != job.scan:
            self.report("保留原厂扫描模式 00（纵向），所选格式的参考模式为 01（横向）；"
                        "不写 DA/DB，图像排列仍需实测确认。", level="warning")
            self.log.update(factory_scan_preserved=True, stored_scan=fields[0xA1].hex().upper(),
                            selected_scan=job.scan.hex().upper())
        self.fields = fields

    def run(self, jobs, *, keep_driver=False, upload=True, timeout=None, capacity=None):
        if keep_driver and upload and any(job.half is not None for job in jobs):
            raise ValueError("Multi-refresh requires its own DB/DA; cannot keep an unknown driver")
        if not upload and (not keep_driver or len(jobs) != 1):
            raise ValueError("Existing-slot refresh must be one operation without DB/DA writes")
        # All jobs and capacity checks must be complete before any authentication/write.
        limit = self.capacity if capacity is None else capacity
        if not 1000 <= limit <= 512000 or any(j.raw_bytes > limit for j in jobs):
            raise ValueError("Job exceeds detected board's conservative capacity budget")
        if any(not j.minimum_observation < (timeout if timeout is not None else j.timeout) <= 600 for j in jobs):
            raise ValueError("Refresh deadline must exceed minimum observation, up to 600 s; no writes sent")
        if any(j.packets and j.packets[0][1] == 0xD3 for j in jobs) and self.fields[0xD1][0] != 1:
            raise ValueError("D1 does not declare D3 support; no configuration written")
        self.authenticate()
        for job in jobs:
            if job.half is not None:
                self.begin_stage("分段", f"第 {job.half + 1}/2 段：传图并刷新。")
            if self.settings.verbose:
                self.report(f"{job.name}: {job.raw_bytes} bytes, {len(job.packets)} APDUs; SHA256={job.sha256}")
            self.log.event("job", {"name": job.name, "raw_bytes": job.raw_bytes, "sha256": job.sha256,
                "half": job.half, "keep_driver": keep_driver, "upload": upload})
            if not keep_driver:
                self.begin_stage("配置", "设置所选屏幕驱动...")
                for i, apdu in enumerate(job.config):
                    self.exchange(f"{job.name} config {i + 1}/{len(job.config)}", apdu)
            else:
                self.begin_stage("配置", "检查已存屏幕配置，本次不更改驱动。")
            self.check_config(job, keep_driver=keep_driver)
            self.report("屏幕配置已确认（D1=9000）。", level="success")
            if upload:
                compressed = bool(job.packets and job.packets[0][1] == 0xD3)
                self.log.update(image_transport="d3" if compressed else "d2")
                self.begin_stage("传图", "开始压缩传图，请保持线圈位置不动。" if compressed
                            else "开始传图（不压缩），请保持线圈位置不动。")
                next_percent = 10
                for i, apdu in enumerate(job.packets):
                    if i:
                        self.pause(self.settings.packet_delay_ms)
                    self.exchange(f"{job.name} D{apdu[1] & 0x0F:X} {i + 1}/{len(job.packets)}", apdu, quiet=True)
                    percent = (i + 1) * 100 // len(job.packets)
                    if percent >= next_percent or i == len(job.packets) - 1:
                        self.report(f"传图 {percent:3d}%（{i + 1}/{len(job.packets)}，SW=9000）。",
                                    level="success" if percent == 100 else "info")
                        next_percent = (percent // 10 + 1) * 10
                self.log.update(upload_packets=len(job.packets), upload_bytes=job.raw_bytes)
            self.pause(self.settings.pre_refresh_ms)
            self.observe(job, timeout=timeout)
        self.log.event("result", {"outcome": "protocol_completed", "jobs": len(jobs), "optically_verified": False})
        self.stage = "结果"
        self.report("驱动板报告刷新结束，请确认屏幕画面是否完整。", level="success", detail=False)

    def observe(self, job, *, timeout=None):
        started = self.clock()
        duration = timeout if timeout is not None else job.timeout
        if not job.minimum_observation < duration <= 600:
            raise ValueError("Refresh timeout must exceed the recipe's minimum observation, up to 600 s")
        deadline = started + duration
        self.begin_stage("刷新", "正在启动屏幕刷新，请保持线圈位置不动...")
        d4 = self.exchange(f"{job.name} D4", job.d4, timeout=min(self.settings.d4_timeout, duration))
        if d4.data not in (b"", b"\x00", b"\x01"):
            raise ValueError("Unexpected D4 response payload")
        self.report("刷新指令已接受（D4=9000），等待屏幕完成...", level="success")
        self.pause(min(self.settings.first_de_delay_ms, max(0, (deadline - self.clock()) * 1000)))
        # D4's accepted/started byte is not an independent BUSY-pin observation.
        seen_busy = False
        idle_samples = 0
        last_state, last_output = None, -float("inf")

        def waiting(details):
            nonlocal last_output
            now = self.clock()
            if now - last_output >= 1:
                self.report(f"驱动板请求延时，等待 DE 回包，已用 {now - started:.1f} 秒"
                            f"（WTX={details['count']}，单次查询上限 {self.settings.de_timeout:g} 秒）...")
                last_output = now

        while self.clock() < deadline:
            poll_started = self.clock()
            remaining = deadline - poll_started
            if remaining < 2:
                break
            state = self.exchange(f"{job.name} DE", DE,
                                  timeout=min(self.settings.de_timeout, remaining), quiet=True, on_wtx=waiting)
            if state.data not in (b"\x00", b"\x01"):
                raise ValueError("DE has no valid BUSY byte")
            busy = state.data == b"\x01"
            seen_busy |= busy
            idle_samples = 0 if busy else idle_samples + 1
            elapsed = self.clock() - started
            if busy != last_state or self.clock() - last_output >= 1:
                self.report(f"{'正在刷新' if busy else '驱动板报告空闲'}，已用 {elapsed:.1f} 秒"
                            f"（DE={state.data.hex().upper()}，SW={state.sw:04X}）" + ("..." if busy else "。"))
                last_state, last_output = busy, self.clock()
            # Once DE has reported BUSY, stable idle completes the observed cycle.
            if not busy and idle_samples >= 3 and (seen_busy or elapsed >= job.minimum_observation):
                if job.half is not None and not seen_busy:
                    raise RuntimeError("No BUSY observed for this half; do not advance to the next half")
                if not seen_busy:
                    self.report("没有观察到刷新忙状态，尚不能确认屏幕已完成刷新。", level="warning")
                self.log.event("refresh_observed", {"job": job.name, "busy_observed": seen_busy,
                    "elapsed_seconds": elapsed, "optically_verified": False})
                self.log.update(refresh_seconds=round(elapsed, 2), busy_observed=seen_busy, d4_sw="9000", de_data="00")
                return
            self.sleep(max(0, 1 / self.settings.de_hz - (self.clock() - poll_started)))
        raise RuntimeError("Refresh observation deadline reached; no D4 replay or reconnect")
