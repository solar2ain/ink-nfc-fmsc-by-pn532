import json

import pytest

from ink_nfc_fmsc.cli import main
from ink_nfc_fmsc.device import Device, RunLog
from ink_nfc_fmsc.pn532 import Pn532Timeout, RfTransportError
from ink_nfc_fmsc.user_messages import failure_description, stage_label


def test_rf_timeout_and_low_voltage_are_distinct_and_keep_original_codes():
    missing = failure_description(RfTransportError(1, "RF transport failed: RF_TIMEOUT"))
    voltage = failure_description(RuntimeError("DE: SW=68CA; stopped"))
    assert missing["code"] == "RF=01 (RF_TIMEOUT)"
    assert "不能直接判定" in missing["advice"]
    assert voltage["code"] == "SW=68CA" and "低电压" in voltage["message"]
    assert failure_description(Pn532Timeout("no valid PN532 response"))["code"] == "READER_TIMEOUT"


def test_compression_capability_failure_explains_explicit_raw_option():
    failure = failure_description(ValueError("D1 does not declare D3 support; no configuration written"))
    assert failure["code"] == "COMPRESSION_UNSUPPORTED"
    assert "--no-compress" in failure["advice"] and "自动" in failure["advice"]


@pytest.mark.parametrize("stage,label", [("full D2 11/120", "传输图片"), ("full D4", "启动屏幕刷新"),
    ("half-1 DE", "等待屏幕刷新"), ("PIN", "检查访问权限"), ("D1 config", "设置或核对屏幕配置")])
def test_readable_failure_stages(stage, label):
    assert stage_label(stage) == label


def test_readable_log_and_summary_keep_details_separate(tmp_path):
    log = RunLog(tmp_path)
    log.message("传图 100%（SW=9000）。")
    log.event("apdu", {"tx": "F0D4858000", "sw": "9000"})
    log.finish("reported_idle", uid="9E56721E", d4_sw="9000", de_data="00")
    assert "100%" in log.text_path.read_text(encoding="utf-8")
    assert "F0D4" not in log.text_path.read_text(encoding="utf-8")
    summary = json.loads((log.directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "reported_idle" and not summary["optically_verified"]
    assert log.path.parent == log.details


def test_product_output_keeps_codes_without_hex_dump_and_failure_is_actionable(tmp_path, capsys):
    from unittest.mock import patch
    from test_device import Clock, FakeReader, FakeSession
    clock = Clock()
    instances = []

    def factory(settings, logger, **kwargs):
        d = Device(settings, logger, reader_factory=FakeReader, session_factory=FakeSession,
                   clock=clock.time, sleep=clock.sleep, **kwargs)
        instances.append(d)
        return d

    args = ["clear", "--screen", "huawei-a0", "--board", "huawei", "--port", "SIM", "--yes", "--log-dir", str(tmp_path)]
    with patch("ink_nfc_fmsc.cli.Device", factory):
        assert main(args) == 0
    output = capsys.readouterr().out
    assert "传图 100%" in output and "D4=9000" in output and "DE=00" in output
    assert "SHA256=" not in output and "APPID" not in output and "DATA=" not in output
    assert "驱动板报告" in output
    assert "华为Nova墨水屏手机壳NFC驱动板" in output
    assert "[INFO] [连接]" in output and "[INFO] [配置]" in output
    assert "[INFO] [传图]" in output and "[SUCCESS] [结果]" in output
    assert "\x1b" not in output
    log = instances[0].log
    summary = json.loads((log.directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "reported_idle" and summary["uid"] == "01020304"
    assert summary["board_name"] == "华为Nova墨水屏手机壳NFC驱动板"
    assert summary["screen"] == "JD79665AA-552x768-BWRY-A0"
    assert (log.directory / "preview.png").exists()
    assert (log.details / "plan.json").exists()

    original = FakeSession.transceive

    def fail(self, apdu, **kwargs):
        if apdu[1] == 0xDE:
            raise RfTransportError(1, "RF_TIMEOUT")
        return original(self, apdu, **kwargs)

    with patch("ink_nfc_fmsc.cli.Device", factory), patch.object(FakeSession, "transceive", fail):
        assert main(args) == 1
    error = capsys.readouterr().err
    assert "RF=01 (RF_TIMEOUT)" in error and "建议" in error
    assert "[ERROR] [结果] 失败位置：等待屏幕刷新" in error
    assert "[WARN]" in error and "\x1b" not in error
    assert instances[-1].reader.closed
    failed = json.loads((instances[-1].log.directory / "summary.json").read_text(encoding="utf-8"))
    assert failed["status"] == "failed" and failed["failure_stage"] == "等待屏幕刷新"
