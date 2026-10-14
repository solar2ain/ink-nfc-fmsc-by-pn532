from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .pn532 import Activation, Pn532, Pn532Error, RfTransportError, hex_bytes


class IsoDepTimeout(Pn532Error):
    pass


@dataclass(frozen=True)
class IsoBlock:
    kind: str
    pcb: int
    number: int | None = None
    more: bool = False
    data: bytes = b""
    factor: int | None = None
    raw: bytes = b""


def parse_block(data: bytes, *, fsc: int, cid_supported: bool) -> IsoBlock:
    if not data or len(data) + 2 > fsc:
        raise Pn532Error("ISO-DEP frame is empty or larger than negotiated FSC")
    pcb = data[0]
    position = 1
    cid_present = bool(pcb & 0x08)
    if cid_present:
        if len(data) < 2 or data[1] != 0 or not cid_supported:
            raise Pn532Error("unexpected ISO-DEP CID")
        position += 1
    if pcb & 0xE2 == 0x02:
        if pcb & 0x04:
            raise Pn532Error("ISO-DEP NAD was not negotiated")
        return IsoBlock("I", pcb, pcb & 1, bool(pcb & 0x10), data[position:], raw=data)
    if pcb & 0xE6 == 0xA2:
        if len(data) != position:
            raise Pn532Error("R-block has an unexpected payload")
        return IsoBlock("R_NAK" if pcb & 0x10 else "R_ACK", pcb, pcb & 1, raw=data)
    if pcb & 0xF7 == 0xF2:
        if len(data) != position + 1:
            raise Pn532Error("invalid S(WTX) length")
        factor = data[position] & 0x3F
        if not 1 <= factor <= 59:
            raise Pn532Error("invalid S(WTX) multiplier")
        return IsoBlock("WTX", pcb, factor=factor, raw=data)
    if pcb & 0xF7 == 0xC2:
        raise Pn532Error("target requested ISO-DEP deselect")
    raise Pn532Error(f"unsupported ISO-DEP PCB 0x{pcb:02X}")


class IsoDepSession:
    def __init__(
        self, pn532: Pn532, activation: Activation, *, max_inf_bytes: int | None = None
    ) -> None:
        if max_inf_bytes is not None and not 1 <= max_inf_bytes <= 253:
            raise ValueError("ISO-DEP INF limit must be within 1..253 bytes")
        self.pn532 = pn532
        self.activation = activation
        self.max_inf_bytes = max_inf_bytes
        self.block_number = 0
        self.cid_supported = bool(activation.ats.tc is not None and activation.ats.tc & 0x02)
        self.use_cid = False
        self.active = True
        self._last_wire_tx: bytes | None = None

    def _control(self, pcb: int) -> bytes:
        return bytes(((pcb | 0x08), 0)) if self.use_cid else bytes((pcb,))

    def transceive(
        self,
        apdu: bytes,
        *,
        timeout_s: float = 30.0,
        max_wtx: int = 16,
        minimum_wait_ms: float = 0.0,
        recover_responses: int = 0,
        retry_de_timeouts: int = 0,
        frame_gap_ms: float = 0.0,
        on_wtx: Callable[[dict], None] | None = None,
    ) -> bytes:
        if not self.active:
            raise Pn532Error("ISO-DEP session is no longer active")
        if not 4 <= len(apdu) <= 4096:
            raise ValueError("APDU length must be within 4..4096 bytes")
        if not 0 <= recover_responses <= 3:
            raise ValueError("response recovery limit must be within 0..3")
        if not 0 <= retry_de_timeouts <= 10:
            raise ValueError("DE timeout retry limit must be within 0..10")
        if retry_de_timeouts and (apdu != bytes.fromhex("F0DE000001") or recover_responses):
            raise ValueError("DE timeout retries only allow the exact read-only DE APDU, without R(NAK) recovery")
        if not 0 <= frame_gap_ms <= min(5.0, self.activation.ats.fwt_ms / 4):
            raise ValueError("frame gap must be 0..5 ms and at most one quarter of negotiated FWT")
        if frame_gap_ms:
            self.pn532.log("iso_frame_pacing", {"gap_ms": frame_gap_ms,
                "fwt_ms": self.activation.ats.fwt_ms, "apdu_resent": False})
        base_wait = max(100.0, self.activation.ats.fwt_ms * 1.10 + 20.0, minimum_wait_ms)
        offset = 0

        def next_command_block() -> tuple[bytes, int, bool]:
            capacity = self.activation.ats.fsc - (4 if self.use_cid else 3)
            # A bridge's UART limit can be smaller than the tag's negotiated FSC.
            if self.max_inf_bytes is not None:
                capacity = min(capacity, self.max_inf_bytes)
            end = min(len(apdu), offset + capacity)
            more = end < len(apdu)
            pcb = 0x02 | self.block_number | (0x10 if more else 0)
            return self._control(pcb) + apdu[offset:end], end, more

        tx, next_offset, command_more = next_command_block()
        wait_ms = base_wait
        output = bytearray()
        wtx_count = 0
        recovery_count = 0
        de_retry_count = 0
        exchanges = 0
        # Long FMSC display commands emit WTX roughly every 50 ms.  Keep the
        # transport guard above the caller's WTX allowance so it does not
        # terminate an otherwise healthy exchange first.
        max_exchanges = max(128, max_wtx + 32)
        started = time.monotonic()
        deadline = started + timeout_s
        try:
            while True:
                exchanges += 1
                if frame_gap_ms:
                    time.sleep(frame_gap_ms / 1000)
                if exchanges > max_exchanges:
                    raise Pn532Error("ISO-DEP exchange count limit exceeded")
                if time.monotonic() >= deadline:
                    raise IsoDepTimeout("ISO-DEP exchange deadline exceeded")
                if time.monotonic() + wait_ms / 1000.0 > deadline:
                    raise IsoDepTimeout("ISO-DEP wait would exceed command deadline")
                remaining_ms = max(100.0, (deadline - time.monotonic()) * 1000.0)
                self._last_wire_tx = tx
                try:
                    response = self.pn532.communicate_thru(
                        tx,
                        wait_ms,
                        host_wait_ms=remaining_ms,
                    )
                except RfTransportError as exc:
                    if (exc.code == 1 and retry_de_timeouts and not command_more and not output
                            and de_retry_count < retry_de_timeouts):
                        de_retry_count += 1
                        # Repeat only this read with its unchanged block number.
                        # A response may be cached; a subsequent new DE is needed.
                        tx = self._control(0x02 | self.block_number) + apdu
                        wait_ms = base_wait
                        self.pn532.log("iso_de_timeout_retry", {
                            "attempt": de_retry_count, "limit": retry_de_timeouts,
                            "rf_error": exc.code, "tx": hex_bytes(tx), "apdu_resent": True,
                            "block_number": self.block_number, "rf_cycled": False,
                            "session_reselected": False, "cached_reply_possible": True,
                        })
                        continue
                    if command_more or exc.code not in (1, 2, 3) or recovery_count >= recover_responses:
                        raise
                    recovery_count += 1
                    # R(NAK) requests the response again, not another execution
                    # of the application write. A returned R(ACK) still stops us.
                    tx = self._control(0xB2 | self.block_number)
                    wait_ms = base_wait
                    self.pn532.log("iso_response_recovery", {
                        "attempt": recovery_count, "rf_error": exc.code,
                        "tx": hex_bytes(tx), "apdu_resent": False,
                    })
                    continue
                block = parse_block(
                    response,
                    fsc=self.activation.ats.fsc,
                    cid_supported=self.cid_supported,
                )
                if block.pcb & 0x08:
                    self.use_cid = True
                if block.kind == "WTX":
                    wtx_count += 1
                    if wtx_count > max_wtx:
                        raise Pn532Error("ISO-DEP WTX count limit exceeded")
                    requested = max(
                        100.0,
                        self.activation.ats.fwt_ms * block.factor * 1.10 + 20.0,
                        minimum_wait_ms,
                    )
                    now = time.monotonic()
                    details = {"count": wtx_count, "factor": block.factor, "wait_ms": requested,
                               "elapsed_seconds": round(now - started, 3),
                               "remaining_seconds": round(max(0, deadline - now), 3)}
                    self.pn532.log("iso_wtx", details)
                    if on_wtx is not None:
                        on_wtx(details)
                    if time.monotonic() + requested / 1000.0 > deadline:
                        raise IsoDepTimeout("ISO-DEP WTX would exceed command deadline")
                    tx = block.raw
                    wait_ms = requested
                    continue
                if command_more:
                    # PICC R(ACK) acknowledges the current PCD block number.
                    # Advance only on a matching ACK; never resend a write here.
                    if block.kind != "R_ACK" or block.number != self.block_number:
                        raise Pn532Error(
                            f"expected matching R_ACK for chained command, received {hex_bytes(block.raw)}"
                        )
                    self.block_number ^= 1
                    offset = next_offset
                    tx, next_offset, command_more = next_command_block()
                    wait_ms = base_wait
                    continue
                if block.kind != "I":
                    raise Pn532Error(
                        f"unexpected {block.kind} while awaiting APDU response: {hex_bytes(block.raw)}"
                    )
                if block.number != self.block_number:
                    raise Pn532Error("ISO-DEP response block number mismatch")
                output.extend(block.data)
                if len(output) > 4096:
                    raise Pn532Error("ISO-DEP application response is unexpectedly large")
                self.block_number ^= 1
                if not block.more:
                    return bytes(output)
                tx = self._control(0xA2 | self.block_number)
                wait_ms = base_wait
        except Exception:
            self.active = False
            raise

    def deselect_in_field(self, *, wait_ms: float = 500.0,
                          host_wait_ms: float | None = None) -> None:
        """Abandon this exchange without switching the RF carrier off."""
        self.active = False
        tx = self._control(0xC2)
        self.pn532.log("iso_deselect_in_field", {"tx": hex_bytes(tx),
            "apdu_resent": False, "rf_cycled": False})
        response = self.pn532.communicate_thru(tx, wait_ms, host_wait_ms=host_wait_ms)
        if response != tx:
            raise Pn532Error(f"unexpected S(DESELECT) response: {hex_bytes(response)}")

    def probe_pending_exchange(self, *, wait_ms: float = 500.0) -> IsoBlock:
        """Retry the last ISO-DEP frame without cycling the RF field.

        This is diagnostic-only: it checks whether a target that stopped during a
        long display operation can still service the pending exchange. It does
        not reactivate the target or alter the session block number.
        """
        if self._last_wire_tx is None:
            raise Pn532Error("no pending ISO-DEP frame is available for probing")
        response = self.pn532.communicate_thru(self._last_wire_tx, wait_ms)
        return parse_block(
            response,
            fsc=self.activation.ats.fsc,
            cid_supported=self.cid_supported,
        )
