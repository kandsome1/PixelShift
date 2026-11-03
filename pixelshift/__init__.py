"""PixelShift —— 图片 / 视频特征码（文件哈希 + 感知指纹）重写工具。

模块划分：
    config  : 选项与预设
    hasher  : 哈希与体积统计
    images  : 图片管线（重编码 / 像素扰动 / 元数据注入）
    videos  : 视频管线（重封装 / 重编码 / 滤镜 / 元数据）
    engine  : 批量任务调度、输出路径、报告
    gui     : tkinter 图形界面
    cli     : 命令行入口（无界面批处理，也用于自测）
"""

__version__ = "1.4.0"
APP_NAME = "PixelShift"
APP_TITLE = "PixelShift · 图片视频特征码改写器"
