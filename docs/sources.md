# 组件与许可

[中文](sources.md) | [English](sources.en.md)

本工具包含 Python 串口通信、图像处理和屏幕驱动配方。安装后可独立运行，不需要其他项目目录、固件 SDK、手机应用或 Node.js。

## 第三方组件

- `pyserial`：串口访问。
- `Pillow`：图像读取、缩放与旋转。
- `lzokay`：LZO 压缩。
- `mini-racer`：运行随安装包附带的图像处理脚本。
- `colorama`：Windows 终端颜色兼容。
- [`epdoptimize.cjs`](https://github.com/paperlesspaper/epdoptimize)：图像抖动组件，保留 Apache-2.0 许可证原文和版权声明。

第三方依赖适用各自的许可证和版权声明；附带许可证见 `src/ink_nfc_fmsc/_assets/EPDOPTIMIZE_LICENSE.txt`。本项目代码采用 **GNU GPLv3 或更新版本（GPL-3.0-or-later）**，许可证原文见根目录 `LICENSE`。

配方、脚本和许可文件的 SHA256 记录在安装资源的 `provenance.json` 中，可用于完整性核对。资源校验不等于硬件兼容性或完整刷屏验证。

下载测试图片适用各自的版权条件，不受本工具代码许可覆盖。发布或再分发前，应确认图片权利人的授权。
