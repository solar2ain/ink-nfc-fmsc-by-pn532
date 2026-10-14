from dataclasses import replace

import pytest

from ink_nfc_fmsc.catalog import select_screen
from ink_nfc_fmsc.device import Device, RunLog, Settings
from ink_nfc_fmsc.images import prepare_image
from ink_nfc_fmsc.plans import jobs_for
from ink_nfc_fmsc.pn532 import Activation, Pn532Timeout, RfTransportError, parse_ats
from ink_nfc_fmsc.protocol import D1, D8, DE, FACTORY_PIN, pin_apdu


class Clock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeReader:
    def __init__(self, port, baud, logger, wake_mode):
        self.private = wake_mode != "standard"
        self.closed = False
        self.rf_on = False
        self.mode = wake_mode

    def open(self):
        pass

    def activate(self, **_kwargs):
        self.rf_on = True
        return Activation(bytes.fromhex("01020304"), b"\x04\x00", 0x20, parse_ats(bytes.fromhex("0208")), 0x7A if self.private else 0x26)

    def close(self):
        self.rf_on = False
        self.closed = True


class FakeSession:
    def __init__(self, reader, activation, *, max_inf_bytes):
        self.reader, self.activation, self.max_inf = reader, activation, max_inf_bytes
        self.fields = {0xA0: bytes.fromhex("F0C72006000228"), 0xA1: bytes.fromhex("01120030FFFFFF"),
                       0xC0: bytes.fromhex("00000001" if reader.private else "201EA001"),
                       0xC1: activation.uid, 0xB1: b"\x02", 0xD1: b"\x01" + b"\x00" * 6}
        self.authorized = not reader.private
        self.sent = []
        self.flow = bytearray()
        self.busy = 0
        self.failure = None
        self.interrupt = False

    def transceive(self, apdu, **_kwargs):
        assert self.reader.rf_on, "RF must stay enabled during every transaction"
        self.sent.append(apdu)
        if apdu == D1:
            return b"".join(bytes((t, len(v))) + v for t, v in self.fields.items()) + b"\x90\x00"
        if apdu == D8:
            return b"\x00" * 14 + b"\x90\x00" if self.authorized else b"\x69\x85"
        if apdu[:2] == b"\x00\x20":
            self.authorized = True
        if apdu[:3] == bytes.fromhex("F0DB02"):
            self.flow.clear()
        elif apdu[:2] == bytes.fromhex("F0DB"):
            self.flow.extend(apdu[5:])
        elif apdu[:3] == bytes.fromhex("F0DA00"):
            self.fields[0xA0] = b"\xf0" + self.flow[2:8]
        elif apdu[:3] == bytes.fromhex("F0DA01"):
            self.fields[0xA1] = b"\x01" + apdu[5:].ljust(6, b"\xff")
        elif apdu[:2] == bytes.fromhex("F0D4"):
            self.busy = 1
            return b"\x01\x90\x00"
        elif apdu == DE:
            if self.interrupt:
                raise KeyboardInterrupt()
            if isinstance(self.failure, Exception):
                raise self.failure
            if self.failure is not None:
                return self.failure
            result = bytes([self.busy]) + b"\x90\x00"
            self.busy = 0
            return result
        return b"\x90\x00"


def device(tmp_path, **kwargs):
    clock = Clock()
    settings = Settings(port="SIM", precharge_ms=0, **kwargs)
    result = Device(settings, RunLog(tmp_path), reader_factory=FakeReader, session_factory=FakeSession,
                    clock=clock.time, sleep=clock.sleep, output=lambda _x: None)
    return result, clock


def plan(alias="huawei-a0", **kwargs):
    s = select_screen(alias)
    planes, _ = prepare_image(s, white=True)
    return jobs_for(s, planes, **kwargs)


def test_huawei_authenticates_once_and_stays_in_field_through_d4_de(tmp_path):
    dev, clock = device(tmp_path, board="huawei")
    with dev:
        dev.run(plan())
        sent = dev.session.sent
        assert sent.count(pin_apdu(FACTORY_PIN)) == 1
        assert sent.count(D8) == 2
        assert sum(p[1] == 0xD4 for p in sent) == 1
        assert sum(p == DE for p in sent) >= 3
        assert dev.session.max_inf == 240
        assert clock.now < 1
        assert sent.count(DE) == 4
    assert dev.reader.closed and not dev.reader.rf_on


def test_busy_progress_uses_ellipsis_and_idle_uses_period(tmp_path):
    dev, _ = device(tmp_path, board="huawei")
    lines = []
    dev.output = lines.append
    with dev:
        dev.run(plan())
    busy = [line for line in lines if "正在刷新，已用" in line]
    idle = [line for line in lines if "驱动板报告空闲，已用" in line]
    assert busy and all(line.endswith("...") for line in busy)
    assert idle and all(line.endswith("。") for line in idle)


@pytest.mark.parametrize("private,board", [(True, "huawei"), (False, "iphone")])
def test_unspecified_board_uses_auto_wake_and_authenticates_only_huawei(tmp_path, private, board):
    class AutoReader(FakeReader):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.private = private

    clock = Clock()
    dev = Device(Settings(port="SIM", precharge_ms=0), RunLog(tmp_path), reader_factory=AutoReader,
                 session_factory=FakeSession, clock=clock.time, sleep=clock.sleep, output=lambda _x: None)
    with dev:
        assert dev.reader.mode == "auto" and dev.board == board
        assert dev.log.summary["board_name"] == ("华为Nova墨水屏手机壳NFC驱动板" if private
                                                else "FMSC墨水屏NFC驱动板")
        dev.run(plan("SSD1683-400x300-BWR"))
        assert dev.session.sent.count(pin_apdu(FACTORY_PIN)) == int(private)
        assert any(p[1] == 0xD3 for p in dev.session.sent)
    assert dev.reader.closed


def test_undeclared_d3_stops_before_pin_and_writes_but_explicit_d2_works(tmp_path):
    dev, _ = device(tmp_path, board="huawei")
    with dev:
        dev.fields[0xD1] = dev.session.fields[0xD1] = b"\x00" * 7
        with pytest.raises(ValueError, match="D3 support"):
            dev.run(plan("SSD1683-400x300-BWR"))
        assert dev.session.sent == [D1]
        dev.run(plan("SSD1683-400x300-BWR", transport="d2"))
        assert sum(p[1] == 0xD2 for p in dev.session.sent) == 120
        assert not any(p[1] == 0xD3 for p in dev.session.sent)
        assert dev.session.sent.count(pin_apdu(FACTORY_PIN)) == 1


@pytest.mark.parametrize("failure", [RfTransportError(1, "RF_TIMEOUT"), bytes.fromhex("6992")])
def test_compressed_upload_failure_does_not_fall_back_or_start_refresh(tmp_path, failure, monkeypatch):
    original = FakeSession.transceive

    def reject_d3(self, apdu, **kwargs):
        if apdu[1] == 0xD3:
            self.sent.append(apdu)
            if isinstance(failure, Exception):
                raise failure
            return failure
        return original(self, apdu, **kwargs)

    monkeypatch.setattr(FakeSession, "transceive", reject_d3)
    dev, _ = device(tmp_path, board="huawei")
    with pytest.raises(Exception):
        with dev:
            dev.run(plan("SSD1683-400x300-BWR"))
    assert sum(p[1] == 0xD3 for p in dev.session.sent) == 1
    assert not any(p[1] in (0xD2, 0xD4) for p in dev.session.sent)
    assert dev.reader.closed


@pytest.mark.parametrize("reader,limit", [("auto", 240), ("pcr532", 240), ("pn532", 253)])
def test_reader_policy_and_iphone_no_pin_two_pass(tmp_path, reader, limit):
    dev, _ = device(tmp_path, board="iphone", reader=reader)
    with dev:
        dev.run(plan(capacity=dev.capacity))
        sent = dev.session.sent
        assert dev.session.max_inf == limit
        assert dev.reader.mode == "standard"
        assert not any(p[:2] == b"\x00\x20" for p in sent)
        assert sum(p[1] == 0xD4 for p in sent) == 2
        assert max(p[5] for p in sent if p[1] == 0xD3) == 26
        assert sum(p[:3] == bytes.fromhex("F0DB02") for p in sent) == 2


@pytest.mark.parametrize("failure", [bytes.fromhex("68CA"), bytes.fromhex("6985"), RfTransportError(1, "RF_TIMEOUT")])
def test_first_half_failure_stops_without_reauthentication_or_d4_replay(tmp_path, failure):
    dev, _ = device(tmp_path, board="iphone")
    with pytest.raises(Exception):
        with dev:
            dev.session.failure = failure
            dev.run(plan(capacity=dev.capacity))
    sent = dev.session.sent
    assert sum(p[1] == 0xD4 for p in sent) == 1
    assert sum(p[:3] == bytes.fromhex("F0DB02") for p in sent) == 1
    assert sent.count(D8) == 1
    assert dev.reader.closed


def test_de_does_not_replay_write_and_default_frequency_is_9hz(tmp_path):
    dev, clock = device(tmp_path, board="iphone")
    with dev:
        start = clock.now
        job = replace(plan(capacity=80000, passes="first")[0], minimum_observation=1)
        dev.run((job,))
        polls = dev.session.sent.count(DE)
        assert polls == 4
        assert clock.now - start == pytest.approx(3 / 9)
        assert sum(p[1] == 0xD4 for p in dev.session.sent) == 1


@pytest.mark.parametrize("query_limit,total_limit,expected", [(5, 150, 5), (3, 150, 3), (5, 4, 4)])
def test_unanswered_de_has_its_own_deadline_and_stops_without_replay(
        tmp_path, monkeypatch, query_limit, total_limit, expected):
    dev, clock = device(tmp_path, de_timeout=query_limit)
    original = FakeSession.transceive
    waits = []

    def unanswered(self, apdu, **kwargs):
        if apdu == DE:
            self.sent.append(apdu)
            waits.append(kwargs["timeout_s"])
            clock.sleep(kwargs["timeout_s"])
            raise Pn532Timeout("DE response timed out")
        return original(self, apdu, **kwargs)

    monkeypatch.setattr(FakeSession, "transceive", unanswered)
    job = replace(plan()[0], minimum_observation=1)
    with pytest.raises(Pn532Timeout):
        with dev:
            dev.run((job,), timeout=total_limit)
    assert waits == [expected]
    assert clock.now == expected
    assert dev.session.sent.count(DE) == 1
    assert sum(p[1] == 0xD4 for p in dev.session.sent) == 1
    assert dev.reader.closed


def test_de_wtx_wait_reports_progress_before_the_reply(tmp_path, monkeypatch):
    dev, clock = device(tmp_path)
    original = FakeSession.transceive
    lines = []
    dev.output = lines.append

    def delayed(self, apdu, **kwargs):
        if apdu == DE:
            self.sent.append(apdu)
            for count in range(1, 7):
                clock.sleep(0.5)
                kwargs["on_wtx"]({"count": count, "factor": 1, "wait_ms": 1000})
            raise RfTransportError(2, "RF_CRC_ERROR")
        return original(self, apdu, **kwargs)

    monkeypatch.setattr(FakeSession, "transceive", delayed)
    with pytest.raises(RfTransportError):
        with dev:
            dev.run(plan())
    progress = [line for line in lines if "WTX=" in line]
    assert len(progress) == 3
    assert "DE" in progress[0] and "0.5" in progress[0]
    assert "2.5" in progress[-1]
    assert not any("DE=00" in line or "刷新结束" in line for line in lines)
    assert dev.session.sent.count(DE) == 1


def test_d4_started_byte_does_not_replace_independent_busy_for_half(tmp_path):
    dev, _ = device(tmp_path, board="iphone")
    with dev:
        dev.session.failure = bytes.fromhex("009000")
        with pytest.raises(RuntimeError, match="No BUSY"):
            dev.run(plan(capacity=80000))
        assert sum(p[1] == 0xD4 for p in dev.session.sent) == 1


def test_unobserved_busy_retains_minimum_observation_for_full_frame(tmp_path):
    dev, clock = device(tmp_path, board="huawei")
    with dev:
        dev.session.failure = bytes.fromhex("009000")
        job = replace(plan()[0], minimum_observation=1)
        dev.run((job,))
        assert clock.now >= 1
        assert dev.log.summary["busy_observed"] is False


def test_transient_idle_does_not_end_refresh(tmp_path, monkeypatch):
    original = FakeSession.transceive
    states = iter((1, 0, 0, 1, 0, 0, 0))

    def sequence(self, apdu, **kwargs):
        if apdu == DE:
            self.sent.append(apdu)
            return bytes((next(states), 0x90, 0))
        return original(self, apdu, **kwargs)

    monkeypatch.setattr(FakeSession, "transceive", sequence)
    dev, clock = device(tmp_path, board="huawei")
    with dev:
        dev.run(plan())
        assert dev.session.sent.count(DE) == 7
        assert clock.now == pytest.approx(6 / 9)


def test_cancel_closes_field_immediately_and_does_not_restart(tmp_path):
    dev, _ = device(tmp_path, board="iphone")
    with pytest.raises(KeyboardInterrupt):
        with dev:
            dev.session.interrupt = True
            dev.run(plan(capacity=80000))
    assert dev.reader.closed and not dev.reader.rf_on
    assert sum(p[1] == 0xD4 for p in dev.session.sent) == 1


def test_keep_driver_and_refresh_only_do_not_modify_config(tmp_path):
    dev, _ = device(tmp_path, board="iphone")
    with dev:
        jobs = plan(capacity=200000)
        job = replace(jobs[0], config=(), planes=(b"",), packets=())
        dev.run((job,), keep_driver=True, upload=False)
        assert not any(p[1] in (0xDB, 0xDA, 0xD2, 0xD3) for p in dev.session.sent)
        assert sum(p[1] == 0xD4 for p in dev.session.sent) == 1


def test_bad_geometry_and_uid_or_deadline_prevent_image_and_refresh(tmp_path):
    dev, _ = device(tmp_path, board="iphone")
    with dev:
        dev.session.fields[0xA0] = bytes.fromhex("F0C72003000228")
        with pytest.raises(ValueError):
            dev.run(plan(capacity=200000), keep_driver=True, capacity=200000)
        assert not any(p[1] in (0xDB, 0xDA, 0xD2, 0xD3, 0xD4) for p in dev.session.sent)
    dev, _ = device(tmp_path, expected_uid=b"\xaa" * 4)
    with pytest.raises(ValueError, match="UID"):
        with dev:
            pass
    assert dev.reader.closed
    dev, _ = device(tmp_path, board="huawei")
    with dev:
        with pytest.raises(ValueError, match="deadline"):
            dev.run(plan(), timeout=10)
        assert dev.session.sent == [D1]


def test_custom_pin_never_enters_log(tmp_path):
    pin = bytes.fromhex("F1F2F3F4F5")
    dev, _ = device(tmp_path, board="huawei", pin=pin)
    with dev:
        dev.authenticate()
    log = dev.log.path.read_text()
    assert pin.hex().upper() not in log and "PIN REDACTED" in log


def test_recorded_huawei_factory_vertical_scan_can_keep_driver(tmp_path):
    dev, _ = device(tmp_path, board="huawei")
    with dev:
        dev.session.fields[0xA1] = bytes.fromhex("00120030FFFFFF")
        dev.run(plan(), keep_driver=True)
        assert not any(p[1] in (0xDB, 0xDA) for p in dev.session.sent)


def test_iphone_6985_does_not_try_factory_pin(tmp_path):
    dev, _ = device(tmp_path, board="iphone")
    with dev:
        dev.session.authorized = False
        with pytest.raises(RuntimeError, match="6985"):
            dev.authenticate()
        assert not any(p[:2] == b"\x00\x20" for p in dev.session.sent)


@pytest.mark.parametrize("option", [dict(de_hz=0), dict(de_hz=float("nan")), dict(de_hz=100), dict(rf_timeout_ms=1),
                                  dict(packet_delay_ms=-1), dict(board="iphone", pin=FACTORY_PIN),
                                  dict(de_timeout=0), dict(de_timeout=float("nan")), dict(de_timeout=61)])
def test_invalid_timing_and_authentication_options(option):
    with pytest.raises(ValueError):
        Settings(port="SIM", **option)


def test_log_directory_names_do_not_collide_at_same_windows_clock_tick(tmp_path):
    from unittest.mock import patch
    with patch("ink_nfc_fmsc.device.datetime") as timestamp:
        timestamp.now.return_value.strftime.return_value = "fixed_clock_"
        first, second = RunLog(tmp_path), RunLog(tmp_path)
    assert first.directory != second.directory
    assert first.directory.exists() and second.directory.exists()
