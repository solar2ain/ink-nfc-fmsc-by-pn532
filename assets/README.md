# 测试图片

[中文](README.md) | [English](README.en.md)

`examples/` 包含可直接使用的示例图。`gooddisplay/` 包含 30 张原厂示例图（16 张 BMP、14 张 PNG），全部位于外层目录。

传图前请通过 `prepare` 检查缩放、颜色和方向。图片尺寸与屏幕不一致时，默认会居中裁剪；需要完整保留画面比例时，应先制作对应尺寸的图片。

```sh
python -m ink_nfc_fmsc prepare assets/examples/mountain-journey-400x300.png --screen SSD1683-400x300-BWR --output output/preview
```

图片不包含驱动程序，不能用于确定未知屏幕的初始化或接线。

## Good Display 示例图

图片来自 [Good Display 官方下载](https://www.good-display.com/companyfile/29/)，保留原始文件内容。`bw` 表示黑白示例，`r` 表示黑白红示例，`f` 表示四色示例；文件名不能代替屏幕型号与颜色核对。

命令中的 `COMxx` 是 Windows 串口占位符，请替换为 `python -m ink_nfc_fmsc ports` 输出的实际串口名称；macOS 和 Linux 使用相应的设备路径。

```sh
python -m ink_nfc_fmsc send assets/gooddisplay/f420.bmp --screen JD79668AA-400x300-BWRY --port COMxx
```

图片的权利归原权利人所有，不适用本项目的 GPL 许可，也不表示已取得独立再许可权。来源、原始文件名、尺寸及 SHA-256 见 [图片清单](gooddisplay-manifest.json)。
