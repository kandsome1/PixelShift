# PixelShift · 图片 / 视频特征码改写器

批量或单个改写图片、视频的**特征码**（文件哈希 + 感知指纹 + 元数据），带图形界面，全部在
本机离线完成，不联网、不上传任何文件。

![PixelShift 界面](docs/screenshot.png)

- 界面：Python + tkinter（纯原生，无 Electron、无浏览器内核）
- 图片处理：Pillow 解码 → 像素微扰 / 裁剪缩放 → 重编码 → 元数据重构
- 视频处理：调用本机 ffmpeg（重封装 / 重编码 / 滤镜 / 容器元数据）

---

## 一、快速开始

```bat
:: 1. 装依赖（只需一次）
python -m pip install -r requirements.txt

:: 2. 启动界面
双击  "启动 PixelShift.bat"
:: 或
python app.py
```

环境要求：

| 组件 | 要求 | 说明 |
|---|---|---|
| Python | 3.9+（本机验证 3.12.10） | 装 Python 时勾选 Add to PATH |
| Pillow | 9.1+ | `pip install Pillow` |
| tkinter | Python 自带 | 无需安装 |
| ffmpeg / ffprobe | 推荐 5.0+ | 处理视频必须；已自动在 PATH 与常见安装目录查找 |
| 拖入文件 | Windows 原生，**零依赖** | 直接子类化窗口过程接管 `WM_DROPFILES`，不用装任何包 |

> **批处理文件必须保持纯 ASCII**：cmd.exe 按系统 OEM 代码页（中文 Windows 是 GBK/936）
> 逐行解析 `.bat`，文件里一旦有 UTF-8 中文，字节会被错位解释、行被切断，
> 于是就会出现一堆 `'xxx' 不是内部或外部命令`。
> 所以 `启动 PixelShift.bat` 的提示文字全是英文，换行统一 CRLF。
> 如果你自己另存一份中文版，请保存为 **ANSI / GBK** 编码，不要用 UTF-8。

ffmpeg 没装时的表现：图片照常处理，视频会被跳过并提示。安装方式：
`winget install Gyan.FFmpeg` 或下载 [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 解压后把 `bin` 加进 PATH。

---

## 二、界面怎么用

```
┌ 顶栏：强度预设（轻度 / 标准 / 强力） + 添加文件 / 添加文件夹 / 移除 / 清空
├ 左：文件表格 —— 文件、类型、大小、原 MD5、新 MD5、状态（pHashΔ）
├ 右：三个设置页 —— 图片 / 视频 / 输出与运行
├ 中下：运行日志（可折叠）
└ 底部：进度条 + 统计 + 开始处理 / 停止
```

1. **加文件**：右上角「添加文件 / 添加文件夹」（文件夹会递归扫描子目录），或直接拖入窗口。
   列表会立刻显示每个文件的原始 MD5。
   只要处理一个文件时，用菜单 `文件 → 快速处理单个文件…`，选完直接开跑。
2. **选强度**：顶部预设一键套用，右侧面板里的每一项都可以再单独微调。
3. **选输出**：「输出与运行 → 输出目录」留空时，每个源文件旁边生成 `_pixelshift` 目录；
   也可以指定一个统一目录并保留层级。
4. **开始处理**：结果会回填到表格（新 MD5、状态、感知哈希差异），并在输出目录生成
   `PixelShift_报告_年月日_时分秒.csv`。

输出文件名默认与源文件相同（换目录），也可以勾上「文件名追加随机后缀」。
同名冲突有三种处理：自动改名（`_2`、`_3`…）、覆盖、跳过。

---

## 三、三种强度预设

| 预设 | 图片 | 视频 | 适用 |
|---|---|---|---|
| **轻度** | 重编码 q97 + 注入随机元数据，像素几乎不动 | `-c copy` 重封装 + 容器元数据 | 只想换掉文件字节哈希，速度最快 |
| **标准**（默认） | 像素微扰 25 + 色调 8 + 缩放过 1px + q95 重编码 + 元数据重构 | 视频重编码 crf20 + 极轻噪声 + 音频拷贝 | 日常推荐，肉眼无差 |
| **强力** | 居中裁切 1.5% + 噪声 45 + 色调 20 + 缩放往返 + q90 | 深度变形：缩放往返 + 噪声 + 调色 + 关键帧重排 + 音频重编码 | 需要把感知指纹也拉开 |

> 预设只是起点。右侧面板的「结构变形」组（裁切 %、旋转、镜像、变速）默认关闭，
> 它们是**唯一能显著拉开感知哈希**的手段，但会改变画面/时长，按需开启。

---

## 四、它到底改了什么

### 1. 文件层特征码（MD5 / SHA1 / 各种"特征码"检测）

**必然改变。** 图片重新编码 + 重写元数据、视频重新封装/编码，输出字节流与原文件完全不同。
面板和报告里会同时给出原 MD5 与新 MD5，可以逐条核对。

### 2. 感知层指纹（pHash / aHash / 平台查重用的画面指纹）

由这几件事叠加改变：

| 手段 | 影响 | 默认 |
|---|---|---|
| 像素微扰（随机挑 0.1~2% 像素通道 ±1~6） | 破坏直方图/统计特征 | 开（强度可调） |
| 缩放往返（缩 N px 再放大回原尺寸） | 打断重采样指纹 | 标准/强力 |
| 色调漂移（亮度/对比/饱和/gamma 亚 2% 随机） | 破坏颜色特征 | 标准及以上 |
| **居中裁切 N% 再放大回原尺寸** | 画面整体位移+放大，pHash 差异最大 | 强力 |
| 镜像 / 旋转 | 结构级改变 | 手动 |
| 视频噪声 + 缩放往返 + 关键帧重排 + 变速 | 帧级指纹变化 | 按模式 |

工具会对每张图片计算处理前后的 **pHash 汉明距离**并写进表格（`pHashΔ`）与报告：
64 位里差 0~5 位基本还算"同一张图"，差 15 位以上就属于明显不同的画面特征了。

### 3. 元数据

- 图片：可清空原始 EXIF/XMP/ICC，然后写入一套全新的随机元数据
  （相机品牌型号、软件名、拍摄时间、作者、图像描述、16 位指纹串），
  风格可选随机 / 单反 / 手机 / 扫描仪 / 极简。JPEG 额外写 COM 注释段，
  PNG 写入文本块，GIF 写入注释块。
- 视频：可清空原容器元数据，再写入随机 `comment` / `title` /
  `handler_name` / `creation_time` / `encoder`。MP4/MOV 还会重写
  `video_track_timescale` 并做 faststart 重排。

### 4. 免 ffmpeg 的容器级改写（metabox 模式）

视频的第四种模式，**完全不调用 ffmpeg**——纯 Python 解析 MP4/MOV 的 box 结构，只重写
`moov` 这棵子树：

```
moov
├── mvhd            ← 改写 creation_time / modification_time（1904 纪元）
├── trak/mdia/mdhd  ← 每个轨道同样改写时间戳
└── udta/meta/ilst  ← 重建 iTunes 标签：©too / ©nam / ©cmt / desc / ©day
```

源文件没有 `udta` 时会按规范新建一整套（`meta` + `hdlr(mdir/appl)` + `ilst` + `data`）。

| 项 | 说明 |
|---|---|
| 速度 | 秒级。只读 `moov`（几 KB~几 MB），`mdat` 按 4 MB 分块流式拷贝，几十 GB 也不吃内存 |
| 依赖性 | **零依赖**，机器上没装 ffmpeg 也能跑 |
| 画面 | 完全不碰，mdat 字节级一致（测试里逐字节比对过） |
| 时长 / 分辨率 / 码率 | 完全不变 |
| 文件哈希 | 必变（moov 重建 + 随机元数据） |
| 合规性 | 结构合法，ffprobe、播放器、剪辑软件都能正常解析 |

两种触发方式：

1. **主动选**：「视频 → 处理方式 → 仅改元数据 · 免 ffmpeg」
2. **自动降级**：勾选「缺 ffmpeg 时自动降级」，检测不到 ffmpeg 时 MP4/MOV 走这条路，
   其它容器（mkv/avi/webm…）仍会跳过并提示

> 它只动容器账本，**不改变感知哈希**。要让画面指纹也变，仍然需要 light / deep 那两档。

---

## 五、命令行用法（可选，适合脚本批处理）

```bat
:: 用「强力」处理整个目录
python app.py -i D:\素材 -o D:\输出 -p strong

:: 只处理图片，保留目录层级，文件名加随机后缀
python app.py -i D:\素材 -o D:\输出 --only image --random-name

:: 只列出会处理哪些文件，不实际执行
python app.py -i D:\素材 --dry-run

:: 固定随机种子（结果可复现）
python app.py -i D:\素材 -o D:\输出 --seed 2024
```

| 参数 | 说明 |
|---|---|
| `-i/--input` | 文件或目录，可给多个 |
| `-o/--out` | 输出目录；留空则写到源目录旁 `_pixelshift` |
| `-p/--preset` | `light` / `medium` / `strong` |
| `--only` | `all` / `image` / `video` |
| `--video-mode` | `remux` / `light` / `deep` / `metabox`（覆盖预设；metabox 不需要 ffmpeg） |
| `--no-fallback` | 缺少 ffmpeg 时不自动降级到 metabox |
| `--concurrency` | 图片并发线程（默认 4） |
| `--video-workers` | 同时跑的 ffmpeg 数量（默认 2） |
| `--overwrite` | 同名覆盖而不是改名 |
| `--random-name` | 文件名追加 4 位随机后缀 |
| `--seed` | 固定随机种子 |
| `--dry-run` | 只列文件 |

---

## 六、报告字段

CSV 为 UTF-8-BOM，Excel 直接双击可读：

| 列 | 含义 |
|---|---|
| 源文件 / 输出文件 | 完整路径 |
| 类型 | image / video |
| 状态 | ok / failed / skipped / cancelled |
| 原MD5 / 新MD5 | 特征码对比 |
| 原大小 / 新大小 | 字节数 |
| 耗时秒 | 单个文件耗时 |
| 感知哈希距离 | 图片 pHash 汉明距离（0-64） |
| 备注/错误 | 变形参数、失败原因 |

---

## 七、常见问题

**视频处理完体积变大了？**
强力模式会加噪声与缩放往返，x264 要花更多码率去保留这些高频细节，体积涨 50%~150%
是正常的。想控制体积：把 CRF 调高（22~26），或改用「轻度/标准」，或把滤镜强度降到 0~1。

**pHashΔ 是 0 或很小？**
纯色块、文字图、简单几何图案这类画面，感知哈希本来就对微扰不敏感（这正是 pHash 的设计）。
要拉开差距就开「结构变形」里的裁切（1.5%~3%）或镜像。

**视频很慢？**
`deep` 模式是完整重编码。1 分钟 1080p 大约需要 1~3 分钟（取决于 CPU）。
赶时间就把模式换成 `remux`（秒级）或 `light`；只想换掉文件哈希就选 `metabox`（也是秒级）。

**没装 ffmpeg 还能处理视频吗？**
能。MP4/MOV 走「仅改元数据」模式（纯 Python 改容器，见第四章第 4 节），文件哈希必变、
结构仍然合法、画面与时长不动。默认已勾选「缺 ffmpeg 时自动降级」，不用手动切换。
MKV / AVI / WEBM 这类非 box 容器做不到，会被跳过并提示。

**能拖文件进窗口吗？**
能，Windows 下直接拖，不需要装任何东西。实现方式（`pixelshift/dnd.py`）是对 Tk 顶层窗口
调用 `DragAcceptFiles`，再子类化窗口过程截获 `WM_DROPFILES`，用 `DragQueryFileW` 取出
路径。由于该回调运行在 Tcl 消息派发栈里，回调内**只把路径塞进队列、不碰任何 Tk API**
（否则会重入解释器并触发 `PyEval_RestoreThread` 崩溃），由主线程 120ms 轮询取出后再入列表。

拖入支持：单个/多个文件、整个文件夹（自动递归）、混合拖入；重复文件自动去重；
不支持的扩展名会被过滤并在右侧提示条给出原因。列表头部会显示
「⇩ 可直接把文件 / 文件夹拖到这里」，成功拖入后短暂变成绿色「✔ 已拖入 N 个文件」。

**原文件会被覆盖吗？**
默认不会：结果写到别的目录，源文件原样保留。只有当「输出目录」选成源文件所在目录
并且「同名文件」选「覆盖」时才会原地替换——这时引擎会先写临时文件再原子替换，
中途中断也不会留下半个损坏文件。

**双击 bat 弹出一串「不是内部或外部命令」？**
那是旧版中文批处理被 GBK 误读导致的。现在的 `启动 PixelShift.bat` 已改成纯 ASCII + CRLF，
不会再有这个问题。若仍有异常，直接在该目录的 PowerShell / cmd 里执行：
`python app.py`

**支持哪些格式？**
图片：jpg / jpeg / png / webp / bmp / tif / tiff / gif（含动画）/ ppm
视频（ffmpeg 模式）：mp4 / m4v / mov / mkv / avi / flv / wmv / webm / ts / mpg / mpeg / m2ts / 3gp / rmvb
视频（metabox 免 ffmpeg 模式）：mp4 / m4v / mov / 3gp / m4a

---

## 八、打包成 exe

### 三条技术路线

| 路线 | 代表工具 | 本质 | 代价 |
|---|---|---|---|
| **打包派** | PyInstaller / cx_Freeze | 把 CPython 解释器（`python312.dll`）+ 标准库 `.pyc` + 第三方 `.pyd` + 你的代码 + 一个 C 写的 bootloader 打在一起。**不是编译**，代码是字节码 | 体积大；`.pyc` 可被 pyinstxtractor + decompyle 还原 |
| **编译派** | Nuitka / Cython / mypyc | 把 Python 翻译成 C，再用 MSVC/MinGW 编译成真机器码 | 首次构建 10~30 分钟；需要 C 工具链 |
| **嵌入派** | python-build-standalone + 自写 launcher | 手工组织可重定位的 Python 目录，写个小启动器 | 工程量大，但体积与特征最可控 |

本项目采用**打包派（PyInstaller）**：构建快、生态成熟、对 tkinter + Pillow 支持好。

bootloader 的两种形态：

- **onedir**：`PixelShift.exe`（约 2.9 MB）+ `_internal/` 目录，运行时直接加载，启动快
- **onefile**：全部内容塞进一个 exe，运行时解压到 `%TEMP%\_MEIxxxx`，分发方便但启动慢

### 已内置的构建脚本

```bat
build_exe.bat            :: onedir，产物 dist\PixelShift\PixelShift.exe
build_exe.bat onefile    :: 单文件，产物 dist_onefile\PixelShift.exe
```

对应 `PixelShift.spec` 与 `PixelShift.onefile.spec`，两者都做了体积裁剪。

### 实测数据（本机，Python 3.12.10 / Pillow 12.2.0）

| 形态 | 体积 | 文件数 | 冷启动 |
|---|---|---|---|
| onedir 未优化 | 39.5 MB | 999 | — |
| onedir 优化后 | **25.8 MB** | 993 | **1.29 s** |
| onefile 优化后 | **11.6 MB** | 1 | **2.22 s** |

裁剪了什么（省下 13.7 MB）：

| 剔除项 | 省下 | 原因 |
|---|---|---|
| `PIL\_avif.cp312-win_amd64.pyd` | 7.5 MB | 本项目不支持 AVIF |
| `libcrypto-3.dll` + `libssl-3.dll` | 5.8 MB | 全程不联网，`ssl`/`_ssl`/`_hashlib` 一并排除 |
| `numpy` / `pandas` / `scipy` / `matplotlib` / `pytest` / `IPython` | — | 从未 import，但在 Analysis 阶段会被拖进来 |

刻意**不启用 UPX 压缩**：压缩后的 exe 杀软误报率明显升高，省下的几 MB 不值得。

### ffmpeg 怎么处理（关键决策）

本机静态全功能版 `ffmpeg.exe` 217 MB、`ffprobe.exe` 217 MB——直接塞进包会让体积失控。三条路：

| 方案 | 做法 | 体积 | 适用 |
|---|---|---|---|
| **A. 不打包（当前默认）** | 运行时按 `PATH` → exe 同目录 → `bin/` → `ffmpeg/` → 常见安装目录 顺序查找；找不到时图片功能照常，视频提示缺失 | 0 | 自己电脑用、用户已装 ffmpeg |
| **B. 随包放精简版** | 把 30~60 MB 的 shared/minimal 构建放进 `dist\PixelShift\_internal\ffmpeg\`，代码已支持从该目录加载 | +30~60 MB | 分发给不会装软件的人 |
| **C. 首次运行下载** | 启动时检测并引导下载 | 0 | 需要网络与校验逻辑 |

方案 B 只需两步：

```bat
mkdir dist\PixelShift\_internal\ffmpeg
copy <精简版>\ffmpeg.exe  dist\PixelShift\_internal\ffmpeg\
copy <精简版>\ffprobe.exe dist\PixelShift\_internal\ffmpeg\
```

### 四个必踩的坑（本项目已处理）

1. **`--windowed` 会吞掉异常**：打包后崩溃就是静默退出，什么都没有。
   已在 `app.py` 装 `sys.excepthook`，把 traceback 写到
   `%LOCALAPPDATA%\PixelShift\crash.log` 并弹窗。
2. **`sys.stdout` / `sys.stderr` 在 windowed 下是 `None`**：任何 `print` 直接抛
   `AttributeError`。已加 `_fix_std_streams()` 把它们指向 `os.devnull`。
3. **`__file__` 指向临时解压目录**：onefile 下读"项目同目录"的资源会失败，
   资源路径要走 `sys._MEIPASS`。本项目没有外部资源依赖，未受影响。
4. **杀软误报**：PyInstaller bootloader 是常见特征，onefile 尤甚。缓解手段是
   onedir + 不上 UPX + 代码签名（自签名可消除内网分发时的警告）。

### 分发提示

未签名的 exe 首次运行会被 SmartScreen 拦下（"Windows 已保护你的电脑"），
需要点「更多信息 → 仍要运行」。要彻底消除得买代码签名证书（OV/EV）；
内网自用可以用自签名证书 + 手动导入受信任根。

---

## 九、目录结构

```
PixelShift/
├── app.py                      入口（GUI / CLI 二合一，含崩溃日志钩子）
├── 启动 PixelShift.bat         双击启动
├── build_exe.bat               打包 exe（onedir / onefile）
├── PixelShift.spec             PyInstaller onedir 描述
├── PixelShift.onefile.spec     PyInstaller onefile 描述
├── requirements.txt
├── pixelshift/
│   ├── __init__.py
│   ├── config.py               所有选项、三种预设
│   ├── dnd.py                  Windows 原生拖放（WM_DROPFILES，零依赖）
│   ├── hasher.py               MD5 / SHA1 / 体积 / pHash / aHash
│   ├── random_fp.py            随机指纹素材（设备、软件、日期、注释）
│   ├── images.py               图片管线
│   ├── videos.py               视频管线（ffmpeg 调用 + 进度解析）
│   ├── mp4box.py               免 ffmpeg 的 MP4/MOV 容器元数据改写
│   ├── engine.py               任务调度、输出路径、并发、CSV 报告
│   ├── gui.py                  tkinter 界面
│   └── cli.py                  命令行
├── samples/                    自带测试素材（可删）
└── tools/
    ├── make_samples.py         生成测试素材
    ├── smoke_test.py           回归自测（34 项）
    ├── mp4box_test.py          免 ffmpeg 容器改写自测（42 项，含 ffprobe 交叉验证）
    ├── dnd_test.py             拖放链路自测（构造 WM_DROPFILES 投递，14 项）
    └── gui_selfcheck.py        界面截图自检
```

自测：

```bat
python tools/smoke_test.py     :: 引擎与管线：34 项
python tools/mp4box_test.py    :: 免 ffmpeg 容器改写：42 项
python tools/dnd_test.py       :: 拖放链路：14 项
```

预期分别输出 `通过 34 · 失败 0`、`通过 42 · 失败 0`、`通过 14 · 失败 0`。

---

## 十、版本管理

本地仓库已初始化：分支 `main`，首个提交 `f7d03d7`，tag `v1.0.0`（仅本地，未配置远端）。

```bat
git status                    :: 看改动
git add -A
git commit -m "改了什么"
git log --oneline --decorate  :: 看历史
git tag v1.0.1                :: 打版本
```

约定：

- `.gitignore` 已排除 `__pycache__`、`out_*`、`_pixelshift/`、`PixelShift_报告_*.csv`、
  `*.ps_tmp*`、`shot_*.png` 等运行时产物——处理结果不会污染提交。
- `.gitattributes` 固定换行：源码 LF，`*.bat` 强制 CRLF（cmd 对 LF-only 批处理支持不稳），
  图片/视频标为 binary。仓库内已设置 `core.autocrlf=false`、`core.quotepath=false`
  （中文文件名正常显示）、`i18n.commitEncoding=utf-8`。

