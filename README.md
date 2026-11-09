[OpCore-Simplify](https://github.com/lzhoang2801/OpCore-Simplify) 最新汉化版，汉化源日期线为20261108  
部分汉化参考了[OpCore-Simplify-Chinese](https://github.com/firste2026/OpCore-Simplify-Chinese)  项目。  
为什么汉化？  
不要问为什么，hd4600核显天下第一🐶，现已支持tahoe  

<img width="935" height="628" alt="Screenshot 2026-11-05 103854" src="https://github.com/user-attachments/assets/fa966cd6-01e6-4e8f-9d8b-13c0462dc6a0" />


## 更新说明

### 20261108

- 同步上游 `eec4cfb`（2026-11-05）→ `9aed67e`（2026-11-08）的全部更新。
- 修正 C422 芯片组默认 Quirks 判断，补全 HEDT CPU（Gulftown / Bloomfield）识别，`AppleXcpmExtraMsrs` 不再限制 CPU 代号范围。
- AirPortAtheros40-Tahoe 下载地址更新为 `v1.0.1`。
- README 汉化源日期线更新为 `20261108`。

### 20261105

- 同步上游 `f0e1e44`（2026-10-16）→ `eec4cfb`（2026-11-05）的全部更新。
- 新增 WhateverXe（Intel Xe 核显）支持及相关提示汉化。
- 新增现代 NVIDIA（Turing / Ampere / Ada Lovelace / Blackwell）支持及相关提示汉化。
- 新增 SimpleBCM（Broadcom Wi-Fi 原生 Skywalk）与 AirportRTW89 支持。
- 新增 14 个 Intel VMD 控制器 ID。
- 重写自动更新流程：增加 SHA 校验、失败回滚、更新恢复和安全解压检查，并补全中文提示。
- 优化下载缓存、集中管理 SSL 上下文，更新 macOS 版本推荐逻辑。
- 补全新增 kext 描述、Wi-Fi / 显卡选择界面、Kext 兼容性检查等界面的汉化。
- README 汉化源日期线更新为 `20261105`。

## 自动更新说明

- 从本版本开始，程序启动时会检查并更新本仓库（`ZoomBili/OpCore-Simplify-CN`），不会再从上游英文仓库覆盖汉化文件。
- 使用 Git 克隆方式运行时，程序会检测到 `.git` 并跳过自动更新，请使用 `git pull` 更新。
- 旧版 ZIP 包内置的更新地址仍指向上游，需要手动下载一次本版本；之后即可从本仓库自动更新。
