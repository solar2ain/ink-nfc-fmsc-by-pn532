import unittest
from unittest.mock import Mock, call, patch, sentinel

from ink_nfc_fmsc.pn532 import (
    ACK,
    FMSC_REQA,
    FMSC_WUPA,
    MAGSAFE_WUPA_CODES,
    WAKE_MODES,
    Pn532,
    Pn532Error,
    Pn532Timeout,
    RfTransportError,
    crc_a,
    encode_frame,
    parse_ats,
    parse_frames,
)


class Pn532FrameTests(unittest.TestCase):
    def test_fmsc_private_short_request_codes(self):
        self.assertEqual(MAGSAFE_WUPA_CODES, (0x7A, 0x7B, 0x7C, 0x7D))
        self.assertEqual(FMSC_REQA, 0x7A)
        self.assertEqual(FMSC_WUPA, 0x7D)

    def test_normal_frame(self):
        frame = encode_frame(bytes.fromhex("D4 02"))
        self.assertEqual(frame, bytes.fromhex("00 00 FF 02 FE D4 02 2A 00"))
        parsed = parse_frames(ACK + encode_frame(bytes.fromhex("D5 03 32 01 06 07")))
        self.assertEqual([item.kind for item in parsed], ["ACK", "DATA"])
        self.assertEqual(parsed[1].data, bytes.fromhex("D5 03 32 01 06 07"))

    def test_extended_frame(self):
        data = bytes(range(250)) + bytes(range(50))
        parsed = parse_frames(encode_frame(data))
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].data, data)

    def test_crc_a_rats(self):
        self.assertEqual(crc_a(bytes.fromhex("E0 80")), bytes.fromhex("31 73"))

    def test_parse_known_ats(self):
        ats = parse_ats(bytes.fromhex("0B 78 33 A0 02 90 0E 68 1E 1E 03"))
        self.assertEqual(ats.fsc, 256)
        self.assertEqual(ats.fwi, 10)
        self.assertAlmostEqual(ats.fwt_ms, 309.3, places=1)


class Pn532ActivationTests(unittest.TestCase):
    def make_reader(self, responses, wake_mode="private"):
        reader = Mock(spec=Pn532)
        reader.wake_request_codes = WAKE_MODES[wake_mode]
        reader.communicate_thru.side_effect = responses
        reader._complete_type_a_activation.return_value = sentinel.activation
        return reader

    @patch("ink_nfc_fmsc.pn532.time.sleep")
    def test_reqa_success_does_not_send_wupa(self, _sleep):
        reader = self.make_reader([b"\x04\x00"])
        self.assertIs(Pn532.activate(reader, off_ms=0, first_wait_ms=0), sentinel.activation)
        reader.communicate_thru.assert_called_once_with(b"\x7a", 100)
        reader._complete_type_a_activation.assert_called_once_with(
            b"\x04\x00", request_code=FMSC_REQA
        )

    @patch("ink_nfc_fmsc.pn532.time.sleep")
    def test_wupa_fallback_keeps_the_current_field(self, _sleep):
        reader = self.make_reader([Pn532Error("RF_TIMEOUT"), b"\x04\x00"])
        self.assertIs(Pn532.activate(reader, off_ms=0, first_wait_ms=0), sentinel.activation)
        self.assertEqual(reader.communicate_thru.call_args_list, [call(b"\x7a", 100), call(b"\x7d", 100)])
        self.assertEqual(reader.set_rf.call_args_list, [call(False), call(True)])
        reader._complete_type_a_activation.assert_called_once_with(
            b"\x04\x00", request_code=FMSC_WUPA
        )

    @patch("ink_nfc_fmsc.pn532.time.sleep")
    def test_failed_codes_are_retried_after_another_field_cycle(self, _sleep):
        reader = self.make_reader([
            Pn532Error("RF_TIMEOUT"), Pn532Error("RF_TIMEOUT"), b"\x04\x00"
        ])
        self.assertIs(Pn532.activate(reader, off_ms=0, first_wait_ms=0), sentinel.activation)
        self.assertEqual(reader.communicate_thru.call_args_list, [
            call(b"\x7a", 100), call(b"\x7d", 100), call(b"\x7a", 100)
        ])

    @patch("ink_nfc_fmsc.pn532.time.sleep")
    def test_total_failure_closes_rf_and_reports_both_codes(self, _sleep):
        reader = self.make_reader([Pn532Error("RF_TIMEOUT")] * 4)
        with self.assertRaises(Pn532Error) as raised:
            Pn532.activate(reader, off_ms=0, first_wait_ms=0)
        self.assertIn("7A/0ms", str(raised.exception))
        self.assertIn("7D/200ms", str(raised.exception))
        self.assertEqual(reader.set_rf.call_args_list[-1], call(False))

    @patch("ink_nfc_fmsc.pn532.time.sleep")
    def test_standard_reqa_uses_seven_bit_without_crc(self, _sleep):
        reader = self.make_reader([b"\x04\x00"], "standard")
        self.assertIs(Pn532.activate(reader, off_ms=0, first_wait_ms=0), sentinel.activation)
        reader.communicate_thru.assert_called_once_with(b"\x26", 100)
        reader.set_framing.assert_called_with(7, False)

    @patch("ink_nfc_fmsc.pn532.time.sleep")
    def test_standard_wupa_fallback_does_not_send_private_frames(self, _sleep):
        reader = self.make_reader([Pn532Error("RF_TIMEOUT"), b"\x04\x00"], "standard")
        Pn532.activate(reader, off_ms=0, first_wait_ms=0)
        self.assertEqual(reader.communicate_thru.call_args_list, [call(b"\x26", 100), call(b"\x52", 100)])
        self.assertEqual(reader.set_rf.call_args_list, [call(False), call(True)])

    @patch("ink_nfc_fmsc.pn532.time.sleep")
    def test_auto_tries_standard_before_private(self, _sleep):
        reader = self.make_reader([
            Pn532Error("RF_TIMEOUT"), Pn532Error("RF_TIMEOUT"), b"\x04\x00"
        ], "auto")
        Pn532.activate(reader, off_ms=0, first_wait_ms=0)
        self.assertEqual(reader.communicate_thru.call_args_list, [
            call(b"\x26", 100), call(b"\x52", 100), call(b"\x7a", 100)
        ])

    def test_in_field_recovery_preserves_standard_wake_mode(self):
        reader = self.make_reader([b"\x04\x00"], "standard")
        reader.rf_on = True
        self.assertIs(Pn532.activate_in_field(reader), sentinel.activation)
        reader.communicate_thru.assert_called_once_with(b"\x26", 100)
        reader.set_rf.assert_not_called()

    def test_in_field_explicit_override_is_preserved(self):
        reader = self.make_reader([b"\x04\x00"], "standard")
        reader.rf_on = True
        Pn532.activate_in_field(reader, request_codes=(FMSC_WUPA,))
        reader.communicate_thru.assert_called_once_with(b"\x7d", 100)

    def test_invalid_mode_is_rejected_without_opening_a_port(self):
        with self.assertRaisesRegex(ValueError, "unknown wake mode"):
            Pn532("TEST", wake_mode="wrong")


class Pn532UidCascadeTests(unittest.TestCase):
    def test_all_standard_uid_lengths_are_selected_before_rats(self):
        cases = (
            ("46 01 02 03", "04 00", ("46 01 02 03 46",)),
            ("46 01 02 03 04 05 06", "44 00", ("88 46 01 02 CD", "03 04 05 06 04")),
            (
                "46 01 02 03 04 05 06 07 08 09",
                "84 00",
                ("88 46 01 02 CD", "88 03 04 05 8A", "06 07 08 09 00"),
            ),
            (
                "46 53 54 4E 31 30 6D 20 64 48",
                "84 00",
                ("88 46 53 54 C9", "88 4E 31 30 C7", "6D 20 64 48 61"),
            ),
        )
        for uid_hex, atqa_hex, block_hexes in cases:
            with self.subTest(uid=uid_hex):
                reader = Mock(spec=Pn532)
                reader.log = Mock()
                responses = []
                expected_calls = []
                expected_framing = []
                for index, block_hex in enumerate(block_hexes):
                    select = (0x93, 0x95, 0x97)[index]
                    block = bytes.fromhex(block_hex)
                    sak = b"\x20" if index == len(block_hexes) - 1 else b"\x04"
                    responses.extend((block, sak))
                    expected_calls.extend((
                        call(bytes((select, 0x20)), 100),
                        call(bytes((select, 0x70)) + block, 100),
                    ))
                    expected_framing.extend((call(8, False), call(8, True)))
                responses.append(bytes.fromhex("02 08"))
                expected_calls.append(call(bytes.fromhex("E0 80"), 100))
                reader.communicate_thru.side_effect = responses

                activation = Pn532._complete_type_a_activation(
                    reader, bytes.fromhex(atqa_hex), request_code=0x26
                )

                self.assertEqual(activation.uid, bytes.fromhex(uid_hex))
                self.assertEqual(activation.atqa, bytes.fromhex(atqa_hex))
                self.assertEqual(activation.sak, 0x20)
                self.assertEqual(activation.ats.raw, bytes.fromhex("02 08"))
                self.assertEqual(activation.wake_command, 0x26)
                self.assertEqual(reader.communicate_thru.call_args_list, expected_calls)
                self.assertEqual(reader.set_framing.call_args_list, expected_framing)
                reader.set_rf.assert_not_called()

    def test_invalid_cascade_bcc_is_rejected_before_select(self):
        reader = Mock(spec=Pn532)
        reader.communicate_thru.return_value = bytes.fromhex("88 46 01 02 CC")

        with self.assertRaisesRegex(Pn532Error, "UID/BCC response is invalid"):
            Pn532._complete_type_a_activation(reader, b"\x84\x00", request_code=0x26)

        reader.communicate_thru.assert_called_once_with(bytes.fromhex("93 20"), 100)

    def test_cascade_on_third_level_is_rejected_without_rats(self):
        reader = Mock(spec=Pn532)
        reader.log = Mock()
        reader.communicate_thru.side_effect = [
            bytes.fromhex("88 46 01 02 CD"), b"\x04",
            bytes.fromhex("88 03 04 05 8A"), b"\x04",
            bytes.fromhex("06 07 08 09 00"), b"\x04",
        ]

        with self.assertRaisesRegex(Pn532Error, "cascade SAK without cascade tag"):
            Pn532._complete_type_a_activation(reader, b"\x84\x00", request_code=0x26)

        self.assertEqual(reader.communicate_thru.call_count, 6)
        reader.log.assert_not_called()


class Pn532TimeoutTests(unittest.TestCase):
    def make_reader(self, timer_code=None):
        reader = Pn532("TEST")
        reader._timer_code = timer_code
        reader.command = Mock(return_value=b"\x00\x90\x00")
        reader.log = Mock()
        return reader

    def test_finite_timeouts_round_up_to_supported_values(self):
        reader = self.make_reader()
        self.assertEqual(reader.set_iso_timeout(1000), 1638.4)
        reader.command.assert_called_with(bytes.fromhex("32 02 00 0B 0F"), 0x33, name="IsoDepTimeout")
        self.assertEqual(reader.set_iso_timeout(3000), 3276.8)
        reader.command.assert_called_with(bytes.fromhex("32 02 00 0B 10"), 0x33, name="IsoDepTimeout")

    def test_long_timeout_disables_hardware_timer_and_reports_watchdog(self):
        reader = self.make_reader()
        self.assertEqual(reader.set_iso_timeout(10000), 10000)
        reader.command.assert_called_once_with(bytes.fromhex("32 02 00 0B 00"), 0x33, name="IsoDepTimeout")
        details = reader.log.call_args.args[1]
        self.assertIsNone(details["hardware_timeout_ms"])
        self.assertTrue(details["host_watchdog"])

    def test_raw_setup_updates_cached_timeout_before_next_exchange(self):
        reader = self.make_reader(timer_code=0x0F)
        reader.write_registers = Mock()
        reader.prepare_type_a()
        self.assertEqual(reader._timer_code, 0x0A)
        reader.set_iso_timeout(1000)
        reader.command.assert_called_with(
            bytes.fromhex("32 02 00 0B 0F"), 0x33, name="IsoDepTimeout"
        )

    def test_long_rf_wait_is_not_replaced_by_whole_command_deadline(self):
        reader = self.make_reader(timer_code=0)
        self.assertEqual(reader.communicate_thru(b"\x02", 10000, host_wait_ms=60000), b"\x90\x00")
        reader.command.assert_called_once_with(b"\x42\x02", 0x43, wait_ms=10700, name="InCommunicateThru")

    def test_long_rf_wait_respects_shorter_remaining_deadline(self):
        reader = self.make_reader(timer_code=0)
        reader.communicate_thru(b"\x02", 10000, host_wait_ms=5000)
        self.assertEqual(reader.command.call_args.kwargs["wait_ms"], 5000)

    def test_finite_rf_wait_keeps_host_silence_guard_short_during_long_refresh(self):
        reader = self.make_reader(timer_code=15)
        reader.communicate_thru(b"\x02", 1500, host_wait_ms=120000)
        self.assertEqual(reader.command.call_args.kwargs["wait_ms"], 2338.4)

    def test_finite_rf_wait_respects_shorter_remaining_deadline(self):
        reader = self.make_reader(timer_code=15)
        reader.communicate_thru(b"\x02", 1500, host_wait_ms=1000)
        self.assertEqual(reader.command.call_args.kwargs["wait_ms"], 1000)

    def test_long_wait_timeout_aborts_without_explicit_rf_off(self):
        reader = self.make_reader(timer_code=0)
        reader.serial = Mock()
        reader.command.side_effect = Pn532Timeout("ACK received but response timed out")
        reader.set_rf = Mock()
        with self.assertRaisesRegex(Pn532Timeout, "host silence watchdog expired after 10700 ms"):
            reader.communicate_thru(b"\x02", 10000, host_wait_ms=60000)
        reader.serial.write.assert_called_once_with(ACK)
        reader.serial.flush.assert_called_once_with()
        reader.set_rf.assert_not_called()

    def test_finite_wait_timeout_does_not_send_abort(self):
        reader = self.make_reader(timer_code=15)
        reader.serial = Mock()
        reader.command.side_effect = Pn532Timeout("response timed out")
        with self.assertRaisesRegex(Pn532Timeout, "response timed out"):
            reader.communicate_thru(b"\x02", 1000, host_wait_ms=60000)
        reader.serial.write.assert_not_called()

    def test_long_wait_does_not_abort_twice_when_command_already_cleaned_up(self):
        reader = self.make_reader(timer_code=0)
        reader.serial = Mock()
        reader.command.side_effect = Pn532Timeout("UART response timed out", command_aborted=True)
        with self.assertRaisesRegex(Pn532Timeout, "host silence watchdog") as raised:
            reader.communicate_thru(b"\x02", 10000, host_wait_ms=60000)
        self.assertTrue(raised.exception.command_aborted)
        reader.serial.write.assert_not_called()


class Pn532CommandCleanupTests(unittest.TestCase):
    def reader(self):
        reader = Pn532("TEST")
        reader.serial = Mock(is_open=True)
        reader.serial.in_waiting = 0
        reader.log = Mock()
        return reader

    @patch("ink_nfc_fmsc.pn532.time.sleep")
    @patch("ink_nfc_fmsc.pn532.time.monotonic", side_effect=[0, 0.1])
    def test_optional_host_ack_follows_a_complete_valid_response_without_resend(self, _clock, _sleep):
        reader = self.reader()
        reader.host_response_ack = True
        response = ACK + encode_frame(bytes.fromhex("D5 43 00 F2 01"))
        reader._drain = Mock()
        reader.serial.in_waiting = len(response)
        reader.serial.read.return_value = response
        command = bytes.fromhex("42 02 F0 DC 00 00 00")
        self.assertEqual(reader.command(command, 0x43), bytes.fromhex("00 F2 01"))
        self.assertEqual(reader.serial.write.call_args_list, [call(encode_frame(b"\xd4" + command)), call(ACK)])
        reader.log.assert_any_call("pn532_host_response_ack", {"hex": "00 00 FF 00 FF 00", "apdu_retry": False})

    def test_acknowledge_response_rejects_closed_port_without_write(self):
        reader = self.reader()
        reader.serial.is_open = False
        with self.assertRaises(Pn532Error):
            reader.acknowledge_response()
        reader.serial.write.assert_not_called()

    @patch("ink_nfc_fmsc.pn532.time.sleep")
    @patch("ink_nfc_fmsc.pn532.time.monotonic", side_effect=[0, 2])
    def test_missing_ack_still_aborts_without_resending_command(self, _clock, _sleep):
        reader = self.reader()
        command = bytes.fromhex("42 F2 01")
        with self.assertRaisesRegex(Pn532Timeout, "no UART ACK") as raised:
            reader.command(command, 0x43, wait_ms=1000, name="InCommunicateThru")
        self.assertTrue(raised.exception.command_aborted)
        self.assertEqual(reader.serial.write.call_args_list, [call(encode_frame(b"\xd4" + command)), call(ACK)])
        reader.log.assert_any_call("pn532_timeout_abort", {"name": "InCommunicateThru",
            "ack_received": False, "apdu_retry": False, "rf_reenabled": False})

    @patch("ink_nfc_fmsc.pn532.time.sleep")
    @patch("ink_nfc_fmsc.pn532.time.monotonic", side_effect=[0, 2])
    def test_cleanup_error_preserves_original_uart_failure(self, _clock, _sleep):
        reader = self.reader()
        reader._abort_pending = Mock(side_effect=OSError("USB disconnected"))
        with self.assertRaisesRegex(Pn532Timeout, "no UART ACK") as raised:
            reader.command(bytes.fromhex("42 F2 01"), 0x43, wait_ms=1000)
        self.assertFalse(raised.exception.command_aborted)
        reader._abort_pending.assert_called_once_with()

    @patch("ink_nfc_fmsc.pn532.time.sleep")
    @patch("ink_nfc_fmsc.pn532.time.monotonic", side_effect=[0, 0.1, 2])
    def test_ack_only_timeout_aborts_once(self, _clock, _sleep):
        reader = self.reader()
        reader._drain = Mock()
        reader.serial.in_waiting = len(ACK)
        reader.serial.read.return_value = ACK
        with self.assertRaisesRegex(Pn532Timeout, "ACK received") as raised:
            reader.command(bytes.fromhex("06 63 04"), 0x07, wait_ms=1000, name="ReadRegister")
        self.assertTrue(raised.exception.command_aborted)
        self.assertEqual(reader.serial.write.call_args_list[-1], call(ACK))
        self.assertEqual(reader.serial.write.call_count, 2)

    @patch("ink_nfc_fmsc.pn532.time.sleep")
    @patch("ink_nfc_fmsc.pn532.time.monotonic", side_effect=[0, 0.1])
    def test_valid_response_without_ack_is_still_accepted(self, _clock, _sleep):
        reader = self.reader()
        response = encode_frame(bytes.fromhex("D5 07 83"))
        reader._drain = Mock()
        reader.serial.in_waiting = len(response)
        reader.serial.read.return_value = response
        self.assertEqual(reader.command(bytes.fromhex("06 63 04"), 0x07), b"\x83")
        self.assertEqual(reader.serial.write.call_count, 1)

    @patch("ink_nfc_fmsc.pn532.time.sleep")
    @patch("ink_nfc_fmsc.pn532.time.monotonic", side_effect=[0, 0.1, 2])
    def test_late_wtx_is_not_decoded_as_register_value(self, _clock, _sleep):
        reader = self.reader()
        response = ACK + encode_frame(bytes.fromhex("D5 43 00 F2 01"))
        reader._drain = Mock()
        reader.serial.in_waiting = len(response)
        reader.serial.read.return_value = response
        with self.assertRaisesRegex(Pn532Timeout, "ACK received"):
            reader.command(bytes.fromhex("06 63 04"), 0x07, wait_ms=1000, name="ReadRegister")
        self.assertEqual(reader.serial.write.call_args_list[-1], call(ACK))

    @patch("ink_nfc_fmsc.pn532.time.sleep")
    @patch("ink_nfc_fmsc.pn532.time.monotonic", side_effect=[0, 2, 3, 3.1])
    def test_abort_drains_late_wtx_before_the_next_register_command(self, _clock, _sleep):
        register_command = encode_frame(bytes.fromhex("D4 06 63 04"))

        class ScriptedSerial:
            is_open = True

            def __init__(self):
                self.buffer = bytearray()
                self.writes = []

            @property
            def in_waiting(self):
                return len(self.buffer)

            def write(self, data):
                self.writes.append(data)
                if data == ACK:
                    self.buffer.extend(encode_frame(bytes.fromhex("D5 43 00 F2 01")))
                elif data == register_command:
                    self.buffer.extend(ACK + encode_frame(bytes.fromhex("D5 07 83")))

            def flush(self):
                pass

            def read(self, size):
                data = bytes(self.buffer[:size])
                del self.buffer[:size]
                return data

        reader = Pn532("TEST")
        reader.serial = ScriptedSerial()
        reader.log = Mock()
        with self.assertRaisesRegex(Pn532Timeout, "no UART ACK"):
            reader.command(bytes.fromhex("42 F2 01"), 0x43, wait_ms=1000)
        self.assertEqual(reader.read_registers((0x6304,)), {0x6304: 0x83})
        self.assertEqual(reader.serial.writes, [encode_frame(bytes.fromhex("D4 42 F2 01")), ACK, register_command])
        self.assertEqual(reader.serial.buffer, bytearray())

    @patch("ink_nfc_fmsc.pn532.time.sleep")
    @patch("ink_nfc_fmsc.pn532.time.monotonic", side_effect=[0, 0.1])
    def test_valid_rf_timeout_without_uart_ack_is_not_aborted_or_retried(self, _clock, _sleep):
        reader = self.reader()
        reader._timer_code = 15
        reader._drain = Mock()
        response = encode_frame(bytes.fromhex("D5 43 01"))
        reader.serial.in_waiting = len(response)
        reader.serial.read.return_value = response
        with self.assertRaises(RfTransportError) as raised:
            reader.communicate_thru(b"\xf2\x01", 1500)
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(reader.serial.write.call_count, 1)


if __name__ == "__main__":
    unittest.main()
