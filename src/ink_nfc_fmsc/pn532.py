from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable

import serial


ACK = bytes.fromhex("00 00 FF 00 FF 00")
MAGSAFE_WUPA_CODES = (0x7A, 0x7B, 0x7C, 0x7D)
# Empirical FMSC behavior: 7A answers only from IDLE; 7D also answers after HLTA.
FMSC_REQA = MAGSAFE_WUPA_CODES[0]
FMSC_WUPA = MAGSAFE_WUPA_CODES[3]
WAKE_MODES = {
    "private": (FMSC_REQA, FMSC_WUPA),
    "standard": (0x26, 0x52),
    "auto": (0x26, 0x52, FMSC_REQA, FMSC_WUPA),
}


class Pn532Error(RuntimeError):
    pass


class Pn532Timeout(Pn532Error):
    def __init__(self, message: str, *, command_aborted: bool = False):
        self.command_aborted = command_aborted
        super().__init__(message)


class RfTransportError(Pn532Error):
    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class WireFrame:
    kind: str
    data: bytes
    offset: int


@dataclass(frozen=True)
class AtsInfo:
    raw: bytes
    fsc: int
    fwi: int
    sfgi: int
    fwt_ms: float
    ta: int | None
    tb: int | None
    tc: int | None
    historical: bytes


@dataclass(frozen=True)
class Activation:
    uid: bytes
    atqa: bytes
    sak: int
    ats: AtsInfo
    wake_command: int


def hex_bytes(data: bytes | bytearray | Iterable[int]) -> str:
    return " ".join(f"{value:02X}" for value in data)


def encode_frame(data: bytes) -> bytes:
    if not 1 <= len(data) <= 65535:
        raise ValueError("PN532 frame data must contain 1..65535 bytes")
    if len(data) <= 255:
        header = bytes((0, 0, 0xFF, len(data), (-len(data)) & 0xFF))
    else:
        high, low = divmod(len(data), 256)
        header = bytes((0, 0, 0xFF, 0xFF, 0xFF, high, low, (-high - low) & 0xFF))
    return header + data + bytes(((-sum(data)) & 0xFF, 0))


def parse_frames(raw: bytes) -> list[WireFrame]:
    frames: list[WireFrame] = []
    pos = 0
    while pos + 5 <= len(raw):
        if raw[pos : pos + 3] != b"\x00\x00\xff":
            pos += 1
            continue
        first, second = raw[pos + 3], raw[pos + 4]
        if (first, second) in ((0, 0xFF), (0xFF, 0)):
            if pos + 6 > len(raw):
                break
            if raw[pos + 5] != 0:
                pos += 1
                continue
            frames.append(WireFrame("ACK" if first == 0 else "NACK", b"", pos))
            pos += 6
            continue
        if first == second == 0xFF:
            if pos + 8 > len(raw):
                break
            high, low, lcs = raw[pos + 5 : pos + 8]
            if (high + low + lcs) & 0xFF:
                pos += 1
                continue
            length = high * 256 + low
            header_length = 8
        else:
            if first == 0 or ((first + second) & 0xFF):
                pos += 1
                continue
            length = first
            header_length = 5
        total = header_length + length + 2
        if length < 1 or length > 8192:
            pos += 1
            continue
        if pos + total > len(raw):
            break
        data = raw[pos + header_length : pos + header_length + length]
        dcs = raw[pos + header_length + length]
        postamble = raw[pos + total - 1]
        if ((sum(data) + dcs) & 0xFF) or postamble != 0:
            frames.append(WireFrame("INVALID", b"", pos))
            pos += 1
            continue
        frames.append(WireFrame("ERROR" if data == b"\x7f" else "DATA", data, pos))
        pos += total
    return frames


def crc_a(data: bytes) -> bytes:
    crc = 0x6363
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0x8408 if crc & 1 else 0)
        crc &= 0xFFFF
    return bytes((crc & 0xFF, crc >> 8))


def parse_ats(data: bytes) -> AtsInfo:
    if not data or data[0] != len(data):
        raise Pn532Error("ATS TL does not match the received byte count")
    if len(data) > 20:
        raise Pn532Error("ATS is longer than the ISO 14443-4 activation limit")
    fsc_table = (16, 24, 32, 40, 48, 64, 96, 128, 256)
    fsci, fwi, sfgi = 2, 4, 0
    ta = tb = tc = None
    index = 1
    if len(data) > 1:
        t0 = data[1]
        if t0 & 0x80:
            raise Pn532Error("ATS T0 has a reserved bit set")
        fsci = t0 & 0x0F
        if fsci > 8:
            raise Pn532Error("ATS FSCI is reserved")
        index = 2
        for flag, name in ((0x10, "ta"), (0x20, "tb"), (0x40, "tc")):
            if t0 & flag:
                if index >= len(data):
                    raise Pn532Error("ATS is missing an advertised interface byte")
                value = data[index]
                index += 1
                if name == "ta":
                    ta = value
                elif name == "tb":
                    tb = value
                    fwi, sfgi = value >> 4, value & 0x0F
                else:
                    tc = value
    if fwi == 15 or sfgi == 15:
        raise Pn532Error("ATS uses reserved FWI/SFGI=15")
    fwt_ms = (4096.0 / 13_560_000.0) * (2**fwi) * 1000.0
    return AtsInfo(data, fsc_table[fsci], fwi, sfgi, fwt_ms, ta, tb, tc, data[index:])


STATUS_NAMES = {
    0x01: "RF_TIMEOUT",
    0x02: "RF_CRC_ERROR",
    0x03: "RF_PARITY_ERROR",
    0x04: "RF_BIT_COUNT_ERROR",
    0x05: "RF_FRAMING_ERROR",
    0x06: "RF_COLLISION",
    0x07: "COMM_BUFFER_TOO_SMALL",
    0x09: "RF_BUFFER_OVERFLOW",
    0x0A: "RF_FIELD_ERROR",
    0x0B: "RF_PROTOCOL_ERROR",
    0x0D: "PN532_TEMPERATURE_ERROR",
    0x0E: "INTERNAL_BUFFER_OVERFLOW",
    0x10: "INVALID_PARAMETER",
    0x23: "UID_CHECK_BYTE_ERROR",
    0x25: "INVALID_DEVICE_STATE",
    0x26: "OPERATION_NOT_ALLOWED",
    0x27: "CONTEXT_NOT_ACCEPTABLE",
    0x29: "TARGET_RELEASED",
    0x2D: "PN532_OVER_CURRENT",
}


RAW_PROFILE = {
    0x6301: 0x3D,
    0x6302: 0x00,
    0x6303: 0x00,
    0x6304: 0x80,
    0x6305: 0x40,
    0x6306: 0x10,
    0x6307: 0x84,
    0x6308: 0x85,
    0x6309: 0x4D,
    0x630C: 0x62,
    0x630D: 0x00,
    0x6313: 0x6F,
    0x6314: 0x26,
    0x6315: 0x87,
    0x6316: 0x59,
    0x6317: 0xF4,
    0x6318: 0x3F,
    0x6319: 0x11,
    0x6338: 0x00,
    0x633C: 0x10,
    0x633D: 0x00,
    0x633E: 0x80,
}


class Pn532:
    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        logger: Callable[[str, dict], None] | None = None,
        *,
        wake_mode: str = "private",
        host_response_ack: bool = False,
    ) -> None:
        if wake_mode not in WAKE_MODES:
            raise ValueError(f"unknown wake mode: {wake_mode}")
        self.port_name = port
        self.baudrate = baudrate
        self.log = logger or (lambda _kind, _details: None)
        self.wake_request_codes = WAKE_MODES[wake_mode]
        self.serial: serial.Serial | None = None
        self.rf_on = False
        self.host_response_ack = host_response_ack
        self._timer_code: int | None = None

    def __enter__(self) -> "Pn532":
        self.open()
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()

    def open(self) -> None:
        if self.serial and self.serial.is_open:
            return
        self._timer_code = None
        self.serial = serial.Serial(
            self.port_name,
            self.baudrate,
            timeout=0.1,
            write_timeout=1.5,
            bytesize=8,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self.serial.dtr = False
        self.serial.rts = False
        time.sleep(0.1)
        self._drain()
        self.command(b"\x14\x01", 0x15, wake=True, name="SAMConfiguration")
        firmware = self.command(b"\x02", 0x03, name="GetFirmwareVersion")
        if len(firmware) < 4 or firmware[0] != 0x32:
            raise Pn532Error(f"unexpected PN532 firmware response: {hex_bytes(firmware)}")
        self.log(
            "firmware",
            {"ic": firmware[0], "version": f"{firmware[1]}.{firmware[2]}", "support": firmware[3]},
        )
        self.set_rf(False)
        self.command(bytes.fromhex("12 24"), 0x13, name="SetParameters")
        self.command(bytes.fromhex("32 05 FF 01 00"), 0x33, name="FiniteActivationRetries")

    def close(self) -> None:
        if not self.serial:
            return
        if self.serial.is_open:
            try:
                self.set_rf(False)
            except Exception as exc:
                self.log("rf_off_error", {"error": str(exc)})
            self.serial.close()
        self.serial = None

    def _drain(self) -> bytes:
        if not self.serial:
            return b""
        waiting = self.serial.in_waiting
        stale = self.serial.read(waiting) if waiting else b""
        if stale:
            self.log("stale_rx", {"hex": hex_bytes(stale)})
        return stale

    def _abort_pending(self) -> None:
        if not self.serial:
            return
        self.serial.write(ACK)
        self.serial.flush()
        time.sleep(0.1)
        self._drain()

    def command(
        self,
        command: bytes,
        expected: int,
        *,
        wait_ms: float = 2000,
        wake: bool = False,
        name: str = "PN532 command",
    ) -> bytes:
        if not self.serial or not self.serial.is_open:
            raise Pn532Error("serial port is not open")
        self._drain()
        wire = encode_frame(b"\xd4" + command)
        frame_format = "extended" if len(command) + 1 > 255 else "normal"
        if wake:
            wire = b"\x55\x55" + bytes(14) + wire
        self.log("pn532_tx", {"name": name, "hex": hex_bytes(wire),
                              "wire_bytes": len(wire), "frame_format": frame_format})
        self.serial.write(wire)
        self.serial.flush()
        raw = bytearray()
        deadline = time.monotonic() + wait_ms / 1000.0
        saw_ack = False
        while time.monotonic() < deadline:
            waiting = self.serial.in_waiting
            if waiting:
                raw.extend(self.serial.read(min(waiting, 4096)))
                if len(raw) > 16384:
                    self._abort_pending()
                    raise Pn532Error("unexpectedly large PN532 UART response")
                for frame in parse_frames(bytes(raw)):
                    saw_ack |= frame.kind == "ACK"
                    if frame.kind == "NACK":
                        raise Pn532Error(f"PN532 returned NACK for {name}")
                    if frame.kind == "ERROR":
                        raise Pn532Error(f"PN532 returned application error for {name}")
                    if (
                        frame.kind == "DATA"
                        and len(frame.data) >= 2
                        and frame.data[0] == 0xD5
                        and frame.data[1] == expected
                    ):
                        self.log("pn532_rx", {"name": name, "hex": hex_bytes(raw)})
                        if self.host_response_ack:
                            self.acknowledge_response()
                        return frame.data[2:]
            time.sleep(0.003)
        self.log("pn532_timeout", {"name": name, "ack": saw_ack, "hex": hex_bytes(raw),
                                   "wire_bytes": len(wire), "frame_format": frame_format})
        # A lost ACK does not prove the reader rejected the command. Cancel it
        # before diagnostics, otherwise a late RF response can occupy that read.
        aborted = False
        try:
            self.log("pn532_timeout_abort", {"name": name, "ack_received": saw_ack,
                                             "apdu_retry": False, "rf_reenabled": False})
            self._abort_pending()
            aborted = True
        except Exception as cleanup_error:
            self.log("pn532_timeout_abort_error", {"name": name, "error": str(cleanup_error)})
        if saw_ack:
            raise Pn532Timeout(f"{name}: ACK received but command response timed out", command_aborted=aborted)
        raise Pn532Timeout(
            f"{name}: no valid PN532 response (no UART ACK; {len(wire)}-byte {frame_format} host frame)",
            command_aborted=aborted,
        )

    def write_registers(self, values: dict[int, int]) -> None:
        command = bytearray((0x08,))
        for address in sorted(values):
            value = values[address]
            if not 0x6300 <= address <= 0x633F or not 0 <= value <= 255:
                raise ValueError("invalid CIU register write")
            command.extend((address >> 8, address & 0xFF, value))
        response = self.command(bytes(command), 0x09, name="WriteRegister")
        if response:
            raise Pn532Error(f"unexpected WriteRegister payload: {hex_bytes(response)}")

    def acknowledge_response(self) -> None:
        """Optional host response ACK, UM0701-02 6.2.2; not a command retry."""
        if self.serial is None or not self.serial.is_open:
            raise Pn532Error("serial port is not open")
        self.serial.write(ACK)
        self.serial.flush()
        self.log("pn532_host_response_ack", {"hex": hex_bytes(ACK), "apdu_retry": False})

    def read_registers(self, addresses: Iterable[int]) -> dict[int, int]:
        addresses = list(addresses)
        command = bytearray((0x06,))
        for address in addresses:
            command.extend((address >> 8, address & 0xFF))
        response = self.command(bytes(command), 0x07, name="ReadRegister")
        if len(response) != len(addresses):
            raise Pn532Error("ReadRegister response length mismatch")
        return dict(zip(addresses, response, strict=True))

    def set_rf(self, enabled: bool) -> None:
        self.command(bytes((0x32, 0x01, int(enabled))), 0x33, name="RF ON" if enabled else "RF OFF")
        self.rf_on = enabled

    def set_framing(self, bits: int, crc: bool) -> None:
        mode = 0x80 if crc else 0
        self.write_registers({0x6302: mode, 0x6303: mode, 0x633D: bits & 7})
        actual = self.read_registers((0x6302, 0x6303, 0x633D))
        if actual[0x6302] != mode or actual[0x6303] != mode or (actual[0x633D] & 0x77) != (bits & 7):
            raise Pn532Error("CIU framing readback mismatch")

    def prepare_type_a(self) -> None:
        profile = dict(RAW_PROFILE)
        profile[0x6304] = 0x83 if self.rf_on else 0x80
        self.write_registers(profile)
        self.command(bytes.fromhex("32 02 00 0B 0A"), 0x33, name="RawResponseTimeout")
        self._timer_code = 0x0A
        self.command(bytes.fromhex("32 04 00"), 0x33, name="NoRfRetry")

    def set_iso_timeout(self, requested_ms: float) -> float:
        if requested_ms <= 0 or requested_ms > 30_000:
            raise ValueError("ISO-DEP wait must be within 0..30000 ms")
        code = 0
        actual = requested_ms
        for candidate in range(1, 17):
            candidate_ms = 0.1 * (2 ** (candidate - 1))
            if candidate_ms >= requested_ms:
                code, actual = candidate, candidate_ms
                break
        if self._timer_code != code:
            self.command(bytes((0x32, 0x02, 0, 0x0B, code)), 0x33, name="IsoDepTimeout")
            self._timer_code = code
            self.log(
                "pn532_rf_timeout",
                {
                    "requested_ms": requested_ms,
                    "timer_code": f"{code:02X}",
                    "hardware_timeout_ms": actual if code else None,
                    "host_watchdog": code == 0,
                },
            )
        return actual

    def abort_command(self) -> None:
        """Abort a pending PN532 command with the documented host ACK."""
        if self.serial is None:
            raise Pn532Error("serial port is not open")
        self.log("pn532_abort", {"hex": hex_bytes(ACK)})
        self.serial.write(ACK)
        self.serial.flush()

    def communicate_thru(
        self,
        block: bytes,
        requested_wait_ms: float,
        *,
        host_wait_ms: float | None = None,
    ) -> bytes:
        actual_wait = self.set_iso_timeout(requested_wait_ms)
        # RFConfiguration's longest finite timer is 3276.8 ms. Code 00
        # disables it, so longer waits need a bounded host watchdog and abort.
        if self._timer_code == 0:
            command_wait = requested_wait_ms + 700
            if host_wait_ms is not None:
                command_wait = min(command_wait, host_wait_ms)
        else:
            command_wait = actual_wait + 700
            if host_wait_ms is not None:
                command_wait = min(command_wait, host_wait_ms)
        try:
            response = self.command(
                b"\x42" + block,
                0x43,
                wait_ms=command_wait,
                name="InCommunicateThru",
            )
        except Pn532Timeout as error:
            if self._timer_code != 0:
                raise
            if not error.command_aborted:
                self.abort_command()
            raise Pn532Timeout(
                f"InCommunicateThru: host silence watchdog expired after "
                f"{command_wait:g} ms; PN532 command aborted; RF state needs a register check",
                command_aborted=True,
            ) from error
        if not response:
            raise Pn532Error("empty InCommunicateThru response")
        status = response[0]
        code = status & 0x3F
        if status != 0:
            name = STATUS_NAMES.get(code, f"PN532_STATUS_0x{code:02X}")
            raise RfTransportError(code, f"RF transport failed: {name}")
        return response[1:]

    def _complete_type_a_activation(
        self, atqa: bytes, *, request_code: int
    ) -> Activation:
        if len(atqa) != 2:
            raise Pn532Error(
                f"{request_code:02X} returned invalid ATQA: {hex_bytes(atqa)}"
            )
        uid = bytearray()
        final_sak = 0
        for cascade_level in range(1, 4):
            select = 0x91 + 2 * cascade_level
            self.set_framing(8, False)
            block = self.communicate_thru(bytes((select, 0x20)), 100)
            if len(block) != 5 or block[4] != (
                block[0] ^ block[1] ^ block[2] ^ block[3]
            ):
                raise Pn532Error("anticollision UID/BCC response is invalid")
            self.set_framing(8, True)
            sak_data = self.communicate_thru(bytes((select, 0x70)) + block, 100)
            if len(sak_data) != 1:
                raise Pn532Error("SELECT returned an invalid SAK")
            final_sak = sak_data[0]
            cascade = bool(final_sak & 0x04)
            has_ct = cascade_level < 3 and block[0] == 0x88
            if cascade:
                if not has_ct:
                    raise Pn532Error("cascade SAK without cascade tag")
                uid.extend(block[1:4])
                continue
            if has_ct:
                raise Pn532Error("cascade tag present without cascade SAK")
            uid.extend(block[:4])
            break
        else:
            raise Pn532Error("UID cascade did not terminate")
        if not (final_sak & 0x20):
            raise Pn532Error(
                f"selected NFC-A target is not ISO-DEP (SAK={final_sak:02X})"
            )
        ats_raw = self.communicate_thru(bytes.fromhex("E0 80"), 100)
        ats = parse_ats(ats_raw)
        activation = Activation(bytes(uid), atqa, final_sak, ats, request_code)
        self.log(
            "activation",
            {
                "uid": hex_bytes(activation.uid),
                "atqa": hex_bytes(atqa),
                "sak": f"{final_sak:02X}",
                "ats": hex_bytes(ats_raw),
                "fsc": ats.fsc,
                "fwt_ms": ats.fwt_ms,
                "request_code": f"{request_code:02X}",
            },
        )
        return activation

    def activate_in_field(
        self,
        *,
        request_codes: tuple[int, ...] | None = None,
    ) -> Activation:
        """Select an FMSC target without switching the RF carrier off.

        This is diagnostic recovery for a target that may have reset during a
        display power operation. It deliberately preserves harvested power.
        """
        if not self.rf_on:
            raise Pn532Error("in-field activation requires the RF carrier to be on")
        if request_codes is None:
            request_codes = self.wake_request_codes
        self.prepare_type_a()
        errors: list[str] = []
        for request_code in request_codes:
            try:
                self.set_framing(7, False)
                atqa = self.communicate_thru(bytes((request_code,)), 100)
                return self._complete_type_a_activation(
                    atqa, request_code=request_code
                )
            except (Pn532Error, RfTransportError) as exc:
                errors.append(f"{request_code:02X}: {exc}")
        raise Pn532Error("in-field activation failed; " + "; ".join(errors))

    def activate(self, *, off_ms: int = 1500, first_wait_ms: int = 5) -> Activation:
        errors: list[str] = []
        for delay_ms in (first_wait_ms, 200):
            try:
                self.set_rf(False)
                off_started = time.monotonic()
                self.prepare_type_a()
                self.set_framing(7, False)
                remaining = off_ms / 1000.0 - (time.monotonic() - off_started)
                if remaining > 0:
                    time.sleep(remaining)
                self.set_rf(True)
                time.sleep(delay_ms / 1000.0)
                # External panel power can keep the target out of IDLE across
                # RF cycles. Try the known private WUPA before cycling again.
                for request_code in self.wake_request_codes:
                    try:
                        self.prepare_type_a()
                        self.set_framing(7, False)
                        atqa = self.communicate_thru(bytes((request_code,)), 100)
                        return self._complete_type_a_activation(
                            atqa, request_code=request_code
                        )
                    except (Pn532Error, RfTransportError) as exc:
                        errors.append(f"{request_code:02X}/{delay_ms}ms: {exc}")
            except (Pn532Error, RfTransportError) as exc:
                errors.append(f"RF/{delay_ms}ms: {exc}")
        try:
            self.set_rf(False)
        except Pn532Error:
            pass
        raise Pn532Error("activation failed; " + "; ".join(errors))
