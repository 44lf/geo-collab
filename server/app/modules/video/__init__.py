"""视频生成模块：文章 → 图文轮播短视频（mp4 + SRT + 元数据）。

Claude 主对话写 storyboard，本模块确定性地跑 TTS + ffmpeg 落库，全程不调 LLM。
"""
