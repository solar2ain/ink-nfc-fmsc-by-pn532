"""Small product CLI; hardware experiments and arbitrary APDUs are intentionally absent."""
import argparse
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

from serial.tools import list_ports

from . import __version__
from .catalog import asset_json, screens, select_screen
from .console import Console, configure_output, format_message
from .device import BOARD_NAMES, Device, RunLog, Settings
from .image_processing import IMAGE_ALGORITHMS, PROCESSING_PRESETS, ImageProcessOptions
from .images import SOLID_COLORS, prepare_image, solid_color, test_pattern
from .plans import jobs_for
from .protocol import D8
from .user_messages import failure_description, stage_label


def parse_hex(value):
    try:
        return bytes.fromhex(value.replace(":", "").replace("-", ""))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected hexadecimal bytes") from exc


def resolution(value):
    try:
        w, h = (int(part) for part in value.lower().split("x"))
        if not 8 <= w <= 2048 or not 8 <= h <= 2048:
            raise ValueError()
        return w, h
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Resolution must be WIDTHxHEIGHT, 8..2048") from exc


def screen_options(parser):
    parser.add_argument("--screen", help="主控-分辨率-颜色[-变体]，如 SSD1683-400x300-BWR；大小写不敏感")
    parser.add_argument("--ic", "--driver", dest="ic", help="屏幕主控名称、系列或驱动编号")
    parser.add_argument("--resolution", type=resolution)
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--color", type=str.casefold, choices=("bw", "bwr", "bwry", "gray4", "e6"))
    parser.add_argument("--profile-file", type=Path, help="Explicit custom JSON recipe, validated before use")


def hardware_options(parser):
    parser.add_argument("--port", help="读写器实际串口名称，如 COM3、/dev/cu.usbserial-...、/dev/ttyUSB0")
    parser.add_argument("--board", choices=("auto", "huawei", "iphone"), default="auto",
                        help=f"默认自动寻卡；huawei：{BOARD_NAMES['huawei']}；iphone：{BOARD_NAMES['iphone']}")
    parser.add_argument("--reader", choices=("auto", "pn532", "pcr532"), default="auto",
                        help="auto uses PCR532-compatible short UART frames; no write retries")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--uid", type=parse_hex, help="Optional exact UID lock")
    parser.add_argument("--pin", type=parse_hex, help="Known 5-byte Huawei PIN override; never brute-forced")
    parser.add_argument("--precharge-ms", type=int, default=1000)
    parser.add_argument("--packet-delay-ms", type=int, default=0)
    parser.add_argument("--pre-refresh-ms", type=int, default=0)
    parser.add_argument("--first-de-delay-ms", type=int, default=0)
    parser.add_argument("--de-hz", type=float, default=9.0, help="Target polling rate; transport latency may limit actual rate")
    parser.add_argument("--rf-timeout-ms", type=int, default=1000)
    parser.add_argument("--d4-timeout", type=float, default=8.0, help="D4 acknowledgement deadline, not waveform duration")
    parser.add_argument("--de-timeout", type=float, default=5.0, help="单次 DE 状态查询回包期限，单位秒，范围 2..60")
    parser.add_argument("--timeout", type=float, help="Total refresh observation deadline in seconds")
    parser.add_argument("--log-dir", type=Path, default=Path("logs"))
    parser.add_argument("--verbose", action="store_true", help="显示详细协议信息；一般使用无需开启")


def upload_options(parser):
    parser.add_argument("--capacity", type=int, help="Conservative decoded-image budget; defaults: iPhone 80000, Huawei 200000")
    parser.add_argument("--passes", choices=("auto", "full", "two", "first", "second"), default="auto")
    transport = parser.add_mutually_exclusive_group()
    # Normalize after parsing so explicit d3 still conflicts with --no-compress on Python 3.11/3.12.
    transport.add_argument("--transport", choices=("auto", "d2", "d3"), default=None,
                           help="默认 d3 压缩传图；auto 使用屏幕原有路径")
    transport.add_argument("--no-compress", dest="transport", action="store_const", const="d2",
                           help="不压缩传图，使用 D2 原始数据传输")
    parser.add_argument("--compression", choices=("lzo", "literal"), default="lzo")
    parser.add_argument("--data-format", choices=("native", "linear"), default="native",
                        help="A0 native includes even/odd row mapping; linear keeps logical row order")
    parser.add_argument("--confirm-e6-wiring", action="store_true", help="Pin16 is independently wired, NOT shorted to pin15/3.3V")


def image_options(parser, *, processing=True):
    parser.add_argument("--rotation", "--direction", default="auto", choices=("auto", "0", "90", "180", "270"))
    parser.add_argument("--fit", choices=("crop", "stretch"), default="crop")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--pan-x", type=float, default=0.0)
    parser.add_argument("--pan-y", type=float, default=0.0)
    parser.add_argument("--mirror-x", action="store_true")
    parser.add_argument("--mirror-y", action="store_true")
    if not processing:
        return
    parser.add_argument("--algorithm", default="auto", help="图像算法名称或编号 1..16；使用 algorithms 查看列表")
    parser.add_argument("--preset", choices=PROCESSING_PRESETS, default="balanced")
    parser.add_argument("--contrast", type=float, default=1.0)
    parser.add_argument("--strength", type=float, default=1.0)
    parser.add_argument("--heavy-ink", action="store_true")


def parser():
    root = argparse.ArgumentParser(prog="ink-nfc", description="Ink NFC：通过 PN532 / PCR532 为墨水屏传图刷新")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    ports = commands.add_parser("ports", help="查看读写器串口")
    ports.add_argument("--json", action="store_true")
    panel_list = commands.add_parser("screens", help="按主控分类查看屏幕型号")
    screen_options(panel_list)
    panel_list.add_argument("--json", action="store_true")
    panel_list.add_argument("--details", action="store_true", help="Include recipe APDUs and qualifications in JSON")
    algorithms = commands.add_parser("algorithms", help="查看图片处理方式")
    algorithms.add_argument("--json", action="store_true")
    probe = commands.add_parser("probe", help="检查驱动板连接，不改配置、不刷屏")
    hardware_options(probe)
    probe.add_argument("--json", action="store_true")
    prepare = commands.add_parser("prepare", help="在电脑上生成图片预览，不连接读写器")
    prepare.add_argument("image", type=Path, nargs="?")
    prepare.add_argument("--white", action="store_true")
    prepare.add_argument("--board", choices=("huawei", "iphone"), default="huawei")
    prepare.add_argument("--output", type=Path, required=True)
    screen_options(prepare)
    upload_options(prepare)
    image_options(prepare)
    descriptions = {"send": "传图刷新 / upload image and refresh",
                    "send-test": "发送色块和棋盘格测试图并刷新 / upload test pattern and refresh",
                    "send-color": "全屏单色刷新 / upload solid color and refresh",
                    "clear": "清白 / upload white image and refresh"}
    for name, description in descriptions.items():
        sub = commands.add_parser(name, help=description)
        if name == "send":
            sub.add_argument("image", type=Path)
        elif name == "send-color":
            sub.add_argument("fill_color", type=str.casefold, choices=tuple(SOLID_COLORS),
                             help="单色名称；必须是所选屏幕支持的原生颜色")
        screen_options(sub)
        hardware_options(sub)
        upload_options(sub)
        image_options(sub, processing=name not in ("send-test", "send-color"))
        sub.add_argument("--yes", action="store_true", help="跳过手动确认；改写屏幕配置的风险不变")
    refresh = commands.add_parser("refresh", help="\u91cd\u5237 / refresh stored image, no new image or DB/DA writes")
    screen_options(refresh)
    hardware_options(refresh)
    refresh.add_argument("--confirm-e6-wiring", action="store_true")
    refresh.add_argument("--yes", action="store_true", help="跳过手动确认，直接重刷已存图片")
    return root


def selected(args):
    width, height = args.width, args.height
    if args.resolution:
        if width is not None or height is not None:
            raise ValueError("Use --resolution OR --width/--height")
        width, height = args.resolution
    return select_screen(args.screen, ic=args.ic, width=width, height=height, color=args.color, profile_file=args.profile_file)


def native_screen(args, screen=None):
    screen = selected(args) if screen is None else screen
    if args.data_format != "native":
        if screen.id != "huawei_a0_jd79665":
            raise ValueError("--data-format linear only applies to Huawei A0")
        if args.command != "prepare" and not args.keep_driver:
            raise ValueError("--data-format linear 仅用于保留已存驱动；请省略 --screen，并用 --ic / --resolution 筛选。")
        screen = replace(screen, value={**screen.value, "row_mapping": "linear"})
    return screen


def stored_screen(args, fields):
    """D1 does not identify the attached glass or expose the stored SPI recipe."""
    width, height = args.width, args.height
    if args.resolution:
        if width is not None or height is not None:
            raise ValueError("Use --resolution OR --width/--height")
        width, height = args.resolution
    candidates = []
    for screen in screens():
        if not screen.supported or (args.ic and not screen.matches_controller(args.ic)):
            continue
        if getattr(args, "data_format", "native") == "linear" and screen.id != "huawei_a0_jd79665":
            continue
        if ((width is not None and screen.value["width"] != width)
                or (height is not None and screen.value["height"] != height)
                or (args.color and screen.color != args.color)):
            continue
        geometry = bytes.fromhex(screen.value["expected_d1_a0"])
        scan = bytes.fromhex(screen.value["expected_d1_a1"])
        factory_vertical = (fields.get(0xC0) == bytes.fromhex("00000001")
                            and geometry == bytes.fromhex("F0C72006000228")
                            and scan == bytes.fromhex("01120030FFFFFF")
                            and fields.get(0xA1) == bytes.fromhex("00120030FFFFFF"))
        half = (args.command == "refresh" and screen.id in asset_json("two-pass.json")
                and fields.get(0xA0) == bytes.fromhex("F0C72003000228"))
        if (fields.get(0xA0) == geometry or half) and (fields.get(0xA1) == scan or factory_vertical):
            candidates.append(screen)
    if len(candidates) != 1:
        names = ", ".join(s.name for s in candidates)
        raise ValueError("已存配置无法唯一确定图像格式，未写入配置或刷新。"
                         "请用 --ic、--resolution、--color 筛选（仍保留已存驱动）；"
                         "需要更换驱动时再指定 --screen。" + (f"候选：{names}" if names else "没有匹配型号。"))
    return candidates[0]


def validate_operation(args, screen):
    screen.require_supported()
    if screen.value.get("requires_e6_wiring_confirmation") and not args.confirm_e6_wiring:
        raise ValueError("Confirm E6 pin16 independent wiring using --confirm-e6-wiring; no display command sent")
    if args.command in ("send", "send-test", "send-color", "clear") and args.keep_driver and args.passes not in ("auto", "full"):
        raise ValueError("保留已存驱动时不能安装分段配方；分段刷新请显式指定 --screen。")
    if args.command == "send-color":
        solid_color(screen, args.fill_color)


def prepare(args, screen):
    options = None if args.command in ("send-test", "send-color") else ImageProcessOptions(
        args.algorithm, args.preset, args.contrast, args.strength, args.heavy_ink).validated()
    image = test_pattern(screen) if args.command == "send-test" else getattr(args, "image", None)
    return prepare_image(screen, image, white=args.command == "clear" or getattr(args, "white", False),
        fill_color=getattr(args, "fill_color", None),
        rotation=args.rotation, fit=args.fit, scale=args.scale, pan_x=args.pan_x, pan_y=args.pan_y,
        mirror_x=args.mirror_x, mirror_y=args.mirror_y, options=options, native_palette=args.command == "send-test")


def save_plan(path, screen, jobs, preview, planes, *, preview_path=None):
    path.mkdir(parents=True, exist_ok=True)
    preview.save(preview_path or path / "preview.png")
    for i, raw in enumerate(planes):
        (path / f"plane-{i}.bin").write_bytes(raw)
    value = {"schema": 1, "version": __version__, "screen": screen.name, "recipe_id": screen.id, "qualification": screen.value["qualification"],
             "optically_verified": False, "jobs": []}
    for job in jobs:
        value["jobs"].append({"name": job.name, "half": job.half, "raw_bytes": job.raw_bytes, "sha256": job.sha256,
            "geometry": job.geometry.hex().upper(), "scan": job.scan.hex().upper(),
            "config": [p.hex().upper() for p in job.config], "image_packets": [p.hex().upper() for p in job.packets],
            "d4": job.d4.hex().upper(), "timeout_seconds": job.timeout})
    (path / "plan.json").write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def serial_settings(args):
    port = args.port
    if not port:
        ports = list(list_ports.comports())
        if len(ports) != 1:
            raise ValueError(f"Specify --port; found {len(ports)} ports. Run ink-nfc ports")
        port = ports[0].device
    return Settings(port=port, board=args.board, reader=args.reader, baud=args.baud, precharge_ms=args.precharge_ms,
        packet_delay_ms=args.packet_delay_ms, pre_refresh_ms=args.pre_refresh_ms, first_de_delay_ms=args.first_de_delay_ms,
        de_hz=args.de_hz, rf_timeout_ms=args.rf_timeout_ms, d4_timeout=args.d4_timeout, de_timeout=args.de_timeout,
        expected_uid=args.uid, pin=args.pin, verbose=args.verbose)


def parse_args(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in ("flash", "\u4f20\u56fe\u5237\u65b0"):
        arguments[0] = "send"
    args = parser().parse_args(arguments)
    if hasattr(args, "transport") and args.transport is None:
        args.transport = "d3"
    if args.command in ("send", "send-test", "send-color", "clear", "refresh"):
        args.keep_driver = args.command == "refresh" or not (args.screen or args.profile_file)
    return args


def confirm_operation(args, screen, console):
    replaces_driver = args.command in ("send", "send-test", "send-color", "clear") and not args.keep_driver
    if not args.yes:
        print("=" * 60, file=sys.stderr)
        print("风险确认" if replaces_driver else "刷新确认", file=sys.stderr)
    if replaces_driver:
        message = "风险：将覆盖驱动板的原厂屏幕配置，当前工具无法还原原厂驱动。请确认屏幕型号和接线。"
        console(format_message(message, level="warning", stage="确认"), file=sys.stderr, emphasis=True)
    else:
        console(format_message("保留已存驱动，不写入 DB/DA。请确认实际屏幕与板内配置一致；工具不能自动识别实体屏幕。",
                               level="warning", stage="确认"), file=sys.stderr)
    console(format_message(f"屏幕：{screen.name}", stage="确认", detail=True), file=sys.stderr)
    if args.yes:
        return True
    print("=" * 60, file=sys.stderr)
    if not sys.stdin.isatty():
        raise ValueError("手动确认需要交互终端；非交互运行请添加 --yes 跳过确认。")
    try:
        return input("继续操作？[y/N] ").strip().casefold() in ("y", "yes")
    except EOFError:
        return False


def main(argv=None):
    configure_output()
    args = parse_args(argv)
    logger = None
    console = Console()
    console.suspend_selection()

    def say(message, *, level="info", stage=None, detail=False, file=None):
        line = (logger.message(message, level=level, stage=stage, detail=detail) if logger
                else format_message(message, level=level, stage=stage, detail=detail))
        console(line, file=file)

    try:
        if args.command == "ports":
            rows = [{"port": p.device, "description": p.description, "hwid": p.hwid} for p in list_ports.comports()]
            if args.json:
                print(json.dumps(rows, ensure_ascii=True))
            else:
                for row in rows:
                    print(f"{row['port']}: {row['description']} ({row['hwid']})")
                if not rows:
                    print("没有发现串口，请检查读写器 USB 连接。")
            return 0
        if args.command == "algorithms":
            rows = [{"index": a.index, "name": a.name, "title": a.title, "direct_strength": a.direct_strength} for a in IMAGE_ALGORITHMS]
            if args.json:
                print(json.dumps({"algorithms": rows, "presets": PROCESSING_PRESETS}))
            else:
                for row in rows:
                    print(f"{row['index']:2} {row['name']:22} {row['title']}")
                print("Presets: " + ", ".join(PROCESSING_PRESETS))
            return 0
        if args.command == "screens":
            values = screens()
            width, height = args.resolution or (args.width, args.height)
            if args.screen or args.profile_file:
                screen = selected(args)
                screen.require_supported()
                values = [screen]
            values = [s for s in values if s.supported and (not args.ic or s.matches_controller(args.ic))
                      and (width is None or width == s.value["width"]) and (height is None or height == s.value["height"])
                      and (args.color is None or args.color == s.color)]
            if args.json:
                rows = []
                for screen in values:
                    row = dict(screen.value) if args.details else {k: screen.value.get(k) for k in
                          ("label", "family", "controller", "width", "height", "packing", "raw_bytes", "supported", "qualification", "unsupported_reason")}
                    if args.details:
                        row["recipe_id"] = screen.id
                    else:
                        row["label"] = screen.name
                    row["id"] = screen.name
                    row["aliases"] = list(screen.aliases)
                    rows.append(row)
                print(json.dumps(rows, ensure_ascii=True, indent=2))
            else:
                rows = [(s.name, ", ".join(s.aliases)) for s in values]
                name_width = max(4, max((len(name) for name, _ in rows), default=0))
                alias_width = max(4, max((len(aliases) for _, aliases in rows), default=0))
                print(f"屏幕{' ' * (name_width - 4)} | 别名{' ' * (alias_width - 4)} | 状态")
                print(f"{'-' * name_width}-+-{'-' * alias_width}-+-----")
                for name, aliases in rows:
                    print(f"{name:{name_width}} | {aliases:{alias_width}} | 可用")
                print(f"共 {len(values)} 个型号；能否完成刷新还取决于接线、线圈位置和供电。")
            return 0
        if args.command == "prepare":
            screen = native_screen(args)
            planes, preview = prepare(args, screen)
            capacity = args.capacity if args.capacity is not None else 80000 if args.board == "iphone" else 200000
            jobs = jobs_for(screen, planes, capacity=capacity, passes=args.passes, transport=args.transport, compression=args.compression)
            save_plan(args.output, screen, jobs, preview, planes)
            say(f"预览已生成：{args.output.resolve() / 'preview.png'}；共 {len(jobs)} 段，本次没有连接读写器。", level="success", stage="预览")
            return 0
        screen = None
        if args.command != "probe" and (args.screen or args.profile_file):
            screen = selected(args) if args.command == "refresh" else native_screen(args)
            validate_operation(args, screen)
        settings = serial_settings(args)
        if screen is not None:
            if not confirm_operation(args, screen, console):
                say("已取消，未连接读写器或写入配置。", level="warning", stage="结果")
                return 0
            if args.command in ("send", "send-test", "send-color", "clear"):
                planes, preview = prepare(args, screen)
        logger = RunLog(args.log_dir)
        logger.update(operation=args.command, version=__version__)
        if args.command == "send-color":
            logger.update(fill_color=args.fill_color)
            say(f"全屏单色：{args.fill_color}。", stage="准备")
        if screen is not None:
            logger.update(screen=screen.name, screen_label=screen.name,
                          resolution=[screen.value["width"], screen.value["height"]], color=screen.color)
            banner = f"屏幕：{screen.name}"
            say(banner, stage="准备")
        with Device(settings, logger, output=(lambda _line: None) if getattr(args, "json", False) else console) as device:
            if args.command == "probe":
                access = device.exchange("D8 (no PIN)", D8, require_ok=False)
                value = {"uid": device.activation.uid.hex().upper(), "board": device.board, "board_name": BOARD_NAMES[device.board],
                         "wake": f"{device.activation.wake_command:02X}", "d8_sw": f"{access.sw:04X}", "meaning": access.meaning,
                         "capacity_budget": device.capacity, "capacity_measured": False,
                         "fields": {f"{k:02X}": v.hex().upper() for k, v in device.fields.items()}, "log": str(logger.path.resolve())}
                logger.finish("checked", d8_sw=value["d8_sw"])
                if args.json:
                    print(json.dumps(value, ensure_ascii=True, indent=2))
                else:
                    text = "需要认证，传图时会自动处理" if access.sw == 0x6985 else access.meaning
                    message = f"访问检查：{text}（D8={access.sw:04X}）；本次没有更改配置或刷屏。"
                    say(message, level="success" if access.sw == 0x9000 else "warning", stage="权限")
                    say(f"日志：{logger.text_path.resolve()}", stage="日志")
                return 0
            if screen is None:
                screen = stored_screen(args, device.fields)
                if args.command != "refresh":
                    screen = native_screen(args, screen)
                validate_operation(args, screen)
                if not confirm_operation(args, screen, console):
                    logger.finish("cancelled")
                    say("已取消；仅读取了驱动板配置，未认证、传图或写入驱动。", level="warning", stage="结果")
                    return 0
                logger.update(screen=screen.name, screen_label=screen.name,
                              resolution=[screen.value["width"], screen.value["height"]], color=screen.color)
                say(f"保留已存驱动；图像格式：{screen.name}。请勿更换屏幕。", stage="准备")
                if args.command in ("send", "send-test", "send-color", "clear"):
                    planes, preview = prepare(args, screen)
            if args.command == "refresh":
                # Empty planes mark needed slots; no image data is ever sent in this mode.
                white_planes, _ = prepare_image(screen, white=True)
                if device.fields[0xA0] == bytes.fromhex(screen.value["expected_d1_a0"]):
                    job = jobs_for(screen, white_planes, capacity=512000, passes="full")[0]
                elif screen.id in asset_json("two-pass.json") and device.fields[0xA0] == bytes.fromhex("F0C72003000228"):
                    job = jobs_for(screen, white_planes, capacity=80000, passes="first")[0]
                    message = "驱动板当前保存的是半屏图片，本次只重刷已存的那一段。"
                    say(message, level="warning", stage="准备")
                else:
                    raise ValueError("Existing D1 geometry does not match this screen; no D4 sent")
                job = replace(job, name="existing-slot", config=(), planes=tuple(b"" for _ in range(screen.value["planes"])), packets=())
                device.run((job,), keep_driver=True, upload=False, timeout=args.timeout)
            else:
                capacity = args.capacity if args.capacity is not None else device.capacity
                jobs = jobs_for(screen, planes, capacity=capacity, passes="full" if args.keep_driver else args.passes,
                                transport=args.transport, compression=args.compression)
                save_plan(logger.details, screen, jobs, preview, planes, preview_path=logger.directory / "preview.png")
                if len(jobs) > 1 or jobs[0].half is not None:
                    message = "图片将分段刷新。请保持线圈位置不动，结束后确认两段画面是否完整。"
                    say(message, level="warning", stage="分段")
                device.run(jobs, keep_driver=args.keep_driver, timeout=args.timeout, capacity=capacity)
        logger.finish("reported_idle")
        say(f"日志：{logger.text_path.resolve()}", stage="日志")
        return 0
    except KeyboardInterrupt:
        if logger:
            logger.event("interrupted", {"retry": False})
            logger.finish("cancelled")
        say("已取消，程序已尝试关闭射频场，不会自动重刷。", level="warning", stage="结果", file=sys.stderr)
        return 130
    except Exception as exc:
        failure = failure_description(exc)
        if logger:
            logger.event("error", {"error": str(exc), "retry": False})
            stage = stage_label(logger.summary.get("last_stage", ""))
            say(f"失败位置：{stage}。", level="error", stage="结果", file=sys.stderr)
            logger.finish("failed", failure=failure, failure_stage=stage)
        message = f"未完成：{failure['message']}" + (f"（{failure['code']}）" if failure["code"] else "")
        say(message, level="error", stage="结果", detail=True, file=sys.stderr)
        say(f"建议：{failure['advice']}", level="warning", stage="结果", detail=True, file=sys.stderr)
        if logger:
            say(f"日志：{logger.text_path.resolve()}", stage="日志", file=sys.stderr)
        return 1
    finally:
        console.restore_selection()
