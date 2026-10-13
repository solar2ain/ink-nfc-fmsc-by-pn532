import random

import lzokay
import pytest

from ink_nfc_fmsc.catalog import screens, select_screen
from ink_nfc_fmsc.images import prepare_image
from ink_nfc_fmsc.plans import jobs_for, split_half
from ink_nfc_fmsc.protocol import image_packets, literal_lzo, reply


def restore(packets, lengths=None):
    planes = {}
    blocks = {}
    for packet in packets:
        assert packet[4] == len(packet) - 5
        slot, block, sub = packet[2], packet[5], packet[6]
        stream = blocks.setdefault((slot, block), bytearray())
        assert len(stream) == sub * 250
        stream.extend(packet[7:])
        if packet[3]:
            expected = min(2000, lengths[slot] - block * 2000) if lengths is not None else 2000
            raw = lzokay.decompress(bytes(stream), expected)
            planes.setdefault(slot, bytearray()).extend(raw)
    return tuple(bytes(planes[i]) for i in sorted(planes))


@pytest.mark.parametrize("alias", ["huawei-a0", "huawei-a1"])
@pytest.mark.parametrize("compression", ["lzo", "literal"])
def test_two_pass_row_slicing_reuses_slot_and_preserves_native_init(alias, compression):
    s = select_screen(alias)
    raw = random.Random(71).randbytes(105984)
    jobs = jobs_for(s, (raw,), capacity=80000, compression=compression)
    assert len(jobs) == 2
    for i, job in enumerate(jobs):
        assert job.raw_bytes == 52992
        assert {p[2] for p in job.packets} == {0}
        assert max(p[5] for p in job.packets) == 26
        assert restore(job.packets, (52992,)) == (split_half(raw, i),)
        assert len(restore(job.packets, (52992,))[0]) == 26 * 2000 + 992
        flow = job.config[1][5:]
        assert bytes.fromhex("6103000228") in flow
        assert bytes.fromhex("1201" if alias == "huawei-a0" else "1200") in flow
    assert jobs_for(s, (raw,), capacity=80000, passes="first")[0].half == 0
    assert jobs_for(s, (raw,), capacity=80000, passes="second")[0].half == 1
    with pytest.raises(ValueError):
        jobs_for(s, (raw,), capacity=80000, passes="full")


@pytest.mark.parametrize("screen", [s for s in screens() if s.supported], ids=lambda s: s.id)
def test_all_sendable_recipes_plan_without_external_sources(screen):
    planes, _ = prepare_image(screen, white=True)
    jobs = jobs_for(screen, planes, capacity=512000, passes="full")
    assert len(jobs) == 1 and jobs[0].raw_bytes == screen.value["raw_bytes"]
    assert all(p[1] == 0xD3 for p in jobs[0].packets)
    assert restore(jobs[0].packets, tuple(map(len, planes))) == planes


def test_hanshow_compressed_native_frame_is_identical_to_d2():
    s = select_screen("JD79668AA-400x300-BWRY")
    raw = random.Random(11).randbytes(30000)
    d2 = jobs_for(s, (raw,), transport="d2")[0]
    d3 = jobs_for(s, (raw,), transport="d3")[0]
    assert len(d2.packets) == 120
    assert b"".join(p[5:] for p in d2.packets) == raw
    assert restore(d3.packets, (30000,)) == (raw,)
    assert d2.config == d3.config and d2.d4 == d3.d4


def test_d3_per_plane_slot_stride_and_40_block_limit():
    raw = b"\x55" * 80000
    packets = image_packets((raw,), "d3")
    assert len(packets) == 40 and max(p[5] for p in packets) == 39
    dual = image_packets((b"\xff" * 15000, b"\x00" * 15000), "d3")
    assert restore(dual, (15000, 15000)) == (b"\xff" * 15000, b"\x00" * 15000)
    assert {p[2] for p in dual} == {0, 1}


@pytest.mark.parametrize("length", [1, 18, 238, 239, 992, 2000])
def test_literal_lzo_roundtrip(length):
    raw = bytes(i % 256 for i in range(length))
    assert lzokay.decompress(literal_lzo(raw), length) == raw


def test_unsupported_arbitrary_multi_refresh_or_packet_index_overflow_is_blocked():
    s = select_screen("osptek-e6-370")
    planes, _ = prepare_image(s, white=True)
    with pytest.raises(ValueError, match="multi-refresh"):
        jobs_for(s, planes, capacity=80000)
    with pytest.raises(ValueError):
        image_packets((b"\x00" * 64001,), "d2")
    with pytest.raises(ValueError):
        image_packets((b"\x00" * 512001,), "d3")
    with pytest.raises(ValueError):
        jobs_for(select_screen("huawei-a0"), (b"\x55" * 105984,), capacity=80000, transport="d2")


def test_original_sw_and_undocumented_status_not_confused():
    assert "low voltage" in reply(bytes.fromhex("68CA")).meaning
    assert "PIN" in reply(bytes.fromhex("6502")).meaning
    assert "undocumented" in reply(bytes.fromhex("6401")).meaning
    assert "unknown" in reply(bytes.fromhex("64A5")).meaning
