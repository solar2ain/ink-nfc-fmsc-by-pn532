import unittest

from ink_nfc_fmsc.isodep import IsoDepSession
from ink_nfc_fmsc.pn532 import Activation, AtsInfo, Pn532Error, RfTransportError, encode_frame


class FakePn532:
    def __init__(self, responses):
        self.responses = list(responses)
        self.waits = []
        self.sent = []
        self.events = []
        self.log = lambda kind, details: self.events.append((kind, details))

    def communicate_thru(self, block, requested_wait_ms, *, host_wait_ms=None):
        self.waits.append(requested_wait_ms)
        self.sent.append(block)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def activation(fwt_ms=309.3):
    ats = AtsInfo(b"\x02\x08", 256, 10, 0, fwt_ms, None, None, None, b"")
    return Activation(b"\x01\x02\x03\x04", b"\x04\x00", 0x28, ats, 0x7A)


class IsoDepTests(unittest.TestCase):
    def test_opt_in_frame_pacing_preserves_blocks_waits_and_wtx_multiplier(self):
        from unittest.mock import patch
        fake = FakePn532([b"\xf2\x01", b"\x02\x90\x00"])
        session = IsoDepSession(fake, activation(77.3))
        with patch("ink_nfc_fmsc.isodep.time.sleep") as sleep:
            result = session.transceive(bytes.fromhex("F0DC000000"), frame_gap_ms=3)
        self.assertEqual(result, b"\x90\x00")
        self.assertEqual(fake.sent, [bytes.fromhex("02F0DC000000"), b"\xf2\x01"])
        self.assertEqual([call.args for call in sleep.call_args_list], [(0.003,), (0.003,)])
        self.assertEqual(fake.events[0][0], "iso_frame_pacing")
        self.assertFalse(fake.events[0][1]["apdu_resent"])

    def test_invalid_or_fwt_unsafe_gap_fails_before_radio(self):
        for gap, fwt in ((-1, 77), (6, 77), (3, 1), (float("nan"), 77)):
            with self.subTest(gap=gap, fwt=fwt):
                fake = FakePn532([])
                session = IsoDepSession(fake, activation(fwt))
                with self.assertRaises(ValueError):
                    session.transceive(bytes.fromhex("00D1000000"), frame_gap_ms=gap)
                self.assertEqual(fake.sent, [])
                self.assertTrue(session.active)

    def test_default_transceive_does_not_sleep_or_emit_pacing(self):
        from unittest.mock import patch
        fake = FakePn532([b"\x02\x90\x00"])
        with patch("ink_nfc_fmsc.isodep.time.sleep") as sleep:
            IsoDepSession(fake, activation()).transceive(bytes.fromhex("00D1000000"))
        sleep.assert_not_called()
        self.assertEqual(fake.events, [])

    def test_consecutive_wtx_updates_each_wait(self):
        fake = FakePn532((bytes.fromhex("F2 02"), bytes.fromhex("F2 03"), bytes.fromhex("02 90 00")))
        session = IsoDepSession(fake, activation(100.0))
        self.assertEqual(session.transceive(bytes.fromhex("00 D1 00 00 00")), bytes.fromhex("90 00"))
        for actual, expected in zip(fake.waits, (130.0, 240.0, 350.0), strict=True):
            self.assertAlmostEqual(actual, expected)

    def test_minimum_link_wait(self):
        fake = FakePn532((bytes.fromhex("02 90 00"),))
        session = IsoDepSession(fake, activation())
        session.transceive(bytes.fromhex("00 D1 00 00 00"), minimum_wait_ms=10_000)
        self.assertEqual(fake.waits, [10_000])

    def test_minimum_link_wait_survives_wtx(self):
        fake = FakePn532((bytes.fromhex("F2 01"), bytes.fromhex("02 90 00")))
        session = IsoDepSession(fake, activation())
        session.transceive(bytes.fromhex("F0 DC 00 00 00"), minimum_wait_ms=10_000)
        self.assertEqual(fake.waits, [10_000, 10_000])

    def test_caller_can_allow_more_than_128_long_command_exchanges(self):
        fake = FakePn532([bytes.fromhex("F2 01")] * 129 + [bytes.fromhex("02 90 00")])
        session = IsoDepSession(fake, activation(1.0))
        response = session.transceive(
            bytes.fromhex("F0 DC 3F 00 04 A5 02 00 28"),
            max_wtx=160,
        )
        self.assertEqual(response, bytes.fromhex("90 00"))
        self.assertEqual(len(fake.waits), 130)

    def test_failed_exchange_can_probe_last_wtx_without_reactivation(self):
        fake = FakePn532((bytes.fromhex("F2 01"), RuntimeError("link lost"), bytes.fromhex("F2 01")))

        def communicate(block, requested_wait_ms, *, host_wait_ms=None):
            response = fake.responses.pop(0)
            fake.waits.append(requested_wait_ms)
            if isinstance(response, Exception):
                raise response
            return response

        fake.communicate_thru = communicate
        session = IsoDepSession(fake, activation())
        with self.assertRaises(RuntimeError):
            session.transceive(bytes.fromhex("F0 D4 85 80 00"))
        block = session.probe_pending_exchange(wait_ms=500)
        self.assertEqual(block.kind, "WTX")
        self.assertEqual(block.raw, bytes.fromhex("F2 01"))
        self.assertEqual(fake.waits[-1], 500)

    def test_deselect_abandons_failed_exchange_without_resending_apdu(self):
        fake = FakePn532([RfTransportError(1, "RF_TIMEOUT"), b"\xc2"])
        session = IsoDepSession(fake, activation())
        with self.assertRaises(RfTransportError):
            session.transceive(bytes.fromhex("F0DE000001"))
        session.deselect_in_field(wait_ms=500, host_wait_ms=2000)
        self.assertEqual(fake.sent, [bytes.fromhex("02F0DE000001"), b"\xc2"])
        self.assertFalse(session.active)
        self.assertFalse(fake.events[-1][1]["rf_cycled"])

    def test_deselect_preserves_cid_and_never_accepts_old_status_as_deselect(self):
        from dataclasses import replace
        for response in (b"\xca\x00", b"\x0a\x00\x00\x90\x00"):
            with self.subTest(response=response):
                fake = FakePn532([response])
                active = activation()
                session = IsoDepSession(fake, replace(active, ats=replace(active.ats, tc=2)))
                session.use_cid = True
                if response == b"\xca\x00":
                    session.deselect_in_field()
                else:
                    with self.assertRaisesRegex(Pn532Error, "S\\(DESELECT\\)"):
                        session.deselect_in_field()
                self.assertEqual(fake.sent, [b"\xca\x00"])
                self.assertFalse(session.active)

    def test_255_byte_d2_is_chained_without_changing_apdu(self):
        apdu = bytes.fromhex("F0 D2 00 00 FA") + bytes(range(250))
        fake = FakePn532([bytes.fromhex("A2"), bytes.fromhex("03 90 00"), bytes.fromhex("02 90 00")])
        session = IsoDepSession(fake, activation())
        self.assertEqual(session.transceive(apdu), bytes.fromhex("90 00"))
        self.assertEqual(fake.sent[0], b"\x12" + apdu[:253])
        self.assertEqual(fake.sent[1], b"\x03" + apdu[253:])
        self.assertEqual(session.block_number, 0)
        session.transceive(bytes.fromhex("00 D1 00 00 00"))
        self.assertEqual(fake.sent[2][0], 0x02)

    def test_pcr532_limit_keeps_d2_and_d3_uart_frames_below_256_bytes(self):
        for opcode in (0xD2, 0xD3):
            with self.subTest(opcode=opcode):
                apdu = bytes((0xF0, opcode, 0, 0, 250)) + bytes(range(250))
                fake = FakePn532([b"\xa2", b"\x03\x90\x00"])
                session = IsoDepSession(fake, activation(), max_inf_bytes=240)
                self.assertEqual(session.transceive(apdu), b"\x90\x00")
                self.assertEqual(fake.sent, [b"\x12" + apdu[:240], b"\x03" + apdu[240:]])
                wires = [encode_frame(b"\xd4\x42" + block) for block in fake.sent]
                self.assertEqual(len(wires[0]), 250)
                self.assertTrue(all(len(wire) <= 255 and wire[:5] != b"\x00\x00\xff\xff\xff"
                                    for wire in wires))

    def test_pcr532_limit_respects_smaller_fsc_and_cid_adoption(self):
        from dataclasses import replace

        active = activation()
        for fsc in (16, 256):
            with self.subTest(fsc=fsc):
                active = replace(active, ats=replace(active.ats, fsc=fsc, tc=2))
                limit = 8
                apdu = bytes(range(24))
                fake = FakePn532([b"\xaa\x00", b"\xab\x00", b"\x0a\x00\x90\x00"])
                session = IsoDepSession(fake, active, max_inf_bytes=limit)
                self.assertEqual(session.transceive(apdu), b"\x90\x00")
                self.assertEqual(fake.sent[0][1:] + b"".join(block[2:] for block in fake.sent[1:]), apdu)
                self.assertTrue(all(len(block) + 2 <= fsc for block in fake.sent))

    def test_invalid_inf_limit_fails_before_exchange(self):
        for limit in (0, -1, 254):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                IsoDepSession(FakePn532([]), activation(), max_inf_bytes=limit)

    def test_chained_command_then_chained_response(self):
        apdu = bytes(range(256)) + b"\x01"
        fake = FakePn532([b"\xa2", b"\x13\x11", b"\x02\x90\x00"])
        session = IsoDepSession(fake, activation())
        self.assertEqual(session.transceive(apdu), b"\x11\x90\x00")
        self.assertEqual(fake.sent[2], b"\xa2")
        self.assertEqual(session.block_number, 1)

    def test_wtx_can_precede_chained_command_ack(self):
        fake = FakePn532([b"\xf2\x02", b"\xa2", b"\xf2\x01", b"\x03\x90\x00"])
        session = IsoDepSession(fake, activation())
        session.transceive(bytes(255))
        self.assertEqual([frame[0] for frame in fake.sent], [0x12, 0xF2, 0x03, 0xF2])

    def test_wrong_ack_or_nak_stops_without_retry(self):
        for response in (b"\xa3", b"\xb2", b"\x02\x90\x00"):
            with self.subTest(response=response):
                fake = FakePn532([response])
                session = IsoDepSession(fake, activation())
                with self.assertRaises(Pn532Error):
                    session.transceive(bytes(255))
                self.assertFalse(session.active)
                self.assertEqual(len(fake.sent), 1)

    def test_small_fsc_chain_and_block_number_alternation(self):
        active = activation()
        from dataclasses import replace
        active = replace(active, ats=replace(active.ats, fsc=16))
        apdu = bytes(range(40))
        fake = FakePn532([b"\xa2", b"\xa3", b"\xa2", b"\x03\x90\x00"])
        session = IsoDepSession(fake, active)
        session.transceive(apdu)
        self.assertEqual([frame[0] for frame in fake.sent], [0x12, 0x13, 0x12, 0x03])
        self.assertEqual(b"".join(frame[1:] for frame in fake.sent), apdu)
        self.assertTrue(all(len(frame) + 2 <= 16 for frame in fake.sent))

    def test_cid_adoption_during_command_chaining(self):
        from dataclasses import replace
        active = activation()
        active = replace(active, ats=replace(active.ats, tc=2))
        apdu = bytes(506)
        fake = FakePn532([b"\xaa\x00", b"\xab\x00", b"\x0a\x00\x90\x00"])
        session = IsoDepSession(fake, active)
        session.transceive(apdu)
        self.assertEqual(len(fake.sent[0]), 254)
        self.assertEqual(fake.sent[1][:2], b"\x1b\x00")
        self.assertEqual(fake.sent[2][:2], b"\x0a\x00")
        self.assertEqual(fake.sent[0][1:] + b"".join(frame[2:] for frame in fake.sent[1:]), apdu)
        self.assertTrue(all(len(frame) + 2 <= 256 for frame in fake.sent))

    def test_opt_in_response_recovery_never_resends_apdu(self):
        apdu = bytes.fromhex("F0D4858000")
        fake = FakePn532([RfTransportError(2, "CRC"), b"\xf2\x01", b"\x02\x90\x00"])
        session = IsoDepSession(fake, activation())
        self.assertEqual(session.transceive(apdu, recover_responses=1), b"\x90\x00")
        self.assertEqual(fake.sent, [b"\x02" + apdu, b"\xb2", b"\xf2\x01"])
        self.assertEqual(fake.events[0][1]["apdu_resent"], False)

    def test_same_session_de_retry_preserves_block_number_and_allows_next_de(self):
        apdu = bytes.fromhex("F0DE000001")
        fake = FakePn532([RfTransportError(1, "RF_TIMEOUT"), b"\x02\x01\x90\x00", b"\x03\x00\x90\x00"])
        session = IsoDepSession(fake, activation())
        self.assertEqual(session.transceive(apdu, retry_de_timeouts=3), b"\x01\x90\x00")
        self.assertTrue(session.active)
        self.assertEqual(session.block_number, 1)
        self.assertEqual(session.transceive(apdu), b"\x00\x90\x00")
        self.assertEqual(fake.sent, [b"\x02" + apdu, b"\x02" + apdu, b"\x03" + apdu])
        self.assertEqual(fake.events[0][0], "iso_de_timeout_retry")
        self.assertTrue(fake.events[0][1]["cached_reply_possible"])
        self.assertFalse(fake.events[0][1]["session_reselected"])

    def test_same_session_retry_after_wtx_preserves_adopted_cid(self):
        from dataclasses import replace
        active = activation()
        active = replace(active, ats=replace(active.ats, tc=2))
        apdu = bytes.fromhex("F0DE000001")
        fake = FakePn532([b"\xfa\x00\x02", RfTransportError(1, "RF_TIMEOUT"),
                          b"\x0a\x00\x01\x90\x00", b"\x0b\x00\x00\x90\x00"])
        session = IsoDepSession(fake, active)
        self.assertEqual(session.transceive(apdu, retry_de_timeouts=1), b"\x01\x90\x00")
        self.assertEqual(session.transceive(apdu), b"\x00\x90\x00")
        self.assertEqual(fake.sent, [b"\x02" + apdu, b"\xfa\x00\x02",
                                     b"\x0a\x00" + apdu, b"\x0b\x00" + apdu])

    def test_same_session_retry_is_read_only_opt_in_and_bounded(self):
        for apdu in (bytes.fromhex("F0D4858000"), bytes.fromhex("00D1000000"), bytes.fromhex("F0DE000002")):
            with self.subTest(apdu=apdu):
                fake = FakePn532([])
                session = IsoDepSession(fake, activation())
                with self.assertRaises(ValueError):
                    session.transceive(apdu, retry_de_timeouts=3)
                self.assertEqual(fake.sent, [])
        for limit in (0, 1, 3, 10):
            with self.subTest(limit=limit):
                fake = FakePn532([RfTransportError(1, "RF_TIMEOUT")] * (limit + 1))
                session = IsoDepSession(fake, activation())
                apdu = bytes.fromhex("F0DE000001")
                with self.assertRaises(RfTransportError):
                    session.transceive(apdu, retry_de_timeouts=limit)
                self.assertEqual(fake.sent, [b"\x02" + apdu] * (limit + 1))
                self.assertFalse(session.active)

    def test_same_session_retry_never_retries_other_faults_or_partial_chains(self):
        from ink_nfc_fmsc.pn532 import Pn532Timeout
        apdu = bytes.fromhex("F0DE000001")
        for failure in (RfTransportError(2, "CRC"), Pn532Timeout("no UART ACK")):
            with self.subTest(failure=failure):
                fake = FakePn532([failure])
                with self.assertRaises(Pn532Error):
                    IsoDepSession(fake, activation()).transceive(apdu, retry_de_timeouts=3)
                self.assertEqual(fake.sent, [b"\x02" + apdu])
        fake = FakePn532([RfTransportError(1, "RF_TIMEOUT")])
        with self.assertRaises(RfTransportError):
            IsoDepSession(fake, activation(), max_inf_bytes=1).transceive(apdu, retry_de_timeouts=3)
        self.assertEqual(len(fake.sent), 1)
        fake = FakePn532([b"\x12\x01", RfTransportError(1, "RF_TIMEOUT")])
        with self.assertRaises(RfTransportError):
            IsoDepSession(fake, activation()).transceive(apdu, retry_de_timeouts=3)
        self.assertEqual(fake.sent, [b"\x02" + apdu, b"\xa3"])

    def test_same_session_retry_does_not_extend_deadline(self):
        from unittest.mock import patch
        apdu = bytes.fromhex("F0DE000001")
        fake = FakePn532([RfTransportError(1, "RF_TIMEOUT")])
        with patch("ink_nfc_fmsc.isodep.time.monotonic", side_effect=[0, 0, 0, 0, 2]):
            with self.assertRaisesRegex(Pn532Error, "deadline"):
                IsoDepSession(fake, activation()).transceive(apdu, timeout_s=1, retry_de_timeouts=3)
        self.assertEqual(fake.sent, [b"\x02" + apdu])

    def test_response_recovery_is_off_by_default_and_bounded(self):
        for limit in (0, 1, 3):
            with self.subTest(limit=limit):
                fake = FakePn532([RfTransportError(1, "timeout")] * (limit + 1))
                session = IsoDepSession(fake, activation())
                with self.assertRaises(RfTransportError):
                    session.transceive(bytes.fromhex("F0D4858000"), recover_responses=limit)
                self.assertEqual(fake.sent[1:], [b"\xb2"] * limit)
                self.assertFalse(session.active)

    def test_command_chain_failure_does_not_send_response_recovery(self):
        fake = FakePn532([RfTransportError(2, "CRC")])
        session = IsoDepSession(fake, activation())
        with self.assertRaises(RfTransportError):
            session.transceive(bytes(255), recover_responses=3)
        self.assertEqual(len(fake.sent), 1)

    def test_recovery_ack_does_not_trigger_command_resend(self):
        fake = FakePn532([RfTransportError(1, "timeout"), b"\xa3"])
        session = IsoDepSession(fake, activation())
        with self.assertRaises(Pn532Error):
            session.transceive(bytes.fromhex("F0D4858000"), recover_responses=1)
        self.assertEqual(fake.sent[-1], b"\xb2")
        self.assertEqual(len(fake.sent), 2)


if __name__ == "__main__":
    unittest.main()
