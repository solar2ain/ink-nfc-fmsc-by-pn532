# 示例图片

[中文](README.md) | [English](README.en.md)

`mountain-journey-400x300.png` 是 400×300 黑白红示例图，可用于检查布局、方向与颜色。文件保持原始像素，不包含驱动程序。

命令中的 `COMxx` 是 Windows 串口占位符，请替换为 `python -m ink_nfc_fmsc ports` 输出的实际串口名称；macOS 和 Linux 使用相应的设备路径。

```sh
python -m ink_nfc_fmsc send assets/examples/mountain-journey-400x300.png --screen SSD1683-400x300-BWR --port COMxx
```
