"""选项与预设。所有可调参数集中在这里，GUI / CLI 共用。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal

# ---------------------------------------------------------------- 类型别名
ImageFormat = Literal["keep", "jpg", "png", "webp", "bmp", "tiff"]
VideoMode = Literal["remux", "light", "deep", "metabox"]
VideoContainer = Literal["keep", "mp4", "mkv", "mov"]
Collision = Literal["suffix", "overwrite", "skip"]

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".avif", ".ppm")
VIDEO_EXTS = (".mp4", ".m4v", ".mov", ".mkv", ".avi", ".flv", ".wmv", ".webm", ".ts",
              ".mpg", ".mpeg", ".m2ts", ".3gp", ".rmvb", ".rm")

SUPPORTED_EXTS = tuple(sorted(set(IMAGE_EXTS) | set(VIDEO_EXTS)))


def kind_of(path: str) -> str:
    low = path.lower()
    if low.endswith(IMAGE_EXTS):
        return "image"
    if low.endswith(VIDEO_EXTS):
        return "video"
    return "other"


# ---------------------------------------------------------------- 图片选项
@dataclass
class ImageOptions:
    mode: str = "reencode"             # reencode = 解码重编码；metaonly = 只注入元数据
    out_format: ImageFormat = "keep"   # keep = 保持原格式
    jpeg_quality: int = 95             # 60-100
    jpeg_subsampling: str = "auto"     # auto / 4:2:0 / 4:2:2 / 4:4:4
    png_level: int = -1                # -1 = 随机 6-9
    webp_quality: int = 92

    pixel_noise: int = 25              # 0-100：随机单字节扰动强度
    noise_delta: int = 2               # ±1..6
    color_jitter: int = 0              # 0-100：亮度/对比/饱和/gamma 微扰
    micro_resize: int = 0              # 0-4 px：缩小再放大，打断重采样统计
    flip: str = "none"                 # none / auto（随机水平镜像）

    # —— 结构变形（真正能改动感知指纹的手段，会改变画面观感，默认关闭）
    crop_percent: float = 0.0          # 0-6：居中裁掉 N% 再放大回原尺寸
    rotate: str = "none"               # none / right90 / left90 / 180

    strip_metadata: bool = True        # 丢弃原始 EXIF / XMP / ICC
    inject_metadata: bool = True       # 写入全新随机元数据（EXIF + 注释）
    meta_profile: str = "auto"         # auto / camera / phone / scanner / plain
    keep_icc: bool = True              # strip 时是否保留 ICC 色彩配置

    def label(self) -> str:
        if self.mode == "metaonly":
            return "图片[仅注入元数据 · 不重编码]"
        fmt = "原格式" if self.out_format == "keep" else self.out_format.upper()
        extra = ""
        if self.crop_percent > 0:
            extra += f" 裁{self.crop_percent:g}%"
        if self.rotate != "none":
            extra += f" 旋转{self.rotate}"
        if self.flip != "none":
            extra += " 镜像"
        return (f"图片[{fmt} q{self.jpeg_quality} 噪声{self.pixel_noise} "
                f"抖动{self.color_jitter}{extra}]")


# ---------------------------------------------------------------- 视频选项
@dataclass
class VideoOptions:
    mode: VideoMode = "light"          # remux 最快 / light 重编码 / deep 全变形 / metabox 免 ffmpeg
    container: VideoContainer = "keep"
    crf: int = 20                      # 16-30
    preset: str = "veryfast"
    filter_level: int = 1              # 0 无 / 1 轻 / 2 中 / 3 强（缩放往返+噪声+色调）
    audio_mode: str = "copy"           # copy / reencode
    audio_bitrate: str = "192k"
    keyint: int = 0                    # 0 = 随机 200-300
    faststart: bool = True             # mp4 前置 moov
    strip_metadata: bool = False       # 清空原始容器元数据
    inject_metadata: bool = True       # 写入随机 comment/title/handler_name/creation_time
    copy_other_streams: bool = True    # 字幕 / 数据流原样带过

    # —— 结构变形（会改变画面或时长，默认关闭）
    mirror: bool = False               # 水平镜像
    crop_percent: float = 0.0          # 0-6：居中裁掉 N% 再放回原尺寸
    speed: float = 1.0                 # 0.95-1.05：整体变速（音画同步拉伸）

    # —— 免 ffmpeg 兜底（纯 Python 改写 MP4/MOV 容器，见 mp4box.py）
    no_ffmpeg_fallback: bool = True    # 缺少 ffmpeg 时自动降级到 metabox 模式
    metabox_patch_mdhd: bool = True    # 顺带改写每个轨道 mdhd 的时间戳

    def label(self) -> str:
        if self.mode == "metabox":
            return "视频[仅改元数据 · 免 ffmpeg]"
        extra = ""
        if self.mirror:
            extra += " 镜像"
        if self.crop_percent > 0:
            extra += f" 裁{self.crop_percent:g}%"
        if abs(self.speed - 1.0) > 1e-6:
            extra += f" 速度{self.speed:.3f}"
        return (f"视频[{self.mode} crf{self.crf} 滤镜L{self.filter_level} "
                f"音频{self.audio_mode}{extra}]")


# ---------------------------------------------------------------- 全局选项
@dataclass
class RunOptions:
    out_dir: str = ""                  # 空 = 输出到源文件旁边的 _pixelshift 目录
    mirror_structure: bool = True      # 保留输入目录层级
    collision: Collision = "suffix"
    random_name: bool = False          # 文件名追加随机后缀
    random_seed: int | None = None     # 固定种子可复现
    concurrency: int = 4               # 图片线程数
    video_workers: int = 2             # 同时运行的 ffmpeg 数量
    verify_output: bool = True         # 处理完回读校验（尺寸/可解码 + 哈希对比）
    open_folder_when_done: bool = False

    image: ImageOptions = field(default_factory=ImageOptions)
    video: VideoOptions = field(default_factory=VideoOptions)

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- 预设
@dataclass
class Preset:
    key: str
    name: str
    desc: str
    image: ImageOptions
    video: VideoOptions


def _img(**kw) -> ImageOptions:
    return ImageOptions(**kw)


def _vid(**kw) -> VideoOptions:
    return VideoOptions(**kw)


PRESETS: dict[str, Preset] = {
    "lazy": Preset(
        key="lazy",
        name="懒人 · 只换特征码",
        desc="最快一档：不重编码、不动画面，只往容器里注入随机元数据。"
             "体积几乎不变（±几百字节），秒级完成；视频走纯 Python 改容器，不需要 ffmpeg。",
        image=_img(mode="metaonly", strip_metadata=True, inject_metadata=True),
        video=_vid(mode="metabox"),
    ),
    "light": Preset(
        key="light",
        name="轻度 · 画质优先",
        desc="重新编码 + 随机元数据，像素几乎零改动；图片肉眼无差，视频只重封装容器层。",
        image=_img(jpeg_quality=97, pixel_noise=10, noise_delta=1, color_jitter=0,
                   micro_resize=0, strip_metadata=True, inject_metadata=True),
        video=_vid(mode="remux", filter_level=0, audio_mode="copy", crf=22),
    ),
    "medium": Preset(
        key="medium",
        name="标准 · 推荐",
        desc="像素级微扰 + 重编码 + 元数据重构；文件哈希与感知指纹同时变化，画质肉眼无损。",
        image=_img(jpeg_quality=95, pixel_noise=25, noise_delta=2, color_jitter=8,
                   micro_resize=1, strip_metadata=True, inject_metadata=True),
        video=_vid(mode="light", filter_level=1, audio_mode="copy", crf=20),
    ),
    "strong": Preset(
        key="strong",
        name="强力 · 深度变形",
        desc="缩放往返 + 噪声 + 色调 + 居中裁切 1.5% + CRF 重编码 + 音频重编码；"
             "替换哈希与感知指纹差异最大，画质轻微损失。",
        image=_img(jpeg_quality=90, pixel_noise=45, noise_delta=3, color_jitter=20,
                   micro_resize=2, crop_percent=1.5, strip_metadata=True, inject_metadata=True),
        video=_vid(mode="deep", filter_level=3, audio_mode="reencode", crf=23, crop_percent=1.5),
    ),
}

DEFAULT_PRESET = "medium"


def preset_options(key: str) -> tuple[ImageOptions, VideoOptions]:
    p = PRESETS.get(key) or PRESETS[DEFAULT_PRESET]
    return ImageOptions(**asdict(p.image)), VideoOptions(**asdict(p.video))


VIDEO_PRESETS_ENCODER = ("ultrafast", "superfast", "veryfast", "faster", "fast",
                         "medium", "slow", "slower")
