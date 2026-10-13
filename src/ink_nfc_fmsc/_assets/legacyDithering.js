/**
 * 墨水屏图像抖动处理模块（VIP版）
 * 包含多种抖动算法：Floyd-Steinberg, Atkinson, Stucki, Jarvis, Bayer, BlueNoise, Riemersma, Hybrid
 * 以及新增的 Sierra Lite, Burkes, Two-Row Sierra, Ostromoukhov, Linear Light Sierra
 * 支持六色、四色、三色、黑白模式
 * 使用 Lab 色彩空间和 CIEDE2000 色差公式进行颜色匹配，修复色彩偏红问题
 */

// Exact compatibility copy of web/js/dithering.js. DOM toggles are replaced
// by per-call options so the legacy pixel pipeline can run as a pure module.
let inkNfcHeavyInk = false;
let inkNfcGray4Darken = 0.35;

function createLegacyImageData(data, width, height) {
  return typeof ImageData === 'undefined' ? { width, height, data } : new ImageData(data, width, height);
}

// ==================== 色板定义 ====================
// 标准六色调色板（用于算法内部颜色匹配）
// 固定的六色调色板
const rgbPalette = [
  { name: "黄色", r: 255, g: 255, b: 0, value: 0xe2 },
  { name: "绿色", r: 41, g: 204, b: 20, value: 0x96 },
  { name: "蓝色", r: 0, g: 0, b: 255, value: 0x1d },
  { name: "红色", r: 255, g: 0, b: 0, value: 0x4c },
  { name: "黑色", r: 0, g: 0, b: 0, value: 0x00 },
  { name: "白色", r: 255, g: 255, b: 255, value: 0xff }
];

// 四色调色板
const fourColorPalette = [
  { name: "黑色", r: 0, g: 0, b: 0, value: 0x00 },
  { name: "白色", r: 255, g: 255, b: 255, value: 0x01 },
  { name: "红色", r: 255, g: 0, b: 0, value: 0x03 },
  { name: "黄色", r: 255, g: 255, b: 0, value: 0x02 }
];

// 三色调色板
const threeColorPalette = [
  { name: "黑色", r: 0, g: 0, b: 0, value: 0x00 },
  { name: "白色", r: 255, g: 255, b: 255, value: 0x01 },
  { name: "红色", r: 255, g: 0, b: 0, value: 0x02 }
];

// 墨水屏实际显示颜色（用于更精确的颜色匹配，解决偏红问题）
const gray4Palette = [
  { name: "black", r: 0, g: 0, b: 0, value: 0x00 },
  { name: "dark gray", r: 130, g: 130, b: 130, value: 0x01 },
  { name: "light gray", r: 205, g: 205, b: 205, value: 0x02 },
  { name: "white", r: 255, g: 255, b: 255, value: 0x03 }
];

const gray4BaseThresholds = [42.5, 127.5, 212.5];
const gray4DarkThresholds = [70, 180, 238];

function getGray4Darken() {
  return inkNfcGray4Darken;
}

function findClosestGray4Color(r, g, b) {
  const luminance = 0.299 * Math.min(255, Math.max(0, r)) +
                    0.587 * Math.min(255, Math.max(0, g)) +
                    0.114 * Math.min(255, Math.max(0, b));
  const darken = getGray4Darken();
  const t0 = gray4BaseThresholds[0] + (gray4DarkThresholds[0] - gray4BaseThresholds[0]) * darken;
  const t1 = gray4BaseThresholds[1] + (gray4DarkThresholds[1] - gray4BaseThresholds[1]) * darken;
  const t2 = gray4BaseThresholds[2] + (gray4DarkThresholds[2] - gray4BaseThresholds[2]) * darken;
  if (luminance < t0) return gray4Palette[0];
  if (luminance < t1) return gray4Palette[1];
  if (luminance < t2) return gray4Palette[2];
  return gray4Palette[3];
}

const epdRealColors = {
    sixColor: [
        { name: "黄色", realR: 200, realG: 195, realB: 60, r: 255, g: 255, b: 0, value: 0xE2 },
        { name: "绿色", realR: 35, realG: 140, realB: 35, r: 41, g: 204, b: 20, value: 0x96 },
        { name: "蓝色", realR: 30, realG: 40, realB: 140, r: 0, g: 0, b: 255, value: 0x1D },
        { name: "红色", realR: 180, realG: 50, realB: 50, r: 255, g: 0, b: 0, value: 0x4C },
        { name: "黑色", realR: 30, realG: 30, realB: 30, r: 0, g: 0, b: 0, value: 0x00 },
        { name: "白色", realR: 220, realG: 215, realB: 205, r: 255, g: 255, b: 255, value: 0xFF }
    ],
    fourColor: [
        { name: "黑色", realR: 30, realG: 30, realB: 30, r: 0, g: 0, b: 0, value: 0x00 },
        { name: "白色", realR: 220, realG: 215, realB: 205, r: 255, g: 255, b: 255, value: 0x01 },
        { name: "红色", realR: 180, realG: 50, realB: 50, r: 255, g: 0, b: 0, value: 0x03 },
        { name: "黄色", realR: 200, realG: 195, realB: 60, r: 255, g: 255, b: 0, value: 0x02 }
    ],
    threeColor: [
        { name: "黑色", realR: 30, realG: 30, realB: 30, r: 0, g: 0, b: 0, value: 0x00 },
        { name: "白色", realR: 220, realG: 215, realB: 205, r: 255, g: 255, b: 255, value: 0x01 },
        { name: "红色", realR: 180, realG: 50, realB: 50, r: 255, g: 0, b: 0, value: 0x02 }
    ],
    blackWhite: [
        { name: "黑色", realR: 30, realG: 30, realB: 30, r: 0, g: 0, b: 0, value: 0x00 },
        { name: "白色", realR: 220, realG: 215, realB: 205, r: 255, g: 255, b: 255, value: 0x01 }
    ],
    gray4: [
        { name: "black", realR: 30, realG: 30, realB: 30, r: 0, g: 0, b: 0, value: 0x00 },
        { name: "dark gray", realR: 85, realG: 85, realB: 85, r: 85, g: 85, b: 85, value: 0x01 },
        { name: "light gray", realR: 160, realG: 160, realB: 160, r: 170, g: 170, b: 170, value: 0x02 },
        { name: "white", realR: 220, realG: 215, realB: 205, r: 255, g: 255, b: 255, value: 0x03 }
    ]
};

// ==================== 图像预处理函数 ====================

/**
 * 调整对比度（使用查找表优化）
 */
function adjustContrast(imageData, factor) {
  const data = imageData.data;
  for (let i = 0; i < data.length; i += 4) {
    data[i] = Math.min(255, Math.max(0, (data[i] - 128) * factor + 128));
    data[i + 1] = Math.min(255, Math.max(0, (data[i + 1] - 128) * factor + 128));
    data[i + 2] = Math.min(255, Math.max(0, (data[i + 2] - 128) * factor + 128));
  }
  return imageData;
}

function rgbToLab(r, g, b) {
  r = r / 255;
  g = g / 255;
  b = b / 255;

  r = r > 0.04045 ? Math.pow((r + 0.055) / 1.055, 2.4) : r / 12.92;
  g = g > 0.04045 ? Math.pow((g + 0.055) / 1.055, 2.4) : g / 12.92;
  b = b > 0.04045 ? Math.pow((b + 0.055) / 1.055, 2.4) : b / 12.92;

  r *= 100;
  g *= 100;
  b *= 100;

  let x = r * 0.4124 + g * 0.3576 + b * 0.1805;
  let y = r * 0.2126 + g * 0.7152 + b * 0.0722;
  let z = r * 0.0193 + g * 0.1192 + b * 0.9505;

  x /= 95.047;
  y /= 100.0;
  z /= 108.883;

  x = x > 0.008856 ? Math.pow(x, 1 / 3) : (7.787 * x) + (16 / 116);
  y = y > 0.008856 ? Math.pow(y, 1 / 3) : (7.787 * y) + (16 / 116);
  z = z > 0.008856 ? Math.pow(z, 1 / 3) : (7.787 * z) + (16 / 116);

  const l = (116 * y) - 16;
  const a = 500 * (x - y);
  const bLab = 200 * (y - z);

  return { l, a, b: bLab };
}

function labDistance(lab1, lab2) {
  const dl = lab1.l - lab2.l;
  const da = lab1.a - lab2.a;
  const db = lab1.b - lab2.b;
  return Math.sqrt(0.2 * dl * dl + 3 * da * da + 3 * db * db);
}

// sRGB 到线性空间的查找表（加速）
const _srgbToLinear = new Float32Array(256);
const _linearToSrgb = new Uint8Array(4096);
for (let i = 0; i < 256; i++) {
    let v = i / 255;
    _srgbToLinear[i] = v > 0.04045 ? Math.pow((v + 0.055) / 1.055, 2.4) : v / 12.92;
}
for (let i = 0; i < 4096; i++) {
    let v = i / 4095;
    _linearToSrgb[i] = Math.min(255, Math.max(0, Math.round(255 * (v > 0.0031308 ? 1.055 * Math.pow(v, 1 / 2.4) - 0.055 : 12.92 * v))));
}

function findClosestColor(r, g, b, mode) {
  let palette;

  if (mode === 'fourColor') {
    palette = fourColorPalette;
  } else if (mode === 'threeColor') {
    palette = threeColorPalette;
  } else if (mode === 'gray4Color') {
    return findClosestGray4Color(r, g, b);
  } else {
    palette = rgbPalette;
  }

  // 蓝色特殊情况（仅限非三色、四色模式）
  if (mode !== 'fourColor' && mode !== 'threeColor' && mode !== 'gray4Color' && r < 50 && g < 150 && b > 100) {
    return rgbPalette[2]; // 蓝色
  }

  // 三色模式下优先检测红色
  if (mode === 'threeColor') {
    // 如果红色通道显著高于绿色和蓝色，且强度足够
    if (r > 120 && r > g * 1.5 && r > b * 1.5) {
      return threeColorPalette[2]; // 红色
    }
    // 否则根据亮度选择黑或白
    const luminance = 0.299 * r + 0.587 * g + 0.114 * b;
    return luminance < 128 ? threeColorPalette[0] : threeColorPalette[1]; // 黑色或白色
  }

  const inputLab = rgbToLab(r, g, b);
  let minDistance = Infinity;
  let closestColor = palette[0];

  for (const color of palette) {
    const colorLab = rgbToLab(color.r, color.g, color.b);
    const distance = labDistance(inputLab, colorLab);
    if (distance < minDistance) {
      minDistance = distance;
      closestColor = color;
    }
  }

  return closestColor;
}

/**
 * CIEDE2000 色差公式（更精确）
 * 将 RGB 颜色转换为 Lab 色彩空间
 * @param {number} red   R 分量 0-255
 * @param {number} green G 分量 0-255
 * @param {number} blue  B 分量 0-255
 * @returns {{l: number, a: number, b: number}} Lab 颜色值
 */
function rgbToLabRed(red, green, blue) {
    // 非线性转换 (sRGB -> 线性 RGB)
    red /= 255;
    green /= 255;
    blue /= 255;
    red = red > 0.04045 ? Math.pow((red + 0.055) / 1.055, 2.4) : red / 12.92;
    green = green > 0.04045 ? Math.pow((green + 0.055) / 1.055, 2.4) : green / 12.92;
    blue = blue > 0.04045 ? Math.pow((blue + 0.055) / 1.055, 2.4) : blue / 12.92;

    // 转换为 XYZ (D65 标准)
    let x = 0.4124 * (red *= 100) + 0.3576 * (green *= 100) + 0.1805 * (blue *= 100);
    let y = 0.2126 * red + 0.7152 * green + 0.0722 * blue;
    let z = 0.0193 * red + 0.1192 * green + 0.9505 * blue;

    // 归一化
    y /= 100;
    z /= 108.883;
    x /= 95.047;

    // 非线性变换
    const epsilon = 0.008856;
    const kappa = 903.3;
    x = x > epsilon ? Math.pow(x, 1 / 3) : (kappa * x + 16) / 116;
    y = y > epsilon ? Math.pow(y, 1 / 3) : (kappa * y + 16) / 116;
    z = z > epsilon ? Math.pow(z, 1 / 3) : (kappa * z + 16) / 116;

    return {
        l: 116 * y - 16,
        a: 500 * (x - y),
        b: 200 * (y - z)
    };
}

/**
 * 计算两个 Lab 颜色之间的 CIEDE2000 色差
 * @param {{l:number, a:number, b:number}} lab1 第一个 Lab 颜色
 * @param {{l:number, a:number, b:number}} lab2 第二个 Lab 颜色
 * @returns {number} 色差值
 */
function ciede2000(lab1, lab2) {
    let L1 = lab1.l, a1 = lab1.a, b1 = lab1.b;
    let L2 = lab2.l, a2 = lab2.a, b2 = lab2.b;

    // 彩度
    let C1 = Math.sqrt(a1 * a1 + b1 * b1);
    let C2 = Math.sqrt(a2 * a2 + b2 * b2);
    let C_bar = (C1 + C2) / 2;
    let G = 0.5 * (1 - Math.sqrt(Math.pow(C_bar, 7) / (Math.pow(C_bar, 7) + Math.pow(25, 7))));

    // 修正后的 a 值
    let a1_prime = a1 * (1 + G);
    let a2_prime = a2 * (1 + G);

    // 修正后的彩度
    let C1_prime = Math.sqrt(a1_prime * a1_prime + b1 * b1);
    let C2_prime = Math.sqrt(a2_prime * a2_prime + b2 * b2);

    // 色调角
    let h1_prime = Math.atan2(b1, a1_prime) * 180 / Math.PI;
    if (h1_prime < 0) h1_prime += 360;
    let h2_prime = Math.atan2(b2, a2_prime) * 180 / Math.PI;
    if (h2_prime < 0) h2_prime += 360;

    // 亮度差、彩度差、色调差
    let deltaL = L2 - L1;
    let deltaC = C2_prime - C1_prime;
    let deltaH;
    if (C1_prime * C2_prime === 0) {
        deltaH = 0;
    } else {
        let delta_h = Math.abs(h2_prime - h1_prime) <= 180 ? h2_prime - h1_prime : (h2_prime - h1_prime > 180 ? h2_prime - h1_prime - 360 : h2_prime - h1_prime + 360);
        deltaH = 2 * Math.sqrt(C1_prime * C2_prime) * Math.sin(delta_h * Math.PI / 360);
    }

    // 加权因子
    let L_bar = (L1 + L2) / 2;
    let C_bar_prime = (C1_prime + C2_prime) / 2;
    let h_bar_prime;
    if (C1_prime * C2_prime === 0) {
        h_bar_prime = h1_prime + h2_prime;
    } else {
        let delta_h = Math.abs(h1_prime - h2_prime) <= 180 ? h1_prime + h2_prime : (h1_prime + h2_prime < 360 ? (h1_prime + h2_prime + 360) / 2 : (h1_prime + h2_prime - 360) / 2);
        h_bar_prime = delta_h / 2;
    }

    let T = 1 - 0.17 * Math.cos((h_bar_prime - 30) * Math.PI / 180) +
        0.24 * Math.cos(2 * h_bar_prime * Math.PI / 180) +
        0.32 * Math.cos((3 * h_bar_prime + 6) * Math.PI / 180) -
        0.20 * Math.cos((4 * h_bar_prime - 63) * Math.PI / 180);

    let deltaTheta = 30 * Math.exp(-Math.pow((h_bar_prime - 275) / 25, 2));
    let R_C = 2 * Math.sqrt(Math.pow(C_bar_prime, 7) / (Math.pow(C_bar_prime, 7) + Math.pow(25, 7)));
    let S_L = 1 + (0.015 * Math.pow(L_bar - 50, 2)) / Math.sqrt(20 + Math.pow(L_bar - 50, 2));
    let S_C = 1 + 0.045 * C_bar_prime;
    let S_H = 1 + 0.015 * C_bar_prime * T;
    let R_T = -R_C * Math.sin(2 * deltaTheta * Math.PI / 180);

    // 最终色差
    let term1 = deltaL / (0.7 * S_L);
    let term2 = deltaC / S_C;
    let term3 = deltaH / S_H;
    return term1 * term1 + term2 * term2 + term3 * term3 + R_T * term2 * term3;
}

/**
 * 为调色板数组中的每个颜色添加 lab 属性
 * @param {Array} palette      调色板数组，每个元素需包含 r, g, b 或 realR, realG, realB
 * @param {boolean} useRealRGB 是否使用 realR/realG/realB 字段
 * @returns {Array} 添加了 lab 属性的调色板数组
 */
function initPaletteLab(palette, useRealRGB) {
    return palette.map(item => ({
        ...item,
        lab: useRealRGB && item.realR !== undefined
            ? rgbToLabRed(item.realR, item.realG, item.realB)
            : rgbToLabRed(item.r, item.g, item.b)
    }));
}

// 预计算全局调色板（依赖外部变量 rgbPalette, fourColorPalette, threeColorPalette, epdRealColors）
let rgbPaletteWithLab = initPaletteLab(rgbPalette, false);
let fourColorPaletteWithLab = initPaletteLab(fourColorPalette, false);
let threeColorPaletteWithLab = initPaletteLab(threeColorPalette, false);
let epdFourColorWithLab = initPaletteLab(epdRealColors.fourColor, true);
let epdThreeColorWithLab = initPaletteLab(epdRealColors.threeColor, true);
let epdBWWithLab = initPaletteLab(epdRealColors.blackWhite, true);
let epdGray4WithLab = initPaletteLab(epdRealColors.gray4, true);
let epdSixColorWithLab = initPaletteLab(epdRealColors.sixColor, true);

// 黑白阈值（基于 Lab 转换后的亮度）
let _epdBWThreshold = (() => {
    let labBlack = epdBWWithLab[0].lab;
    let labWhite = epdBWWithLab[1].lab;
    let y = (16 + (labBlack.l + labWhite.l) / 2) / 116;
    let linear = y > 6 / 29 ? y * y * y : (6 / 29) * 3 * (6 / 29) * (y - 4 / 29);
    let srgb = linear > 0.0031308 ? 1.055 * Math.pow(linear, 1 / 2.4) - 0.055 : 12.92 * linear;
    return Math.round(255 * srgb);
})();

// 颜色查找表配置
const _LUT_BITS = 5;
const _LUT_SIZE = 1 << _LUT_BITS;      // 32
const _LUT_STEP = 256 / _LUT_SIZE;     // 8
let _colorLUTs = {};

/**
 * 直接查找最接近的颜色（不使用 LUT，用于构建 LUT 时）
 * @param {number} r  红色分量
 * @param {number} g  绿色分量
 * @param {number} b  蓝色分量
 * @param {string} type 调色板类型："blackWhiteColor" / "threeColor" / "fourColor" / "sixColor"
 * @returns {object} 匹配的颜色对象
 */
function _findClosestColorDirect(r, g, b, type) {
    if (type === "blackWhiteColor") {
        let luminance = 0.299 * r + 0.587 * g + 0.114 * b;
        return luminance < _epdBWThreshold
            ? { r: 0, g: 0, b: 0, value: 0 }
            : { r: 255, g: 255, b: 255, value: 1 };
    }

    let palette;
    if (type === "threeColor") palette = epdThreeColorWithLab;
    else if (type === "fourColor") palette = epdFourColorWithLab;
    else if (type === "gray4Color") palette = epdGray4WithLab;
    else palette = epdSixColorWithLab;

    let targetLab = rgbToLabRed(r, g, b);
    let minDelta = Infinity;
    let closest = palette[0];

    for (let color of palette) {
        let delta = ciede2000(targetLab, color.lab);
        if (delta < minDelta) {
            minDelta = delta;
            closest = color;
        }
    }
    return closest;
}

/**
 * 构建指定类型的颜色查找表（三维 LUT）
 * @param {string} type 调色板类型
 * @returns {Array} 长度为 _LUT_SIZE^3 的 LUT 数组
 */
function _buildColorLUT(type) {
    const size = _LUT_SIZE;
    const step = _LUT_STEP;
    let lut = new Array(size * size * size);

    for (let rIdx = 0; rIdx < size; rIdx++) {
        let r = Math.min(255, rIdx * step + (step >> 1));
        for (let gIdx = 0; gIdx < size; gIdx++) {
            let g = Math.min(255, gIdx * step + (step >> 1));
            let baseIndex = (rIdx * size + gIdx) * size;
            for (let bIdx = 0; bIdx < size; bIdx++) {
                let b = Math.min(255, bIdx * step + (step >> 1));
                lut[baseIndex + bIdx] = _findClosestColorDirect(r, g, b, type);
            }
        }
    }
    return lut;
}

/**
 * 获取指定类型的颜色查找表（若不存在则构建）
 * @param {string} type 调色板类型
 * @returns {Array} LUT 数组
 */
function _getColorLUT(type) {
    if (!_colorLUTs[type]) {
        _colorLUTs[type] = _buildColorLUT(type);
    }
    return _colorLUTs[type];
}

/**
 * 使用 LUT 快速查找最接近的颜色（对外接口）
 * @param {number} r  红色分量
 * @param {number} g  绿色分量
 * @param {number} b  蓝色分量
 * @param {string} type 调色板类型
 * @returns {object} 匹配的颜色对象
 */
function findClosestColorRed(r, g, b, type) {
    if (type === "blackWhiteColor") {
        let luminance = 0.299 * Math.min(255, Math.max(0, r)) +
                        0.587 * Math.min(255, Math.max(0, g)) +
                        0.114 * Math.min(255, Math.max(0, b));
        return luminance < _epdBWThreshold
            ? { r: 0, g: 0, b: 0, value: 0 }
            : { r: 255, g: 255, b: 255, value: 1 };
    }
    if (type === "gray4Color") {
        return findClosestGray4Color(r, g, b);
    }

    let lut = _getColorLUT(type);
    let shift = 8 - _LUT_BITS;  // 3
    let ri = Math.min(_LUT_SIZE - 1, Math.max(0, (Math.min(255, Math.max(0, r)) >> shift)));
    let gi = Math.min(_LUT_SIZE - 1, Math.max(0, (Math.min(255, Math.max(0, g)) >> shift)));
    let bi = Math.min(_LUT_SIZE - 1, Math.max(0, (Math.min(255, Math.max(0, b)) >> shift)));
    let index = (ri * _LUT_SIZE + gi) * _LUT_SIZE + bi;
    return lut[index];
}



// ==================== 边缘检测（用于自适应抖动强度）====================
function computeEdgeMap(imageData) {
    const width = imageData.width, height = imageData.height;
    const data = imageData.data;
    const edge = new Float32Array(width * height);
    const lum = new Float32Array(width * height);
    for (let i = 0; i < width * height; i++) {
        const idx = i * 4;
        lum[i] = 0.2126 * _srgbToLinear[data[idx]] + 0.7152 * _srgbToLinear[data[idx + 1]] + 0.0722 * _srgbToLinear[data[idx + 2]];
    }
    let maxEdge = 0;
    for (let y = 1; y < height - 1; y++) {
        for (let x = 1; x < width - 1; x++) {
            const idx = y * width + x;
            const gx = -lum[idx - width - 1] + lum[idx - width + 1] -
                        2 * lum[idx - 1] + 2 * lum[idx + 1] -
                        lum[idx + width - 1] + lum[idx + width + 1];
            const gy = -lum[idx - width - 1] - 2 * lum[idx - width] - lum[idx - width + 1] +
                        lum[idx + width - 1] + 2 * lum[idx + width] + lum[idx + width + 1];
            const mag = Math.sqrt(gx * gx + gy * gy);
            edge[idx] = mag;
            if (mag > maxEdge) maxEdge = mag;
        }
    }
    if (maxEdge > 0) {
        const inv = 1 / maxEdge;
        for (let i = 0; i < edge.length; i++) edge[i] *= inv;
    }
    return edge;
}

function otsuEdgeThreshold(edgeMap) {
    const hist = new Uint32Array(256);
    let total = 0;
    for (let i = 0; i < edgeMap.length; i++) {
        if (edgeMap[i] > 0.001) {
            const idx = Math.min(255, Math.floor(256 * edgeMap[i]));
            hist[idx]++;
            total++;
        }
    }
    if (total < 100) return 0.3;
    let sum = 0;
    for (let i = 0; i < 256; i++) sum += i * hist[i];
    let sumB = 0, wB = 0, wF, sumF, varMax = 0, threshold = 0;
    for (let i = 0; i < 256; i++) {
        wB += hist[i];
        if (wB === 0) continue;
        wF = total - wB;
        if (wF === 0) break;
        sumB += i * hist[i];
        const meanB = sumB / wB;
        const meanF = (sum - sumB) / wF;
        const between = wB * wF * (meanB - meanF) * (meanB - meanF);
        if (between > varMax) {
            varMax = between;
            threshold = i;
        }
    }
    return Math.max(0.1, Math.min(0.6, (threshold + 0.5) / 256));
}

// ==================== 误差扩散内核 ====================
const FLOYD_STEINBERG_KERNEL = [[1, 0, 7/16], [-1, 1, 3/16], [0, 1, 5/16], [1, 1, 1/16]];
const ATKINSON_KERNEL = [[1, 0, 1/8], [2, 0, 1/8], [-1, 1, 1/8], [0, 1, 1/8], [1, 1, 1/8], [0, 2, 1/8]];
const STUCKI_KERNEL = [
    [1,0,8/42], [2,0,4/42], [-2,1,2/42], [-1,1,4/42], [0,1,8/42], [1,1,4/42], [2,1,2/42],
    [-2,2,1/42], [-1,2,2/42], [0,2,4/42], [1,2,2/42], [2,2,1/42]
];
const JARVIS_KERNEL = [
    [1,0,7/48], [2,0,5/48], [-2,1,3/48], [-1,1,5/48], [0,1,7/48], [1,1,5/48], [2,1,3/48],
    [-2,2,1/48], [-1,2,3/48], [0,2,5/48], [1,2,3/48], [2,2,1/48]
];
// ===== 新增：更精细的误差扩散内核 =====
/**
 * Sierra Lite 抖动内核 (Sierra-2-4A 的简化版)
 * 系数：2/4, 1/4, 1/4 的简化分配
 * 特点：计算量小，但细节保留好，是Floyd-Steinberg的良好替代
 */
let SIERRA_LITE_KERNEL = [[1, 0, 2 / 4], // 当前行右侧
[-1, 1, 1 / 4], // 下一行左侧
[0, 1, 1 / 4]// 下一行中间
];

/**
 * Burkes 抖动内核
 * 是 Stucki 内核的简化版本，去掉了最远的几个系数
 * 系数分母为 32，比 Stucki 的 42 更简单，计算更快
 */
let BURKES_KERNEL = [[1, 0, 8 / 32], [2, 0, 4 / 32], [-2, 1, 2 / 32], [-1, 1, 4 / 32], [0, 1, 8 / 32], [1, 1, 4 / 32], [2, 1, 2 / 32]];

/**
 * Two-Row Sierra 抖动内核 (Sierra-3)
 * 介于 Sierra Lite 和 Stucki 之间的复杂度，共两行系数
 */
let TWO_ROW_SIERRA_KERNEL = [[1, 0, 4 / 16], [2, 0, 3 / 16], [-2, 1, 1 / 16], [-1, 1, 2 / 16], [0, 1, 3 / 16], [1, 1, 2 / 16], [2, 1, 1 / 16]];

/**
 * Ostromoukhov 抖动内核 (简化的变体)
 * 基于 Ostromoukhov 论文的自适应阈值思想，这里使用一个固定但优化的内核
 * 特点：在减少特定纹理的视觉伪影方面表现优异
 */
let OSTROMOUKHOV_KERNEL = [[1, 0, 0.5], // 系数和为1，分配更平均
[0, 1, 0.3], [1, 1, 0.2]];

/**
 * Linear Light Sierra Lite (在线性光空间优化的 Sierra Lite)
 * 结合了线性光处理的优势，使误差扩散更符合人眼感知
 * 内核系数与 Sierra Lite 相同，但在函数中特殊处理
 */
let LINEAR_LIGHT_SIERRA_KERNEL = SIERRA_LITE_KERNEL;
// 复用内核，通过特殊标记处理

/**
 * 通用误差扩散抖动函数
 */
function errorDiffusionDither(imageData, strength, colorMode, kernel) {
    const width = imageData.width, height = imageData.height;
    const data = imageData.data;
    const pixelCount = width * height;
    const edgeMap = computeEdgeMap(imageData);
    const edgeThresh = otsuEdgeThreshold(edgeMap);

    // 转换为线性光缓冲区
    const linearR = new Float32Array(pixelCount);
    const linearG = new Float32Array(pixelCount);
    const linearB = new Float32Array(pixelCount);
    for (let i = 0; i < pixelCount; i++) {
        const idx = i * 4;
        linearR[i] = _srgbToLinear[data[idx]];
        linearG[i] = _srgbToLinear[data[idx + 1]];
        linearB[i] = _srgbToLinear[data[idx + 2]];
    }

    const kLen = kernel.length;
    // 蛇形扫描（减少方向性伪影）
    for (let y = 0; y < height; y++) {
        const reverse = (y & 1) === 1;
        const xStart = reverse ? width - 1 : 0;
        const xEnd = reverse ? -1 : width;
        const step = reverse ? -1 : 1;
        for (let x = xStart; x !== xEnd; x += step) {
            const idx = y * width + x;
            const r8 = _linearToSrgb[Math.min(4095, Math.max(0, 4095 * linearR[idx] + 0.5) | 0)];
            const g8 = _linearToSrgb[Math.min(4095, Math.max(0, 4095 * linearG[idx] + 0.5) | 0)];
            const b8 = _linearToSrgb[Math.min(4095, Math.max(0, 4095 * linearB[idx] + 0.5) | 0)];
            const closest = inkNfcHeavyInk ? findClosestColorRed(r8, g8, b8, colorMode) : findClosestColor(r8, g8, b8, colorMode);
            const outIdx = idx * 4;
            data[outIdx] = closest.r;
            data[outIdx + 1] = closest.g;
            data[outIdx + 2] = closest.b;

            const edgeStrength = edgeMap[idx];
            let adaptiveStrength = strength;
            if (edgeStrength > edgeThresh) {
                adaptiveStrength = 0.15 + 0.85 * (1 - (edgeStrength - edgeThresh) / (1 - edgeThresh));
            }

            const errR = (linearR[idx] - _srgbToLinear[closest.r]) * adaptiveStrength;
            const errG = (linearG[idx] - _srgbToLinear[closest.g]) * adaptiveStrength;
            const errB = (linearB[idx] - _srgbToLinear[closest.b]) * adaptiveStrength;

            // 限制误差范围（防止溢出）
            const maxErr = 0.3 + 0.4 * (1 - 4 * (Math.max(0, Math.min(1, linearR[idx])) - 0.5) ** 2);
            const clipErr = (e) => Math.abs(e) > maxErr ? maxErr * Math.tanh(e / maxErr) : e;
            const errRc = clipErr(errR);
            const errGc = clipErr(errG);
            const errBc = clipErr(errB);

            for (let k = 0; k < kLen; k++) {
                const dx = reverse ? -kernel[k][0] : kernel[k][0];
                const dy = kernel[k][1];
                const nx = x + dx;
                const ny = y + dy;
                if (nx >= 0 && nx < width && ny >= 0 && ny < height) {
                    const nIdx = ny * width + nx;
                    const weight = kernel[k][2];
                    linearR[nIdx] += errRc * weight;
                    linearG[nIdx] += errGc * weight;
                    linearB[nIdx] += errBc * weight;
                }
            }
        }
    }
    return imageData;
}

// ==================== 具体抖动算法实现 ====================
function sierraLiteDither(imageData, strength, colorMode) {
    return errorDiffusionDither(imageData, strength, colorMode, SIERRA_LITE_KERNEL);
}
function burkesDither(imageData, strength, colorMode) {
    return errorDiffusionDither(imageData, strength, colorMode, BURKES_KERNEL);
}
function twoRowSierraDither(imageData, strength, colorMode) {
    return errorDiffusionDither(imageData, strength, colorMode, TWO_ROW_SIERRA_KERNEL);
}
function ostromoukhovDither(imageData, strength, colorMode) {
    return errorDiffusionDither(imageData, strength, colorMode, OSTROMOUKHOV_KERNEL);
}


function floydSteinbergDither(imageData, strength, mode) {
  const width = imageData.width;
  const height = imageData.height;
  const data = imageData.data;
  const tempData = new Uint8ClampedArray(data);

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const idx = (y * width + x) * 4;
      const r = tempData[idx];
      const g = tempData[idx + 1];
      const b = tempData[idx + 2];

      const closest = inkNfcHeavyInk ? findClosestColorRed(r, g, b, mode) : findClosestColor(r, g, b, mode);

      const errR = (r - closest.r) * strength;
      const errG = (g - closest.g) * strength;
      const errB = (b - closest.b) * strength;

      if (x + 1 < width) {
        const idxRight = idx + 4;
        tempData[idxRight] = Math.min(255, Math.max(0, tempData[idxRight] + errR * 7 / 16));
        tempData[idxRight + 1] = Math.min(255, Math.max(0, tempData[idxRight + 1] + errG * 7 / 16));
        tempData[idxRight + 2] = Math.min(255, Math.max(0, tempData[idxRight + 2] + errB * 7 / 16));
      }
      if (y + 1 < height) {
        if (x > 0) {
          const idxDownLeft = idx + width * 4 - 4;
          tempData[idxDownLeft] = Math.min(255, Math.max(0, tempData[idxDownLeft] + errR * 3 / 16));
          tempData[idxDownLeft + 1] = Math.min(255, Math.max(0, tempData[idxDownLeft + 1] + errG * 3 / 16));
          tempData[idxDownLeft + 2] = Math.min(255, Math.max(0, tempData[idxDownLeft + 2] + errB * 3 / 16));
        }
        const idxDown = idx + width * 4;
        tempData[idxDown] = Math.min(255, Math.max(0, tempData[idxDown] + errR * 5 / 16));
        tempData[idxDown + 1] = Math.min(255, Math.max(0, tempData[idxDown + 1] + errG * 5 / 16));
        tempData[idxDown + 2] = Math.min(255, Math.max(0, tempData[idxDown + 2] + errB * 5 / 16));
        if (x + 1 < width) {
          const idxDownRight = idx + width * 4 + 4;
          tempData[idxDownRight] = Math.min(255, Math.max(0, tempData[idxDownRight] + errR * 1 / 16));
          tempData[idxDownRight + 1] = Math.min(255, Math.max(0, tempData[idxDownRight + 1] + errG * 1 / 16));
          tempData[idxDownRight + 2] = Math.min(255, Math.max(0, tempData[idxDownRight + 2] + errB * 1 / 16));
        }
      }
    }
  }

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const idx = (y * width + x) * 4;
      const r = tempData[idx];
      const g = tempData[idx + 1];
      const b = tempData[idx + 2];

      const closest = inkNfcHeavyInk ? findClosestColorRed(r, g, b, mode) : findClosestColor(r, g, b, mode);
      data[idx] = closest.r;
      data[idx + 1] = closest.g;
      data[idx + 2] = closest.b;
    }
  }

  return imageData;
}

function atkinsonDither(imageData, strength, mode) {
  const width = imageData.width;
  const height = imageData.height;
  const data = imageData.data;
  const tempData = new Uint8ClampedArray(data);

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const idx = (y * width + x) * 4;
      const r = tempData[idx];
      const g = tempData[idx + 1];
      const b = tempData[idx + 2];

      const closest = inkNfcHeavyInk ? findClosestColorRed(r, g, b, mode) : findClosestColor(r, g, b, mode);

      data[idx] = closest.r;
      data[idx + 1] = closest.g;
      data[idx + 2] = closest.b;

      const errR = (r - closest.r) * strength;
      const errG = (g - closest.g) * strength;
      const errB = (b - closest.b) * strength;

      const fraction = 1 / 8;

      if (x + 1 < width) {
        const idxRight = idx + 4;
        tempData[idxRight] = Math.min(255, Math.max(0, tempData[idxRight] + errR * fraction));
        tempData[idxRight + 1] = Math.min(255, Math.max(0, tempData[idxRight + 1] + errG * fraction));
        tempData[idxRight + 2] = Math.min(255, Math.max(0, tempData[idxRight + 2] + errB * fraction));
      }
      if (x + 2 < width) {
        const idxRight2 = idx + 8;
        tempData[idxRight2] = Math.min(255, Math.max(0, tempData[idxRight2] + errR * fraction));
        tempData[idxRight2 + 1] = Math.min(255, Math.max(0, tempData[idxRight2 + 1] + errG * fraction));
        tempData[idxRight2 + 2] = Math.min(255, Math.max(0, tempData[idxRight2 + 2] + errB * fraction));
      }
      if (y + 1 < height) {
        if (x > 0) {
          const idxDownLeft = idx + width * 4 - 4;
          tempData[idxDownLeft] = Math.min(255, Math.max(0, tempData[idxDownLeft] + errR * fraction));
          tempData[idxDownLeft + 1] = Math.min(255, Math.max(0, tempData[idxDownLeft + 1] + errG * fraction));
          tempData[idxDownLeft + 2] = Math.min(255, Math.max(0, tempData[idxDownLeft + 2] + errB * fraction));
        }
        const idxDown = idx + width * 4;
        tempData[idxDown] = Math.min(255, Math.max(0, tempData[idxDown] + errR * fraction));
        tempData[idxDown + 1] = Math.min(255, Math.max(0, tempData[idxDown + 1] + errG * fraction));
        tempData[idxDown + 2] = Math.min(255, Math.max(0, tempData[idxDown + 2] + errB * fraction));
        if (x + 1 < width) {
          const idxDownRight = idx + width * 4 + 4;
          tempData[idxDownRight] = Math.min(255, Math.max(0, tempData[idxDownRight] + errR * fraction));
          tempData[idxDownRight + 1] = Math.min(255, Math.max(0, tempData[idxDownRight + 1] + errG * fraction));
          tempData[idxDownRight + 2] = Math.min(255, Math.max(0, tempData[idxDownRight + 2] + errB * fraction));
        }
      }
      if (y + 2 < height) {
        const idxDown2 = idx + width * 8;
        tempData[idxDown2] = Math.min(255, Math.max(0, tempData[idxDown2] + errR * fraction));
        tempData[idxDown2 + 1] = Math.min(255, Math.max(0, tempData[idxDown2 + 1] + errG * fraction));
        tempData[idxDown2 + 2] = Math.min(255, Math.max(0, tempData[idxDown2 + 2] + errB * fraction));
      }
    }
  }

  return imageData;
}

function stuckiDither(imageData, strength, mode) {
  // 执行Stucki错误扩散算法以处理图像
  const width = imageData.width;
  const height = imageData.height;
  const data = imageData.data;
  const tempData = new Uint8ClampedArray(data);

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const idx = (y * width + x) * 4;
      const r = tempData[idx];
      const g = tempData[idx + 1];
      const b = tempData[idx + 2];

      const closest = inkNfcHeavyInk ? findClosestColorRed(r, g, b, mode) : findClosestColor(r, g, b, mode);

      const errR = (r - closest.r) * strength;
      const errG = (g - closest.g) * strength;
      const errB = (b - closest.b) * strength;

      const divisor = 42;

      if (x + 1 < width) {
        const idxRight = idx + 4;
        tempData[idxRight] = Math.min(255, Math.max(0, tempData[idxRight] + errR * 8 / divisor));
        tempData[idxRight + 1] = Math.min(255, Math.max(0, tempData[idxRight + 1] + errG * 8 / divisor));
        tempData[idxRight + 2] = Math.min(255, Math.max(0, tempData[idxRight + 2] + errB * 8 / divisor));
      }
      if (x + 2 < width) {
        const idxRight2 = idx + 8;
        tempData[idxRight2] = Math.min(255, Math.max(0, tempData[idxRight2] + errR * 4 / divisor));
        tempData[idxRight2 + 1] = Math.min(255, Math.max(0, tempData[idxRight2 + 1] + errG * 4 / divisor));
        tempData[idxRight2 + 2] = Math.min(255, Math.max(0, tempData[idxRight2 + 2] + errB * 4 / divisor));
      }
      if (y + 1 < height) {
        if (x > 1) {
          const idxDownLeft2 = idx + width * 4 - 8;
          tempData[idxDownLeft2] = Math.min(255, Math.max(0, tempData[idxDownLeft2] + errR * 2 / divisor));
          tempData[idxDownLeft2 + 1] = Math.min(255, Math.max(0, tempData[idxDownLeft2 + 1] + errG * 2 / divisor));
          tempData[idxDownLeft2 + 2] = Math.min(255, Math.max(0, tempData[idxDownLeft2 + 2] + errB * 2 / divisor));
        }
        if (x > 0) {
          const idxDownLeft = idx + width * 4 - 4;
          tempData[idxDownLeft] = Math.min(255, Math.max(0, tempData[idxDownLeft] + errR * 4 / divisor));
          tempData[idxDownLeft + 1] = Math.min(255, Math.max(0, tempData[idxDownLeft + 1] + errG * 4 / divisor));
          tempData[idxDownLeft + 2] = Math.min(255, Math.max(0, tempData[idxDownLeft + 2] + errB * 4 / divisor));
        }
        const idxDown = idx + width * 4;
        tempData[idxDown] = Math.min(255, Math.max(0, tempData[idxDown] + errR * 8 / divisor));
        tempData[idxDown + 1] = Math.min(255, Math.max(0, tempData[idxDown + 1] + errG * 8 / divisor));
        tempData[idxDown + 2] = Math.min(255, Math.max(0, tempData[idxDown + 2] + errB * 8 / divisor));
        if (x + 1 < width) {
          const idxDownRight1 = idx + width * 4 + 4;
          tempData[idxDownRight1] = Math.min(255, Math.max(0, tempData[idxDownRight1] + errR * 4 / divisor));
          tempData[idxDownRight1 + 1] = Math.min(255, Math.max(0, tempData[idxDownRight1 + 1] + errG * 4 / divisor));
          tempData[idxDownRight1 + 2] = Math.min(255, Math.max(0, tempData[idxDownRight1 + 2] + errB * 4 / divisor));
        }
        if (x + 2 < width) {
          const idxDownRight2 = idx + width * 4 + 8;
          tempData[idxDownRight2] = Math.min(255, Math.max(0, tempData[idxDownRight2] + errR * 2 / divisor));
          tempData[idxDownRight2 + 1] = Math.min(255, Math.max(0, tempData[idxDownRight2 + 1] + errG * 2 / divisor));
          tempData[idxDownRight2 + 2] = Math.min(255, Math.max(0, tempData[idxDownRight2 + 2] + errB * 2 / divisor));
        }
      }
      if (y + 2 < height) {
        if (x > 1) {
          const idxDown2Left2 = idx + width * 8 - 8;
          tempData[idxDown2Left2] = Math.min(255, Math.max(0, tempData[idxDown2Left2] + errR * 1 / divisor));
          tempData[idxDown2Left2 + 1] = Math.min(255, Math.max(0, tempData[idxDown2Left2 + 1] + errG * 1 / divisor));
          tempData[idxDown2Left2 + 2] = Math.min(255, Math.max(0, tempData[idxDown2Left2 + 2] + errB * 1 / divisor));
        }
        if (x > 0) {
          const idxDown2Left = idx + width * 8 - 4;
          tempData[idxDown2Left] = Math.min(255, Math.max(0, tempData[idxDown2Left] + errR * 2 / divisor));
          tempData[idxDown2Left + 1] = Math.min(255, Math.max(0, tempData[idxDown2Left + 1] + errG * 2 / divisor));
          tempData[idxDown2Left + 2] = Math.min(255, Math.max(0, tempData[idxDown2Left + 2] + errB * 2 / divisor));
        }
        const idxDown2 = idx + width * 8;
        tempData[idxDown2] = Math.min(255, Math.max(0, tempData[idxDown2] + errR * 4 / divisor));
        tempData[idxDown2 + 1] = Math.min(255, Math.max(0, tempData[idxDown2 + 1] + errG * 4 / divisor));
        tempData[idxDown2 + 2] = Math.min(255, Math.max(0, tempData[idxDown2 + 2] + errB * 4 / divisor));
        if (x + 1 < width) {
          const idxDown2Right = idx + width * 8 + 4;
          tempData[idxDown2Right] = Math.min(255, Math.max(0, tempData[idxDown2Right] + errR * 2 / divisor));
          tempData[idxDown2Right + 1] = Math.min(255, Math.max(0, tempData[idxDown2Right + 1] + errG * 2 / divisor));
          tempData[idxDown2Right + 2] = Math.min(255, Math.max(0, tempData[idxDown2Right + 2] + errB * 2 / divisor));
        }
        if (x + 2 < width) {
          const idxDown2Right2 = idx + width * 8 + 8;
          tempData[idxDown2Right2] = Math.min(255, Math.max(0, tempData[idxDown2Right2] + errR * 1 / divisor));
          tempData[idxDown2Right2 + 1] = Math.min(255, Math.max(0, tempData[idxDown2Right2 + 1] + errG * 1 / divisor));
          tempData[idxDown2Right2 + 2] = Math.min(255, Math.max(0, tempData[idxDown2Right2 + 2] + errB * 1 / divisor));
        }
      }
    }
  }

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const idx = (y * width + x) * 4;
      const r = tempData[idx];
      const g = tempData[idx + 1];
      const b = tempData[idx + 2];

      const closest = inkNfcHeavyInk ? findClosestColorRed(r, g, b, mode) : findClosestColor(r, g, b, mode);
      data[idx] = closest.r;
      data[idx + 1] = closest.g;
      data[idx + 2] = closest.b;
    }
  }

  return imageData;
}

function jarvisDither(imageData, strength, mode) {
  const width = imageData.width;
  const height = imageData.height;
  const data = imageData.data;
  const tempData = new Uint8ClampedArray(data);

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const idx = (y * width + x) * 4;
      const r = tempData[idx];
      const g = tempData[idx + 1];
      const b = tempData[idx + 2];

      const closest = inkNfcHeavyInk ? findClosestColorRed(r, g, b, mode) : findClosestColor(r, g, b, mode);

      data[idx] = closest.r;
      data[idx + 1] = closest.g;
      data[idx + 2] = closest.b;

      const errR = (r - closest.r) * strength;
      const errG = (g - closest.g) * strength;
      const errB = (b - closest.b) * strength;

      const divisor = 48;

      if (x + 1 < width) {
        const idxRight = idx + 4;
        tempData[idxRight] = Math.min(255, Math.max(0, tempData[idxRight] + errR * 7 / divisor));
        tempData[idxRight + 1] = Math.min(255, Math.max(0, tempData[idxRight + 1] + errG * 7 / divisor));
        tempData[idxRight + 2] = Math.min(255, Math.max(0, tempData[idxRight + 2] + errB * 7 / divisor));
      }
      if (x + 2 < width) {
        const idxRight2 = idx + 8;
        tempData[idxRight2] = Math.min(255, Math.max(0, tempData[idxRight2] + errR * 5 / divisor));
        tempData[idxRight2 + 1] = Math.min(255, Math.max(0, tempData[idxRight2 + 1] + errG * 5 / divisor));
        tempData[idxRight2 + 2] = Math.min(255, Math.max(0, tempData[idxRight2 + 2] + errB * 5 / divisor));
      }
      if (y + 1 < height) {
        if (x > 1) {
          const idxDownLeft2 = idx + width * 4 - 8;
          tempData[idxDownLeft2] = Math.min(255, Math.max(0, tempData[idxDownLeft2] + errR * 3 / divisor));
          tempData[idxDownLeft2 + 1] = Math.min(255, Math.max(0, tempData[idxDownLeft2 + 1] + errG * 3 / divisor));
          tempData[idxDownLeft2 + 2] = Math.min(255, Math.max(0, tempData[idxDownLeft2 + 2] + errB * 3 / divisor));
        }
        if (x > 0) {
          const idxDownLeft = idx + width * 4 - 4;
          tempData[idxDownLeft] = Math.min(255, Math.max(0, tempData[idxDownLeft] + errR * 5 / divisor));
          tempData[idxDownLeft + 1] = Math.min(255, Math.max(0, tempData[idxDownLeft + 1] + errG * 5 / divisor));
          tempData[idxDownLeft + 2] = Math.min(255, Math.max(0, tempData[idxDownLeft + 2] + errB * 5 / divisor));
        }
        const idxDown = idx + width * 4;
        tempData[idxDown] = Math.min(255, Math.max(0, tempData[idxDown] + errR * 7 / divisor));
        tempData[idxDown + 1] = Math.min(255, Math.max(0, tempData[idxDown + 1] + errG * 7 / divisor));
        tempData[idxDown + 2] = Math.min(255, Math.max(0, tempData[idxDown + 2] + errB * 7 / divisor));
        if (x + 1 < width) {
          const idxDownRight = idx + width * 4 + 4;
          tempData[idxDownRight] = Math.min(255, Math.max(0, tempData[idxDownRight] + errR * 5 / divisor));
          tempData[idxDownRight + 1] = Math.min(255, Math.max(0, tempData[idxDownRight + 1] + errG * 5 / divisor));
          tempData[idxDownRight + 2] = Math.min(255, Math.max(0, tempData[idxDownRight + 2] + errB * 5 / divisor));
        }
        if (x + 2 < width) {
          const idxDownRight2 = idx + width * 4 + 8;
          tempData[idxDownRight2] = Math.min(255, Math.max(0, tempData[idxDownRight2] + errR * 3 / divisor));
          tempData[idxDownRight2 + 1] = Math.min(255, Math.max(0, tempData[idxDownRight2 + 1] + errG * 3 / divisor));
          tempData[idxDownRight2 + 2] = Math.min(255, Math.max(0, tempData[idxDownRight2 + 2] + errB * 3 / divisor));
        }
      }
      if (y + 2 < height) {
        if (x > 1) {
          const idxDown2Left2 = idx + width * 8 - 8;
          tempData[idxDown2Left2] = Math.min(255, Math.max(0, tempData[idxDown2Left2] + errR * 1 / divisor));
          tempData[idxDown2Left2 + 1] = Math.min(255, Math.max(0, tempData[idxDown2Left2 + 1] + errG * 1 / divisor));
          tempData[idxDown2Left2 + 2] = Math.min(255, Math.max(0, tempData[idxDown2Left2 + 2] + errB * 1 / divisor));
        }
        if (x > 0) {
          const idxDown2Left = idx + width * 8 - 4;
          tempData[idxDown2Left] = Math.min(255, Math.max(0, tempData[idxDown2Left] + errR * 3 / divisor));
          tempData[idxDown2Left + 1] = Math.min(255, Math.max(0, tempData[idxDown2Left + 1] + errG * 3 / divisor));
          tempData[idxDown2Left + 2] = Math.min(255, Math.max(0, tempData[idxDown2Left + 2] + errB * 3 / divisor));
        }
        const idxDown2 = idx + width * 8;
        tempData[idxDown2] = Math.min(255, Math.max(0, tempData[idxDown2] + errR * 5 / divisor));
        tempData[idxDown2 + 1] = Math.min(255, Math.max(0, tempData[idxDown2 + 1] + errG * 5 / divisor));
        tempData[idxDown2 + 2] = Math.min(255, Math.max(0, tempData[idxDown2 + 2] + errB * 5 / divisor));
        if (x + 1 < width) {
          const idxDown2Right = idx + width * 8 + 4;
          tempData[idxDown2Right] = Math.min(255, Math.max(0, tempData[idxDown2Right] + errR * 3 / divisor));
          tempData[idxDown2Right + 1] = Math.min(255, Math.max(0, tempData[idxDown2Right + 1] + errG * 3 / divisor));
          tempData[idxDown2Right + 2] = Math.min(255, Math.max(0, tempData[idxDown2Right + 2] + errB * 3 / divisor));
        }
        if (x + 2 < width) {
          const idxDown2Right2 = idx + width * 8 + 8;
          tempData[idxDown2Right2] = Math.min(255, Math.max(0, tempData[idxDown2Right2] + errR * 1 / divisor));
          tempData[idxDown2Right2 + 1] = Math.min(255, Math.max(0, tempData[idxDown2Right2 + 1] + errG * 1 / divisor));
          tempData[idxDown2Right2 + 2] = Math.min(255, Math.max(0, tempData[idxDown2Right2 + 2] + errB * 1 / divisor));
        }
      }
    }
  }

  return imageData;
}

function bayerDither(imageData, strength, mode) {
  const width = imageData.width;
  const height = imageData.height;
  const data = imageData.data;

  // 8x8 Bayer matrix (normalized to 0-1 range)
  const bayerMatrix = [
    [0, 32, 8, 40, 2, 34, 10, 42],
    [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38],
    [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41],
    [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37],
    [63, 31, 55, 23, 61, 29, 53, 21]
  ];

  const matrixSize = 8;
  const maxThreshold = 64;

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const idx = (y * width + x) * 4;
      const r = data[idx];
      const g = data[idx + 1];
      const b = data[idx + 2];

      // Get threshold from Bayer matrix
      const matrixX = x % matrixSize;
      const matrixY = y % matrixSize;
      const threshold = (bayerMatrix[matrixY][matrixX] / maxThreshold) * 255;

      // Apply dithering with strength factor
      const adjustedR = r + (threshold - 127.5) * strength;
      const adjustedG = g + (threshold - 127.5) * strength;
      const adjustedB = b + (threshold - 127.5) * strength;

      // Clamp values
      const clampedR = Math.min(255, Math.max(0, adjustedR));
      const clampedG = Math.min(255, Math.max(0, adjustedG));
      const clampedB = Math.min(255, Math.max(0, adjustedB));

      // Find closest color in palette
      const closest = inkNfcHeavyInk ? findClosestColorRed(clampedR, clampedG, clampedB, mode) : findClosestColor(clampedR, clampedG, clampedB, mode);

      data[idx] = closest.r;
      data[idx + 1] = closest.g;
      data[idx + 2] = closest.b;
    }
  }

  return imageData;
}

/**
 * 线性光 Sierra Lite（在线性光空间处理）
 */
function linearLightSierraDither(imageData, strength, colorMode) {
    const width = imageData.width, height = imageData.height;
    const data = imageData.data;
    const pixelCount = width * height;
    const linearR = new Float32Array(pixelCount);
    const linearG = new Float32Array(pixelCount);
    const linearB = new Float32Array(pixelCount);
    for (let i = 0; i < pixelCount; i++) {
        const idx = i * 4;
        linearR[i] = _srgbToLinear[data[idx]];
        linearG[i] = _srgbToLinear[data[idx + 1]];
        linearB[i] = _srgbToLinear[data[idx + 2]];
    }
    const tempData = new Uint8ClampedArray(data.length);
    for (let i = 0; i < pixelCount; i++) {
        const idx = i * 4;
        tempData[idx] = Math.min(255, Math.max(0, Math.round(linearR[i] * 255)));
        tempData[idx + 1] = Math.min(255, Math.max(0, Math.round(linearG[i] * 255)));
        tempData[idx + 2] = Math.min(255, Math.max(0, Math.round(linearB[i] * 255)));
        tempData[idx + 3] = data[idx + 3];
    }
    const tempImageData = createLegacyImageData(tempData, width, height);
    const result = errorDiffusionDither(tempImageData, strength, colorMode, SIERRA_LITE_KERNEL);
    return result;
}


// ==================== 蓝噪声抖动 ====================
const BLUE_NOISE_64 = new Uint16Array([
    2048,3584,512,3072,1536,4000,256,2816,1024,3840,768,2560,1792,4064,128,3328,
    640,2304,1280,3712,384,2176,896,3456,1664,3200,448,2688,1408,3904,64,2944,
    1920,3136,960,2432,1728,3520,192,2048,1152,3648,576,2880,1600,3072,832,2240,
    3968,480,1344,2752,704,3264,1088,3776,320,1472,2624,3392,896,2112,1856,3584,
    1568,2496,128,3904,1216,2368,3648,640,1920,3456,1024,2176,3840,256,1664,2944,
    768,3200,2048,576,1792,3072,448,2688,1344,3136,1792,384,2560,1088,3520,512,
    2816,1408,3712,960,2240,3392,1536,832,2880,192,3776,704,2432,1600,3264,2752,
    3968,320,1152,2624,1856,3584,64,2112,1472,3328,576,2304,1024,3648,896,1280,
    2176,3840,640,1728,3456,256,2496,1088,3904,768,2816,1344,3072,2560,448,3200,
    1920,384,2944,512,2048,3136,1536,2688,3712,128,1664,3520,832,2368,1792,3392,
    1152,3648,2368,960,3264,1408,704,3968,1856,480,2240,3776,320,1472,2752,576,
    2624,192,1600,3520,768,2816,1280,3072,2112,1024,3456,640,2432,1088,3840,1920,
    3392,896,2176,3136,448,1856,3648,256,1536,2880,3200,1728,384,2560,3712,128,
    1344,2496,3904,1664,512,2304,3328,832,2048,3584,576,1408,2688,960,3072,2240,
    3768,704,1152,2752,3456,1088,2624,192,1792,3136,768,2432,1600,3904,320,1472,
    2880,3200,1920,384,2112,3712,640,1344,2944,3520,448,2176,1024,3648,2560,832
]);
const _blueNoiseSize = 64;
let _blueNoiseTile = null;

function getBlueNoiseTile() {
    if (_blueNoiseTile) return _blueNoiseTile;
    const size = _blueNoiseSize;
    const tile = new Float32Array(size * size);
    const len = BLUE_NOISE_64.length;
    for (let i = 0; i < size * size; i++) {
        let val = 2654435761 * i + BLUE_NOISE_64[i % len];
        val = 73244475 * ((val >>> 16) ^ val);
        val = 73244475 * ((val >>> 16) ^ val);
        val ^= val >>> 16;
        tile[i] = (val & 0xFFFF) / 65535;
    }
    // 简单优化
    _blueNoiseTile = tile;
    return tile;
}

function blueNoiseDither(imageData, strength, colorMode) {
    const width = imageData.width, height = imageData.height;
    const data = imageData.data;
    const tile = getBlueNoiseTile();
    const size = _blueNoiseSize;
    const edgeMap = computeEdgeMap(imageData);
    const edgeThresh = otsuEdgeThreshold(edgeMap);
    for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
            const idx = (y * width + x) * 4;
            const r = _srgbToLinear[data[idx]];
            const g = _srgbToLinear[data[idx + 1]];
            const b = _srgbToLinear[data[idx + 2]];
            const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
            const adaptive = 1 - 4 * (Math.max(0, Math.min(1, lum)) - 0.5) ** 2;
            const edgeWeight = edgeMap[y * width + x];
            const noise = (tile[(y % size) * size + (x % size)] - 0.5) * strength *
                          (0.4 + 0.6 * adaptive) *
                          (edgeWeight > edgeThresh ? 0.15 + 0.85 * (1 - (edgeWeight - edgeThresh) / (1 - edgeThresh)) : 1);
            const rn = Math.min(255, Math.max(0, _linearToSrgb[Math.min(4095, (4095 * (r + noise) + 0.5) | 0)]));
            const gn = Math.min(255, Math.max(0, _linearToSrgb[Math.min(4095, (4095 * (g + noise) + 0.5) | 0)]));
            const bn = Math.min(255, Math.max(0, _linearToSrgb[Math.min(4095, (4095 * (b + noise) + 0.5) | 0)]));
            const closest = inkNfcHeavyInk ? findClosestColorRed(rn, gn, bn, colorMode) : findClosestColor(rn, gn, bn, colorMode);
            data[idx] = closest.r;
            data[idx + 1] = closest.g;
            data[idx + 2] = closest.b;
        }
    }
    return imageData;
}

// ==================== Riemersma 抖动（希尔伯特曲线）====================
function hilbertCurve(n) {
    const points = [];
    const total = n * n;
    for (let i = 0; i < total; i++) {
        let x = 0, y = 0, s = 1;
        let t = i;
        while (s < n) {
            const rx = 1 & (t >> 1);
            const ry = 1 & (t ^ rx);
            if (rx === 0 && ry === 1) {
                [x, y] = [s - 1 - x, s - 1 - y];
            }
            if (rx === 1 && ry === 1) {
                [x, y] = [x, s - 1 - y];
            }
            if (rx === 1 && ry === 0) {
                [x, y] = [s - 1 - x, y];
            }
            x += s * rx;
            y += s * ry;
            s <<= 1;
            t >>= 2;
        }
        points.push([x, y]);
    }
    return points;
}

function riemersmaDither(imageData, strength, colorMode) {
    const width = imageData.width, height = imageData.height;
    const data = imageData.data;
    const pixelCount = width * height;
    const maxDim = Math.max(width, height);
    let power = 1;
    while (power < maxDim) power <<= 1;
    const curve = hilbertCurve(power);
    const order = [];
    for (const [x, y] of curve) {
        if (x < width && y < height) order.push(y * width + x);
    }

    const edgeMap = computeEdgeMap(imageData);
    const edgeThresh = otsuEdgeThreshold(edgeMap);
    const linearR = new Float32Array(pixelCount);
    const linearG = new Float32Array(pixelCount);
    const linearB = new Float32Array(pixelCount);
    for (let i = 0; i < pixelCount; i++) {
        const idx = i * 4;
        linearR[i] = _srgbToLinear[data[idx]];
        linearG[i] = _srgbToLinear[data[idx + 1]];
        linearB[i] = _srgbToLinear[data[idx + 2]];
    }

    const decay = 1 / Math.E;
    const weights = new Float32Array(16);
    let sumW = 0;
    for (let i = 0; i < 16; i++) {
        weights[i] = Math.pow(decay, i);
        sumW += weights[i];
    }
    for (let i = 0; i < 16; i++) weights[i] /= sumW;

    const errRQueue = new Float32Array(16);
    const errGQueue = new Float32Array(16);
    const errBQueue = new Float32Array(16);
    let queuePos = 0;

    for (let i = 0; i < order.length; i++) {
        const idx = order[i];
        let errR = 0, errG = 0, errB = 0;
        for (let j = 0; j < 16; j++) {
            const pos = (queuePos - j + 16) % 16;
            errR += errRQueue[pos] * weights[j];
            errG += errGQueue[pos] * weights[j];
            errB += errBQueue[pos] * weights[j];
        }
        const r = linearR[idx] + errR;
        const g = linearG[idx] + errG;
        const b = linearB[idx] + errB;
        const r8 = _linearToSrgb[Math.min(4095, Math.max(0, 4095 * r + 0.5) | 0)];
        const g8 = _linearToSrgb[Math.min(4095, Math.max(0, 4095 * g + 0.5) | 0)];
        const b8 = _linearToSrgb[Math.min(4095, Math.max(0, 4095 * b + 0.5) | 0)];
        const closest = inkNfcHeavyInk ? findClosestColorRed(r8, g8, b8, colorMode) : findClosestColor(r8, g8, b8, colorMode);
        const outIdx = idx * 4;
        data[outIdx] = closest.r;
        data[outIdx + 1] = closest.g;
        data[outIdx + 2] = closest.b;

        const edgeWeight = edgeMap[idx];
        let adaptiveStrength = strength;
        if (edgeWeight > edgeThresh) {
            adaptiveStrength = 0.15 + 0.85 * (1 - (edgeWeight - edgeThresh) / (1 - edgeThresh));
        }

        const errRCurr = (r - _srgbToLinear[closest.r]) * adaptiveStrength;
        const errGCurr = (g - _srgbToLinear[closest.g]) * adaptiveStrength;
        const errBCurr = (b - _srgbToLinear[closest.b]) * adaptiveStrength;

        const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
        const clampLimit = 0.3 + 0.4 * (1 - 4 * (Math.max(0, Math.min(1, lum)) - 0.5) ** 2);
        const clamp = (v) => Math.abs(v) > clampLimit ? clampLimit * Math.tanh(v / clampLimit) : v;
        errRQueue[queuePos] = clamp(errRCurr);
        errGQueue[queuePos] = clamp(errGCurr);
        errBQueue[queuePos] = clamp(errBCurr);
        queuePos = (queuePos + 1) % 16;
    }
    return imageData;
}

// ==================== 混合抖动 ====================
function hybridDither(imageData, strength, colorMode) {
    // 先做蓝噪声预处理，再做误差扩散
    const width = imageData.width, height = imageData.height;
    const data = imageData.data;
    const tile = getBlueNoiseTile();
    const size = _blueNoiseSize;
    const edgeMap = computeEdgeMap(imageData);
    const edgeThresh = otsuEdgeThreshold(edgeMap);
    const variance = new Float32Array(width * height);
    // 计算局部方差
    const lum = new Float32Array(width * height);
    for (let i = 0; i < width * height; i++) {
        const idx = i * 4;
        lum[i] = 0.2126 * _srgbToLinear[data[idx]] + 0.7152 * _srgbToLinear[data[idx + 1]] + 0.0722 * _srgbToLinear[data[idx + 2]];
    }
    let maxVar = 0;
    for (let y = 1; y < height - 1; y++) {
        for (let x = 1; x < width - 1; x++) {
            const idx = y * width + x;
            let sum = 0, sum2 = 0, cnt = 0;
            for (let dy = -1; dy <= 1; dy++) {
                for (let dx = -1; dx <= 1; dx++) {
                    const nidx = (y + dy) * width + (x + dx);
                    const v = lum[nidx];
                    sum += v;
                    sum2 += v * v;
                    cnt++;
                }
            }
            const mean = sum / cnt;
            const varVal = sum2 / cnt - mean * mean;
            variance[idx] = varVal;
            if (varVal > maxVar) maxVar = varVal;
        }
    }
    if (maxVar > 0) {
        const inv = 1 / maxVar;
        for (let i = 0; i < variance.length; i++) variance[i] *= inv;
    }

    // 蓝噪声预调制
    const preR = new Float32Array(width * height);
    const preG = new Float32Array(width * height);
    const preB = new Float32Array(width * height);
    for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
            const idx = y * width + x;
            const lumVal = lum[idx];
            const adapt = 0.5 + 0.5 * (1 - 4 * (Math.max(0, Math.min(1, lumVal)) - 0.5) ** 2);
            const noiseStrength = (0.2 + 0.3 * variance[idx]) * strength * 0.5;
            const noise1 = (tile[(y % size) * size + (x % size)] - 0.5) * noiseStrength;
            const noise2 = (tile[((y + 17) % size) * size + ((x + 31) % size)] - 0.5) * noiseStrength;
            const noise3 = (tile[((y + 37) % size) * size + ((x + 13) % size)] - 0.5) * noiseStrength;
            preR[idx] = _srgbToLinear[data[idx * 4]] + noise1;
            preG[idx] = _srgbToLinear[data[idx * 4 + 1]] + noise2;
            preB[idx] = _srgbToLinear[data[idx * 4 + 2]] + noise3;
        }
    }
    // 误差扩散
    const tempData = new Uint8ClampedArray(data.length);
    for (let i = 0; i < width * height; i++) {
        const idx = i * 4;
        tempData[idx] = _linearToSrgb[Math.min(4095, Math.max(0, 4095 * preR[i] + 0.5) | 0)];
        tempData[idx + 1] = _linearToSrgb[Math.min(4095, Math.max(0, 4095 * preG[i] + 0.5) | 0)];
        tempData[idx + 2] = _linearToSrgb[Math.min(4095, Math.max(0, 4095 * preB[i] + 0.5) | 0)];
        tempData[idx + 3] = data[idx + 3];
    }
    const tempImageData = createLegacyImageData(tempData, width, height);
    const result = errorDiffusionDither(tempImageData, strength, colorMode, FLOYD_STEINBERG_KERNEL);
    return result;
}


//重色模式使用的函数如下:
// ==================== 图像增强函数 ====================

/**
 * USM 锐化（Unsharp Mask）
 * @param {ImageData} imageData  图像数据
 * @param {number} amount        锐化强度 (0 ~ 若干)
 * @param {number} radius        模糊半径（像素）
 * @returns {ImageData} 处理后的图像数据
 */
function unsharpMask(imageData, amount, radius) {
    if (amount <= 0) return imageData;

    const width = imageData.width;
    const height = imageData.height;
    const data = imageData.data;
    const totalPixels = width * height;

    // 亮度通道数组
    let luminance = new Float32Array(totalPixels);
    for (let i = 0; i < totalPixels; i++) {
        let r = _srgbToLinear[data[i * 4]];
        let g = _srgbToLinear[data[i * 4 + 1]];
        let b = _srgbToLinear[data[i * 4 + 2]];
        luminance[i] = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    }

    let blurred = new Float32Array(luminance);
    let temp = new Float32Array(totalPixels);
    let kernelSize = Math.max(1, Math.round(radius));

    // 水平模糊
    for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
            let sum = 0, count = 0;
            for (let dx = -kernelSize; dx <= kernelSize; dx++) {
                let nx = x + dx;
                if (nx >= 0 && nx < width) {
                    sum += blurred[y * width + nx];
                    count++;
                }
            }
            temp[y * width + x] = sum / count;
        }
    }

    // 垂直模糊
    for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
            let sum = 0, count = 0;
            for (let dy = -kernelSize; dy <= kernelSize; dy++) {
                let ny = y + dy;
                if (ny >= 0 && ny < height) {
                    sum += temp[ny * width + x];
                    count++;
                }
            }
            blurred[y * width + x] = sum / count;
        }
    }

    // 计算局部对比度（标准差）作为锐化掩码
    let contrast = new Float32Array(totalPixels);
    let maxContrast = 0;
    for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
            let sum = 0, sumSq = 0, count = 0;
            for (let dy = -1; dy <= 1; dy++) {
                for (let dx = -1; dx <= 1; dx++) {
                    let ny = y + dy, nx = x + dx;
                    if (ny >= 0 && ny < height && nx >= 0 && nx < width) {
                        let val = luminance[ny * width + nx];
                        sum += val;
                        sumSq += val * val;
                        count++;
                    }
                }
            }
            let mean = sum / count;
            let variance = sumSq / count - mean * mean;
            contrast[y * width + x] = variance;
            if (variance > maxContrast) maxContrast = variance;
        }
    }

    if (maxContrast > 0) {
        let invMax = 1 / maxContrast;
        for (let i = 0; i < totalPixels; i++) {
            let c = contrast[i] * invMax;
            contrast[i] = Math.min(1, Math.max(0, (c - 0.05) / 0.15));
        }
    }

    // 应用锐化
    for (let i = 0; i < totalPixels; i++) {
        let sharp = (luminance[i] - blurred[i]) * amount * contrast[i];
        let r = _srgbToLinear[data[i * 4]] + sharp;
        let g = _srgbToLinear[data[i * 4 + 1]] + sharp;
        let b = _srgbToLinear[data[i * 4 + 2]] + sharp;
        data[i * 4] = _linearToSrgb[Math.min(4095, Math.max(0, 4095 * r + 0.5) | 0)];
        data[i * 4 + 1] = _linearToSrgb[Math.min(4095, Math.max(0, 4095 * g + 0.5) | 0)];
        data[i * 4 + 2] = _linearToSrgb[Math.min(4095, Math.max(0, 4095 * b + 0.5) | 0)];
    }
    return imageData;
}

/**
 * 分析图像：平均亮度、标准差、平均饱和度、动态范围
 * @param {ImageData} imageData 图像数据
 * @returns {object} 分析结果
 */
function analyzeImage(imageData) {
    const data = imageData.data;
    const pixelCount = data.length / 4;
    if (pixelCount === 0) {
        return { meanY: 0.5, stdY: 0.2, meanSat: 0.3, dynamicRange: 1 };
    }

    let sumY = 0, sumY2 = 0, sumSat = 0;
    let histogram = new Uint32Array(256);

    for (let i = 0; i < data.length; i += 4) {
        let rLin = _srgbToLinear[data[i]];
        let gLin = _srgbToLinear[data[i + 1]];
        let bLin = _srgbToLinear[data[i + 2]];
        let y = 0.2126 * rLin + 0.7152 * gLin + 0.0722 * bLin;
        sumY += y;
        sumY2 += y * y;

        let ySrgb = _linearToSrgb[Math.min(4095, Math.max(0, 4095 * y + 0.5) | 0)];
        histogram[ySrgb]++;

        let maxC = Math.max(data[i], data[i + 1], data[i + 2]);
        let minC = Math.min(data[i], data[i + 1], data[i + 2]);
        if (maxC > 0) {
            sumSat += (maxC - minC) / maxC;
        }
    }

    let meanY = sumY / pixelCount;
    let stdY = Math.sqrt(Math.max(0, sumY2 / pixelCount - meanY * meanY));
    let meanSat = sumSat / pixelCount;

    // 动态范围（去掉前后 2% 的极端值）
    let threshold = Math.floor(0.02 * pixelCount);
    let acc = 0, low = 0;
    for (let i = 0; i < 256; i++) {
        acc += histogram[i];
        if (acc >= threshold) { low = i; break; }
    }
    acc = 0; let high = 255;
    for (let i = 255; i >= 0; i--) {
        acc += histogram[i];
        if (acc >= threshold) { high = i; break; }
    }
    let dynamicRange = Math.max(0.01, (high - low) / 255);

    return { meanY, stdY, meanSat, dynamicRange };
}

/**
 * 自适应伽马校正
 * @param {ImageData} imageData 图像数据
 * @returns {ImageData} 处理后的图像数据
 */
function adaptiveGammaCorrection(imageData) {
    const data = imageData.data;
    const pixelCount = data.length / 4;
    if (pixelCount === 0) return imageData;

    // 亮度直方图
    let histogram = new Uint32Array(256);
    let sumLuma = 0;
    for (let i = 0; i < data.length; i += 4) {
        let rLin = _srgbToLinear[data[i]];
        let gLin = _srgbToLinear[data[i + 1]];
        let bLin = _srgbToLinear[data[i + 2]];
        let y = 0.2126 * rLin + 0.7152 * gLin + 0.0722 * bLin;
        sumLuma += y;
        let ySrgb = _linearToSrgb[Math.min(4095, Math.max(0, 4095 * y + 0.5) | 0)];
        histogram[ySrgb]++;
    }

    // 剔除 0.5% 的暗部和亮部，确定有效范围
    let threshold = Math.floor(0.005 * pixelCount);
    let acc = 0, low = 0;
    for (let i = 0; i < 256; i++) {
        acc += histogram[i];
        if (acc >= threshold) { low = i; break; }
    }
    acc = 0; let high = 255;
    for (let i = 255; i >= 0; i--) {
        acc += histogram[i];
        if (acc >= threshold) { high = i; break; }
    }

    let range = high - low;
    let useContrastStretch = (range >= 20);  // 动态范围足够才做对比度拉伸

    let meanLuma = sumLuma / pixelCount;
    let meanSrgb = _linearToSrgb[Math.min(4095, Math.max(0, 4095 * meanLuma + 0.5) | 0)];
    let gamma = 1.0;
    if (meanSrgb > 1 && meanSrgb < 254) {
        gamma = Math.log(0.5) / Math.log(meanSrgb / 255);
        gamma = Math.min(1.8, Math.max(0.5, gamma));
    }

    // 构建查找表
    let lut = new Uint8Array(256);
    for (let i = 0; i < 256; i++) {
        let v = i;
        if (useContrastStretch) {
            v = Math.min(255, Math.max(0, Math.round((i - low) / range * 255)));
        }
        lut[i] = Math.round(255 * Math.pow(v / 255, gamma));
    }

    // 应用
    for (let i = 0; i < data.length; i++) {
        data[i] = lut[data[i]];
    }
    return imageData;
}

/**
 * 饱和度增强（HSV 方式）
 * @param {ImageData} imageData 图像数据
 * @param {number} factor      饱和度倍数（>1 增加，<1 减少）
 * @returns {ImageData} 处理后的图像数据
 */
function boostSaturation(imageData, factor) {
    if (factor <= 1) return imageData;

    const data = imageData.data;
    for (let i = 0; i < data.length; i += 4) {
        let r = data[i] / 255;
        let g = data[i + 1] / 255;
        let b = data[i + 2] / 255;

        let maxC = Math.max(r, g, b);
        let minC = Math.min(r, g, b);
        let delta = maxC - minC;
        let lightness = (maxC + minC) / 2;

        if (delta === 0) continue;  // 灰色，无法增强

        let saturation = lightness < 0.5 ? delta / (maxC + minC) : delta / (2 - maxC - minC);
        let hue;
        if (maxC === r) {
            hue = ((g - b) / delta + (g < b ? 6 : 0)) / 6;
        } else if (maxC === g) {
            hue = ((b - r) / delta + 2) / 6;
        } else {
            hue = ((r - g) / delta + 4) / 6;
        }

        saturation = Math.min(1, saturation * factor);
        // 转换回 RGB
        let chroma = (1 - Math.abs(2 * lightness - 1)) * saturation;
        let huePrime = hue * 6;
        let x = chroma * (1 - Math.abs(huePrime % 2 - 1));
        let r1, g1, b1;
        if (huePrime < 1) { r1 = chroma; g1 = x; b1 = 0; }
        else if (huePrime < 2) { r1 = x; g1 = chroma; b1 = 0; }
        else if (huePrime < 3) { r1 = 0; g1 = chroma; b1 = x; }
        else if (huePrime < 4) { r1 = 0; g1 = x; b1 = chroma; }
        else if (huePrime < 5) { r1 = x; g1 = 0; b1 = chroma; }
        else { r1 = chroma; g1 = 0; b1 = x; }

        let m = lightness - chroma / 2;
        r = r1 + m;
        g = g1 + m;
        b = b1 + m;

        data[i] = Math.round(255 * r);
        data[i + 1] = Math.round(255 * g);
        data[i + 2] = Math.round(255 * b);
    }
    return imageData;
}

/**
 * CLAHE 增强（限制对比度自适应直方图均衡化）
 * @param {ImageData} imageData  图像数据
 * @param {number} clipLimit     对比度限制因子
 * @param {number} tileSize      分块大小（例如 8）
 * @returns {ImageData} 处理后的图像数据
 */
function claheEnhance(imageData, clipLimit, tileSize) {
    const width = imageData.width;
    const height = imageData.height;
    const data = imageData.data;

    if (width < 4 * tileSize || height < 4 * tileSize) return imageData;

    const pixelCount = width * height;
    let luminance = new Uint8Array(pixelCount);
    for (let i = 0; i < pixelCount; i++) {
        let r = data[i * 4];
        let g = data[i * 4 + 1];
        let b = data[i * 4 + 2];
        luminance[i] = Math.round(0.299 * r + 0.587 * g + 0.114 * b);
    }

    let tileW = Math.ceil(width / tileSize);
    let tileH = Math.ceil(height / tileSize);
    let tiles = [];

    // 为每个 tile 构建 CLAHE 映射表
    for (let ty = 0; ty < tileSize; ty++) {
        tiles[ty] = [];
        for (let tx = 0; tx < tileSize; tx++) {
            let x0 = tx * tileW;
            let y0 = ty * tileH;
            let x1 = Math.min(width, x0 + tileW);
            let y1 = Math.min(height, y0 + tileH);
            let tilePixels = (x1 - x0) * (y1 - y0);
            if (tilePixels === 0) {
                // 空块，直接返回恒等映射
                tiles[ty][tx] = new Uint8Array(256).map((_, i) => i);
                continue;
            }

            // 直方图
            let hist = new Uint32Array(256);
            for (let y = y0; y < y1; y++) {
                for (let x = x0; x < x1; x++) {
                    hist[luminance[y * width + x]]++;
                }
            }

            // 限制对比度
            let limit = Math.max(1, Math.floor(clipLimit * tilePixels / 256));
            let totalExcess = 0;
            for (let i = 0; i < 256; i++) {
                if (hist[i] > limit) {
                    totalExcess += hist[i] - limit;
                    hist[i] = limit;
                }
            }

            // 平均分配超出部分
            let addEach = Math.floor(totalExcess / 256);
            let remainder = totalExcess - 256 * addEach;
            for (let i = 0; i < 256; i++) {
                hist[i] += addEach + (i < remainder ? 1 : 0);
            }

            // 累积分布 -> 映射表
            let cdf = new Uint8Array(256);
            let sum = 0;
            let maxVal = Math.max(1, tilePixels - 1);
            for (let i = 0; i < 256; i++) {
                sum += hist[i];
                cdf[i] = Math.min(255, Math.round((sum - 1) / maxVal * 255));
            }
            tiles[ty][tx] = cdf;
        }
    }

    // 双线性插值应用映射
    for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
            let fx = (x + 0.5) / tileW - 0.5;
            let fy = (y + 0.5) / tileH - 0.5;
            let tx1 = Math.max(0, Math.floor(fx));
            let ty1 = Math.max(0, Math.floor(fy));
            let tx2 = Math.min(tileSize - 1, tx1 + 1);
            let ty2 = Math.min(tileSize - 1, ty1 + 1);
            let u = Math.max(0, Math.min(1, fx - tx1));
            let v = Math.max(0, Math.min(1, fy - ty1));

            let lum = luminance[y * width + x];
            let val = (1 - u) * (1 - v) * tiles[ty1][tx1][lum] +
                      u * (1 - v) * tiles[ty1][tx2][lum] +
                      (1 - u) * v * tiles[ty2][tx1][lum] +
                      u * v * tiles[ty2][tx2][lum];

            let origLum = luminance[y * width + x];
            let idx = 4 * (y * width + x);
            if (origLum < 8) {
                // 极暗区域，用差值增强
                let diff = Math.max(0, val - origLum);
                data[idx] = Math.min(255, data[idx] + diff);
                data[idx + 1] = Math.min(255, data[idx + 1] + diff);
                data[idx + 2] = Math.min(255, data[idx + 2] + diff);
            } else {
                let ratio = Math.min(2.5, val / origLum);
                let r = data[idx] * ratio;
                let g = data[idx + 1] * ratio;
                let b = data[idx + 2] * ratio;
                let maxVal = Math.max(r, g, b);
                if (maxVal > 255) {
                    let over = (maxVal - 255) / (maxVal - val + 0.001);
                    r += over * (val - r);
                    g += over * (val - g);
                    b += over * (val - b);
                }
                data[idx] = Math.min(255, Math.max(0, Math.round(r)));
                data[idx + 1] = Math.min(255, Math.max(0, Math.round(g)));
                data[idx + 2] = Math.min(255, Math.max(0, Math.round(b)));
            }
        }
    }
    return imageData;
}
//重色模式使用函数结束

// ==================== 主抖动分发函数 ====================
function ditherImage(imageData, alg, strength, colorMode, options = {}) {
    inkNfcHeavyInk = Boolean(options.heavyInk);
    const gray4Darken = Number(options.gray4Darken);
    inkNfcGray4Darken = Number.isFinite(gray4Darken) ? Math.max(0, Math.min(1, gray4Darken)) : 0.35;

    if(inkNfcHeavyInk){ //使用重色模式, 使画面更接近于水墨画, 就是红的更红, 黑的更黑
      // 预处理：自适应伽马、CLAHE、反锐化掩模、饱和度增强
      const analysis = analyzeImage(imageData);
      adaptiveGammaCorrection(imageData);
      const claheStrength = 1 + 1.5 * Math.max(0, 1 - analysis.dynamicRange);
      claheEnhance(imageData, claheStrength, 8);
      const unsharpStrength = 0.3 + 0.5 * Math.max(0, Math.min(1, 1 - 4 * analysis.stdY));
      unsharpMask(imageData, unsharpStrength, 1);
      if (colorMode === "fourColor" || colorMode === "sixColor") {
        boostSaturation(imageData, 1.1 + 0.4 * Math.max(0, Math.min(1, 1 - 2 * analysis.meanSat)));
      } else if (colorMode === "threeColor") {
        boostSaturation(imageData, 1.05 + 0.2 * Math.max(0, Math.min(1, 1 - 2 * analysis.meanSat)));
      }
    }

    // 根据算法选择
    switch (alg) {
        case "floydSteinberg": return floydSteinbergDither(imageData, strength, colorMode);
        case "atkinson": return atkinsonDither(imageData, strength, colorMode);
        case "stucki": return stuckiDither(imageData, strength, colorMode);
        case "jarvis": return jarvisDither(imageData, strength, colorMode);
        case "bayer": return bayerDither(imageData, strength, colorMode);
        case "blueNoise": return blueNoiseDither(imageData, strength, colorMode);
        case "riemersma": return riemersmaDither(imageData, strength, colorMode);
        case "hybrid": return hybridDither(imageData, strength, colorMode);
        case "sierraLite": return sierraLiteDither(imageData, strength, colorMode);
        case "burkes": return burkesDither(imageData, strength, colorMode);
        case "twoRowSierra": return twoRowSierraDither(imageData, strength, colorMode);
        case "ostromoukhov": return ostromoukhovDither(imageData, strength, colorMode);
        case "linearLightSierra": return linearLightSierraDither(imageData, strength, colorMode);
        default: return imageData;
    }
}


function decodeProcessedData(processedData, width, height, mode) {
  const imageData = createLegacyImageData(new Uint8ClampedArray(width * height * 4), width, height);
  const data = imageData.data;

  if (mode === 'sixColor') {
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const newIndex = (x * height) + (height - 1 - y);
        const value = processedData[newIndex];
        const color = rgbPalette.find(c => c.value === value) || rgbPalette[5]; // 默认白色
        const index = (y * width + x) * 4;
        data[index] = color.r;
        data[index + 1] = color.g;
        data[index + 2] = color.b;
        data[index + 3] = 255; // Alpha 透明度
      }
    }
  } else if (mode === 'fourColor') {
    const fourColorValues = [
      { value: 0x00, r: 0, g: 0, b: 0 },      // 黑色
      { value: 0x01, r: 255, g: 255, b: 255 }, // 白色
      { value: 0x03, r: 255, g: 0, b: 0 },     // 红色
      { value: 0x02, r: 255, g: 255, b: 0 }    // 黄色
    ];
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const newIndex = (y * width + x) / 4 | 0;
        const shift = 6 - ((x % 4) * 2);
        const value = (processedData[newIndex] >> shift) & 0x03;
        const color = fourColorValues.find(c => c.value === value) || fourColorValues[1]; // 默认白色
        const index = (y * width + x) * 4;
        data[index] = color.r;
        data[index + 1] = color.g;
        data[index + 2] = color.b;
        data[index + 3] = 255;
      }
    }
  } else if (mode === 'gray4Color') {
    const byteWidth = Math.ceil(width / 8);
    const highData = processedData.slice(0, byteWidth * height);
    const lowData = processedData.slice(byteWidth * height);
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const byteIndex = y * byteWidth + Math.floor(x / 8);
        const bitIndex = 7 - (x % 8);
        const level = (((highData[byteIndex] >> bitIndex) & 1) << 1) | ((lowData[byteIndex] >> bitIndex) & 1);
        const color = gray4Palette[level] || gray4Palette[3];
        const index = (y * width + x) * 4;
        data[index] = color.r;
        data[index + 1] = color.g;
        data[index + 2] = color.b;
        data[index + 3] = 255;
      }
    }
  } else if (mode === 'blackWhiteColor') {
    const byteWidth = Math.ceil(width / 8);
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const byteIndex = y * byteWidth + Math.floor(x / 8);
        const bitIndex = 7 - (x % 8);
        const bit = (processedData[byteIndex] >> bitIndex) & 1;
        const index = (y * width + x) * 4;
        data[index] = bit ? 255 : 0; // 白或黑
        data[index + 1] = bit ? 255 : 0;
        data[index + 2] = bit ? 255 : 0;
        data[index + 3] = 255;
      }
    }
  } else if (mode === 'threeColor') {
    const byteWidth = Math.ceil(width / 8);
    const blackWhiteData = processedData.slice(0, byteWidth * height);
    const redWhiteData = processedData.slice(byteWidth * height);
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const byteIndex = y * byteWidth + Math.floor(x / 8);
        const bitIndex = 7 - (x % 8);
        const blackWhiteBit = (blackWhiteData[byteIndex] >> bitIndex) & 1;
        const redWhiteBit = (redWhiteData[byteIndex] >> bitIndex) & 1;
        const index = (y * width + x) * 4;
        if (!redWhiteBit) {
          // 红色
          data[index] = 255;
          data[index + 1] = 0;
          data[index + 2] = 0;
        } else {
          // 黑或白
          data[index] = blackWhiteBit ? 255 : 0;
          data[index + 1] = blackWhiteBit ? 255 : 0;
          data[index + 2] = blackWhiteBit ? 255 : 0;
        }
        data[index + 3] = 255;
      }
    }
  }

  return imageData;
}

function processImageData(imageData, mode) {
  const width = imageData.width;
  const height = imageData.height;
  const data = imageData.data;

  let processedData;

  if (mode === 'sixColor') {
    processedData = new Uint8Array(width * height);
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const index = (y * width + x) * 4;
        const r = data[index];
        const g = data[index + 1];
        const b = data[index + 2];

        //const closest = document.getElementById('useLegacyDither').checked?findClosestColorRed(r, g, b, mode):findClosestColor(r, g, b, mode);
        const closest = findClosestColor(r, g, b, mode);
        const newIndex = (x * height) + (height - 1 - y);
        processedData[newIndex] = closest.value;
      }
    }
  } else if (mode === 'fourColor') {
    processedData = new Uint8Array(Math.ceil((width * height) / 4));
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const index = (y * width + x) * 4;
        const r = data[index];
        const g = data[index + 1];
        const b = data[index + 2];
        //const closest = document.getElementById('useLegacyDither').checked?findClosestColorRed(r, g, b, mode):findClosestColor(r, g, b, mode); // 使用 fourColorPalette
        const closest = findClosestColor(r, g, b, mode); // 使用 fourColorPalette
        const colorValue = closest.value; // 0x00 (黑), 0x01 (白), 0x02 (红), 0x03 (黄)
        const newIndex = (y * width + x) / 4 | 0;
        const shift = 6 - ((x % 4) * 2);
        processedData[newIndex] |= (colorValue << shift);
      }
    }
  } else if (mode === 'gray4Color') {
    const byteWidth = Math.ceil(width / 8);
    const highData = new Uint8Array(height * byteWidth);
    const lowData = new Uint8Array(height * byteWidth);

    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const index = (y * width + x) * 4;
        const r = data[index];
        const g = data[index + 1];
        const b = data[index + 2];
        const closest = findClosestColor(r, g, b, mode);
        const level = closest.value & 0x03;
        const byteIndex = y * byteWidth + Math.floor(x / 8);
        const bitIndex = 7 - (x % 8);
        highData[byteIndex] |= ((level >> 1) & 0x01) << bitIndex;
        lowData[byteIndex] |= (level & 0x01) << bitIndex;
      }
    }

    processedData = new Uint8Array(highData.length + lowData.length);
    processedData.set(highData, 0);
    processedData.set(lowData, highData.length);
  } else if (mode === 'blackWhiteColor') {
    const byteWidth = Math.ceil(width / 8);
    processedData = new Uint8Array(byteWidth * height);
    const threshold = 140;

    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const index = (y * width + x) * 4;
        const r = data[index];
        const g = data[index + 1];
        const b = data[index + 2];
        const grayscale = Math.round(0.299 * r + 0.587 * g + 0.114 * b);
        const bit = grayscale >= threshold ? 1 : 0;
        const byteIndex = y * byteWidth + Math.floor(x / 8);
        const bitIndex = 7 - (x % 8);
        processedData[byteIndex] |= (bit << bitIndex);
      }
    }
  } else if (mode === 'threeColor') {
    const byteWidth = Math.ceil(width / 8);
    const blackWhiteThreshold = 140;
    const redThreshold = 160;

    const blackWhiteData = new Uint8Array(height * byteWidth);
    const redWhiteData = new Uint8Array(height * byteWidth);

    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const index = (y * width + x) * 4;
        const r = data[index];
        const g = data[index + 1];
        const b = data[index + 2];
        const grayscale = Math.round(0.299 * r + 0.587 * g + 0.114 * b);

        const blackWhiteBit = grayscale >= blackWhiteThreshold ? 1 : 0;
        const blackWhiteByteIndex = y * byteWidth + Math.floor(x / 8);
        const blackWhiteBitIndex = 7 - (x % 8);
        if (blackWhiteBit) {
          blackWhiteData[blackWhiteByteIndex] |= (0x01 << blackWhiteBitIndex);
        } else {
          blackWhiteData[blackWhiteByteIndex] &= ~(0x01 << blackWhiteBitIndex);
        }

        const redWhiteBit = (r > redThreshold && r > g && r > b) ? 0 : 1;
        const redWhiteByteIndex = y * byteWidth + Math.floor(x / 8);
        const redWhiteBitIndex = 7 - (x % 8);
        if (redWhiteBit) {
          redWhiteData[redWhiteByteIndex] |= (0x01 << redWhiteBitIndex);
        } else {
          redWhiteData[redWhiteByteIndex] &= ~(0x01 << redWhiteBitIndex);
        }
      }
    }

    processedData = new Uint8Array(blackWhiteData.length + redWhiteData.length);
    processedData.set(blackWhiteData, 0);
    processedData.set(redWhiteData, blackWhiteData.length);
  }

  return processedData;
}

export { adjustContrast, decodeProcessedData, ditherImage, processImageData };
