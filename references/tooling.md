# 工具与验收

脚本使用 Python 3.10+ 和 Pillow。优先使用本技能目录下 `.venv/bin/python` 或已有且能导入 Pillow 的环境。其他环境在项目临时目录创建虚拟环境并安装技能目录内的 `requirements.txt`。以下命令的 `$skill` 是技能目录，`$py` 是选用的 Python。

## 转换与渲染

```sh
"$py" "$skill/scripts/media_tools.py" normalize 参考图 正面.jpg 斜侧.HEIC 全身.png
"$py" "$skill/scripts/media_tools.py" cut-sheet 连续姿势.png 逐帧 --cols 4 --rows 2
"$py" "$skill/scripts/render_sticker.py" 动作.json '素材/动图/01 啊？.gif'
"$py" "$skill/scripts/media_tools.py" artwork 全身定妆.png 正面头像.png 素材/配图
"$py" "$skill/scripts/media_tools.py" preview 素材/动图 素材/预览图/整套动图.gif
```

- `normalize` 保留原文件，校正 EXIF 方向并生成临时 PNG。macOS 的 HEIC 转换通过 `sips`。遇到其他平台或失败的 HEIC 时，用宿主已有解码工具转换或请用户重新提供剩余必需的角度。
- `cut-sheet` 适用于透明背景的规则等分网格，按行、从左到右导出 240×240 PNG。用 `--box x1 y1 x2 y2` 指定整个网格的有效区域。若网格线、格距或姿势基线不齐，先检查并逐格按同一比例与地面坐标重新放置。别逐帧裁到主体包围盒。透明余量要留给耳、尾、抬爪和字幕。
- `artwork` 的两个输入是审核过的透明全身和正脸图。它生成无字的横幅、封面和图标。图标单独检查 50 像素阅读效果。输出目录需要尚未创建。
- 本机 macOS 可自动使用系统中文字体。其他宿主可用 `--font /path/to/font.ttf`。确认成片没有缺字和重影。标题和含义已在画面排好时可省略 `caption`。
- `preview` 读取包内已定稿 GIF 的实际帧延时，先按各自节奏播放一轮，短的先停在开始姿势。单独 GIF 自身无限循环。固定颜色表压缩时仍保留全部输入帧时间。若超过限制，优先缩短冗长动作或移除无关细节，重新解码复核。当前规则与默认字节数不同时，渲染和准备审查均可传 `--max-bytes` 或 `--max-sticker-bytes`。

### 一个确定的头部动作

`anchored-head` 从一张 240×240 透明 PNG 轻微变换头部，可做歪头、点头、摇头。按定妆的实际头颈位置选转轴和渐变区。坐姿低于 `lock_y` 的画面逐帧检查，编码后可见像素和透明轮廓保持一致。它适合小幅动作，真实大角度转头需要有依据的多个姿势。用小步长补追踪帧，避免把时长理解成每个关键姿势都要停留。

```json
{
  "mode": "anchored-head",
  "base": "坐姿.png",
  "pivot": [120, 88],
  "blend_top": 78,
  "lock_y": 97,
  "step_ms": 30,
  "caption": "啊？",
  "track": [
    {"at_ms": 0},
    {"at_ms": 100},
    {"at_ms": 360, "x": -2, "roll_deg": -12},
    {"at_ms": 520, "x": -2, "roll_deg": -12},
    {"at_ms": 850},
    {"at_ms": 1020}
  ]
}
```

`track` 每项是绝对时刻和相对于原姿势的位移。四个可选量为 `x`、`y`、`roll_deg`、小幅 `pitch`，未填视为 0。首尾关键姿势一致。实际侧面的嘴和耳朵保真优先于运动幅度。

### 多姿势动作

对张嘴、眨眼、走路、下趴、抬爪等动作先生成或裁出可信的连续姿势。为接地、离地、抬起、落地分别提供有对应的受力帧。若程序插帧能维持明确的像素或关节对应，可把插出的帧同样加入队列并检查。下面是文件格式示例，实际帧数和时长由动作决定。

```json
{
  "mode": "keyframes",
  "caption": "等一下",
  "frames": [
    {"file": "逐帧/001.png", "duration_ms": 90},
    {"file": "逐帧/002.png", "duration_ms": 40},
    {"file": "逐帧/003.png", "duration_ms": 120},
    {"file": "逐帧/002.png", "duration_ms": 40},
    {"file": "逐帧/001.png", "duration_ms": 60}
  ]
}
```

文件路径相对于动作 JSON。每个输入帧已是相同尺寸的透明画布。可为两种模式补充 `locked_boxes`，例如 `[[20,110,200,185]]`，用于检查确定要保持静止的部位。运动或合理遮挡区域不加对应断言。

## 逐帧审查与打包

```sh
"$py" "$skill/scripts/review_stickers.py" prepare 素材 工作/第1轮/review.json
"$py" "$skill/scripts/review_stickers.py" gate 素材 工作/第1轮/review.json
"$py" "$skill/scripts/review_stickers.py" pack 素材 工作/第1轮/review.json --zip 宠物表情包.zip
```

在 prepare 生成报告后才人工填写观察，然后再运行 gate 和 pack。工作目录放在交付素材外。

- 实际打开每张证据图，对照原照及动作规格检查所有解码帧；看关键处全尺寸，查看真实播放的速度与首尾连接。暗色背景也能暴露白边和重影。通过的图片为每个检查项写具体所见并记录实际审过的 `reviewed_frames`。非图片为文案顺序和含义词等写 `content`。
- `anatomy` 核对肢体连接、遮挡、承重和爪数，`identity` 核对五官、耳形和毛发，`text` 核对文案及小尺寸可读性，`motion` 核对实际节奏和循环，`framing` 核对边缘、留白及裁切。静态配图对应查看静态情况。
- 未检查为 `pending`，发现问题为 `fail`，逐项通过才写 `pass`。审查工具检查技术与记录完整性，视觉判断由实际看图的审查者做。
- 返修后使用新报告。确认仍是同一只宠物和同一套作品时，`prepare ... --reuse 工作/第1轮/review.json` 可以带入名称和摘要相同且已完整通过的文件。改动过的 GIF、预览和文案需重新核对。用户要求全套复查时照做。
- `pack` 只打包有完整审查证据的四个交付目录，核验 ZIP 内文件摘要与审查时一致。工具及审查流程继承自 [photo-to-wechat-stickers](https://github.com/y2675632584-hub/photo-to-wechat-stickers)，保留原 MIT 许可。
