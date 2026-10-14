# 进阶使用

[中文](advanced-use.md) | [English](advanced-use.en.md)

## 连接与屏幕选择

`probe` 只读驱动板身份和访问状态，不认证、不改配置、不传图。`prepare` 仅生成本地预览，不打开串口。

`--screen` 选择完整屏幕配方。也可用 `--ic SSD1683 --resolution 400x300 --color bwr` 筛选；如果匹配多个型号，必须指定 `--screen`。不能只改变分辨率而沿用其他屏幕的驱动。

默认 `--board auto` 依次尝试 `26`、`52`、`7A`、`7D`，再核对驱动板信息。华为Nova墨水屏手机壳NFC驱动板可用 `--board huawei` 限定；标准唤醒 FMSC 驱动板保留 `--board iphone` 兼容参数。

## 图像处理

提供 16 种图像算法和七种预设，使用 `algorithms` 查看名称。`--strength` 调整抖动强度，`--heavy-ink` 增强深色，`--contrast` 调整对比度。

`--scale` 控制缩放，`--pan-x`、`--pan-y` 调整裁剪位置，`--mirror-x`、`--mirror-y` 控制镜像。`--rotation` 可选 `auto`、`0`、`90`、`180`、`270`。`--fit` 默认 `crop`，也可选 `stretch`。

建议先通过 `prepare` 检查预览，再执行传图刷新。

## 时序与传输

| 参数 | 默认值 | 用途 |
|---|---|---|
| `--precharge-ms` | 1000 | 连接后充能等待 |
| `--packet-delay-ms` | 0 | 图片包之间的间隔 |
| `--pre-refresh-ms` | 0 | 刷新指令前等待 |
| `--first-de-delay-ms` | 0 | 首次查询状态前等待 |
| `--de-hz` | 9 | 状态查询目标频率，实际受通信耗时限制 |
| `--rf-timeout-ms` | 1000 | 射频交换等待时间 |
| `--d4-timeout` | 8 | 刷新指令回包期限，单位秒，不是刷新时长 |
| `--de-timeout` | 5 | 单次 DE 状态查询回包期限，单位秒，范围 2..60；包含 WTX 延时 |
| `--timeout` | 按屏幕设置 | 刷新观察时间上限，单位秒 |

单次 DE 查询超过期限或通信中断时停止本次操作，不自动重发 D4。收到 WTX 时会显示等待进度并记录延时请求；WTX 不会重置单次查询期限。

默认 D3 + LZO 压缩传输。`--no-compress` 等同于 `--transport d2`，两者不能同时使用。`--transport auto` 使用配方指定路径。`--compression literal` 仍是 D3 编码，不是 D2 原始传输。

压缩不能扩容。驱动板未声明 D3 支持时会在写入前停止，不自动改用 D2。D2 每个图槽最多 64000 bytes；A0/A1 整帧及分段配方不能使用 D2。

## A0 / A1 分段刷新

```sh
python -m ink_nfc_fmsc send picture.png --screen huawei-a1 --board iphone --passes two --port COM18
```

`--passes auto` 按容量预算选择；`full` 要求整帧，`two` 分两段，`first`、`second` 单独刷新某一段。每段复用图槽 0，原始数据为 52992 bytes；第一段失败时停止后续操作。

分段不保证降低峰值功耗，也不保证另一段画面保持不变。最后保存的是末段配置与图片，此时 `refresh` 仅重刷已存的一段。

默认保守预算：标准唤醒驱动板为 80000 bytes，华为驱动板为 200000 bytes。它不是自动测出的物理容量；提高 `--capacity` 不会扩容。其他主控没有通用分段方案。

## 配置与安全

不指定 `--screen` 或自定义配方时保留已存驱动，不写 DB/DA。现有配置和实际屏幕必须一致；配置不能唯一对应图像格式时程序停止，可用主控、分辨率和颜色参数筛选。`--data-format linear` 仅适用于保留已有 A0 驱动时的布局对照，不能用来识别 A0/A1；默认 `native` 使用原生行映射。保留驱动不能与分段配置混用。

`--uid` 可锁定驱动板。`--pin` 仅提供自己已知的华为 PIN，不猜测或连续重试。`--profile-file` 导入完整配方属于进阶操作，使用者需确认配方和接线。

`screens --json --details` 可导出配方；`id` 是规范名称，`recipe_id` 是内部配方编号，不是屏幕选择名称。应重新导出当前版本的配方，不依赖旧版内部字段。

六色屏核对第 16 脚及外围电路后，添加 `--confirm-e6-wiring`。其他屏幕不能直接套用六色接线或 A0/A1 的分段方案。
