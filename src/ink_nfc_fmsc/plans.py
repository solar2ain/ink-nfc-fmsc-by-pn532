"""Build immutable per-refresh jobs; compression never changes storage capacity."""
import hashlib
from dataclasses import dataclass

from .catalog import Screen, asset_json, driver_tags
from .protocol import DB_CLEAR, image_packets


@dataclass(frozen=True)
class Job:
    name: str
    config: tuple[bytes, ...]
    geometry: bytes
    scan: bytes
    planes: tuple[bytes, ...]
    packets: tuple[bytes, ...]
    d4: bytes
    timeout: float
    minimum_observation: float
    half: int | None = None

    @property
    def raw_bytes(self):
        return sum(map(len, self.planes))

    @property
    def sha256(self):
        return hashlib.sha256(b"".join(self.planes)).hexdigest()


def split_half(raw, half):
    if len(raw) != 105984 or half not in (0, 1):
        raise ValueError("Source splitting only supports the reviewed 768x552 2-bpp frames")
    return b"".join(raw[row * 192 + half * 96:row * 192 + half * 96 + 96] for row in range(552))


def jobs_for(screen: Screen, planes, *, capacity=200000, passes="auto", transport="d3", compression="lzo"):
    screen.require_supported()
    p = screen.value
    if len(planes) != p["planes"] or any(len(x) != p["plane_bytes"] for x in planes):
        raise ValueError("Image planes do not match selected screen")
    if not 1000 <= capacity <= 512000:
        raise ValueError("Capacity budget must be 1000..512000 bytes")
    if passes not in ("auto", "full", "two", "first", "second"):
        raise ValueError("Unknown pass mode")
    if transport not in ("auto", "d2", "d3"):
        raise ValueError("Unknown transport")
    mode = "two" if passes == "auto" and sum(map(len, planes)) > capacity else "full" if passes == "auto" else passes
    if mode == "full":
        if sum(map(len, planes)) > capacity:
            raise ValueError("Frame exceeds capacity budget; compression reduces UART traffic, NOT stored image bytes")
        actual_transport = p["transport"] if transport == "auto" else transport
        return (Job("full", tuple(bytes.fromhex(v) for v in (p["db_clear"], *p["db_apdus"], p["da_apdu"], p["da_scan_apdu"])),
                    bytes.fromhex(p["expected_d1_a0"]), bytes.fromhex(p["expected_d1_a1"]), planes,
                    image_packets(planes, actual_transport, compression), bytes.fromhex(p["d4_apdu"]),
                    p["refresh_timeout_seconds"], p["minimum_observation_seconds"]),)
    catalog = asset_json("two-pass.json")
    if screen.id not in catalog or p["packing"] != "huawei_2bpp" or len(planes) != 1:
        raise ValueError("No reviewed multi-refresh recipe for this panel; choose a smaller screen or known capacity")
    if screen.id == "huawei_a0_jd79665" and p.get("row_mapping") != "a0_interleaved":
        raise ValueError("A0 two-pass flow requires native even/odd mapping BEFORE Source splitting")
    if capacity < 52992 or transport == "d2":
        raise ValueError("A0/A1 halves require D3 and at least 52992 bytes capacity")
    profile = catalog[screen.id]
    if (profile["full_raw_bytes"] != 105984 or profile["half_raw_bytes"] != 52992
            or profile["db_clear"] != DB_CLEAR.hex().upper()
            or profile["expected_d1_a0"] != "F0C72003000228"
            or profile["expected_d1_a1"] != "01120030FFFFFF"
            or profile["da_apdu"] != "F0DA000003F0C720"
            or profile["da_scan_apdu"] != "F0DA010103120030"
            or profile["d4_async"] != "F0D4858000"):
        raise ValueError("Two-pass metadata changed; review before use")
    jobs = []
    for entry in profile["passes"]:
        half = entry["half"]
        if mode == "first" and half != 0 or mode == "second" and half != 1:
            continue
        db = bytes.fromhex(entry["db_apdu"])
        if hashlib.sha256(db).hexdigest() != entry["db_sha256"] or db[:5] != bytes((0xF0, 0xDB, 0, 0, len(db) - 5)):
            raise ValueError("Two-pass DB fingerprint/framing mismatch")
        tags = driver_tags(db[5:])
        source = (half * 384, half * 384 + 383)
        window = b"\x83" + source[0].to_bytes(2, "big") + source[1].to_bytes(2, "big") + b"\x00\x00\x02\x27\x01"
        if (half not in (0, 1) or entry["source_x"] != list(source) or entry["gate_y"] != [0, 551]
                or tags[0] != (0xA0, bytes.fromhex("C72003000228"))
                or (0xA1, window) not in tags or (0xA2, window) not in tags
                or (0xA1, bytes.fromhex("6103000228")) not in tags
                or (0xA2, bytes.fromhex("1201" if "_a0_" in screen.id else "1200")) not in tags):
            raise ValueError("Two-pass window/native TRES/DRF contract mismatch")
        raw = split_half(planes[0], half)
        config = (DB_CLEAR, db, bytes.fromhex(profile["da_apdu"]), bytes.fromhex(profile["da_scan_apdu"]))
        jobs.append(Job(f"half-{half + 1}", config, bytes.fromhex(profile["expected_d1_a0"]),
                        bytes.fromhex(profile["expected_d1_a1"]), (raw,), image_packets((raw,), "d3", compression),
                        bytes.fromhex(profile["d4_async"]), profile["refresh_timeout_seconds"],
                        profile["minimum_observation_seconds"], half))
    if len(jobs) != (2 if mode == "two" else 1):
        raise ValueError("Two-pass plan is missing a requested half")
    return tuple(jobs)
