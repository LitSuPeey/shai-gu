# 第三方开源软件声明 / Third-Party Notices

本项目的代码依据 **MIT License** 发布（见 `LICENSE`）。本项目使用了以下第三方开源软件，
各软件仍受其原始许可证约束。本声明仅列举本项目直接依赖（`requirements.txt`）或可选依赖
的许可证信息，完整许可证文本请以各项目官方仓库为准。

The code of this project is licensed under the **MIT License** (see `LICENSE`).
This project depends on the following third-party open-source software, each of
which remains governed by its own license. This notice lists the licenses of the
direct / optional dependencies declared in `requirements.txt`; refer to each
project's official repository for the full license text.

## 运行时依赖 / Runtime Dependencies

| 软件 Package | 许可证 License | 说明 |
| --- | --- | --- |
| FastAPI | MIT | Web 框架 |
| Uvicorn | BSD-3-Clause | ASGI 服务器 |
| Starlette | BSD-3-Clause | FastAPI 底层依赖 |
| Pydantic | MIT | 数据校验 |
| pandas | BSD-3-Clause | 数据处理 |
| NumPy | BSD-3-Clause | 数值计算 |
| AKShare | MIT | 数据源（行情/估值/分红等） |
| requests | Apache-2.0 | HTTP 客户端 |
| tqdm | MPL-2.0 与 MIT 双许可 | 进度条 |
| jieba | MIT | 中文分词（快讯关键词提取） |
| py_mini_racer | MIT | V8 运行时封装（内含 Google V8，BSD-3-Clause） |
| curl_cffi | MIT | 复刻真实 Chrome TLS 指纹（B站模块） |

## 可选依赖 / Optional Dependencies

仅当启用「仅供学习」模块的本地语音转写时才需要，二选一即可，均不安装则功能自动降级隐藏。

| 软件 Package | 许可证 License | 说明 |
| --- | --- | --- |
| faster-whisper | MIT | 本地语音转写引擎 |
| FunASR | MIT（工具包源码）；预训练模型权重另有单独许可，以各模型卡片为准 | 本地语音转写引擎 |
| ModelScope | Apache-2.0 | FunASR 模型下载依赖 |

## 重要提示 / Important Notes

1. **数据源**：本项目通过 AKShare 等库聚合来自新浪、腾讯、东方财富、百度等免费公开渠道的
   金融数据。这些数据仅供学习研究，其归属与使用限制以各数据提供方的条款为准；**请勿将抓取
   的数据用于商业用途或大规模再分发**。

2. **非代码素材**：`app/web/memes/`（表情包）与 `app/web/gifs/`（动图）为网络搜集或游戏内
   表情，**不属于 MIT 许可范围**，其版权归原作者所有，**严禁商用**。若您是权利人且不希望
   被收录，请联系作者删除。

3. 本声明不构成法律意见。若你有分发或商用需求，请咨询专业人士确认合规性。
