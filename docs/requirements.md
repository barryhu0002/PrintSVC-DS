# PrintSVC — 网络打印服务需求文档

## 1. 项目背景

有一台东芝 E-Studio 240s 老式打印机，连接着打印机的上位机是一台 **Windows 7 32位** 电脑。该电脑上已安装东芝 E-Studio 240s 打印机驱动，通过电脑上图片、PDF、Word 等应用中的"打印"功能可以实现正常的本地打印。

**目标：** 编写一个应用 **PrintSVC**，运行在 Win7 32 位电脑上，作为打印机的网络服务程序。同一局域网内的设备（如手机、平板、其他电脑）可以通过局域网发现该服务，并通过设备上自带应用发送打印命令实现远程打印。

## 2. 工作流程

1. 启动东芝 E-Studio 240s 老式打印机和 PrintSVC 程序，启动顺序不分先后。
2. PrintSVC 程序成功连接打印机，并启动网络发现服务，该过程需要有日志输出用于查看状态。
3. 在同一局域网内的另一设备（如手机、平板、其他电脑）上打开需要被打印的文件，执行打印。
4. 打印机成功接收打印命令和文件，并成功打印。

## 3. 技术方案

| 决策项 | 选择 | 原因 |
|---|---|---|
| **接收打印协议** | IPP / AirPrint | 支持 Android（Mopria）、Windows、iOS 自带打印功能 |
| **文件发送至打印机的方式** | 通过 Windows 驱动（GDI） | 利用已有东芝驱动，兼容性最好 |
| **打印发起端** | Android 设备 + Windows 电脑 | 用户实际使用场景 |
| **打印文件格式** | PDF + 图片(JPEG/PNG等) + Word(docx/doc) + Excel(xlsx/xls) + PowerPoint(pptx/ppt) | 覆盖主流文件类型 |

## 4. 功能要求

### 4.1 核心功能

- **IPP 协议服务器**（端口 631）
  - 支持 `Print-Job`、`Validate-Job`、`Get-Printer-Attributes`、`Get-Jobs`、`Get-Job-Attributes`、`Cancel-Job` 等操作
  - 符合 RFC 2910（传输编码）和 RFC 2911（模型和语义）
- **局域网发现服务**
  - **mDNS/DNS-SD**：广播 `_ipp._tcp`、`_printer._tcp`、`_pdl-datastream._tcp` 服务
  - **SSDP**（UPnP）：响应 Windows 网络发现请求
  - 支持 Android（Mopria 协议栈）、Windows、iOS 自动发现
- **Windows GDI 打印**
  - 通过 `win32print` + `win32ui` 调用打印机驱动
  - PDF 通过 PyMuPDF 渲染为图像后送印
  - 图片通过 Pillow 处理后送印
  - Word / Excel / PowerPoint 通过 Office COM 自动化转 PDF 后送印
  - 支持份数、双面等基本打印选项

### 4.2 Web 状态页面

提供浏览器访问的 Web 状态页面（`http://localhost:631/`），显示：
- 打印机连接状态（名称、驱动、端口、运行状态）
- 服务运行状态（IPP 端口、mDNS 状态）
- 打印作业历史（ID、名称、用户、状态、格式、时间）
- 各设备连接引导说明
- JSON API 接口供程序化查询

### 4.3 日志要求

- 以时间戳格式输出到控制台和日志文件
- 日志级别支持 DEBUG / INFO / WARNING / ERROR
- 日志内容包括：
  - 打印机连接状态
  - 网络发现服务状态
  - 各打印作业的接收、处理、完成状态
  - 异常和错误信息
  - 定时汇总状态（每 60 秒）

### 4.4 日志内容示例

```
2024-01-15 10:00:00 - INFO - Printer connected: Name=TOSHIBA e-STUDIO240s, Driver=..., Status=Idle
2024-01-15 10:00:01 - INFO - IPP server running on 0.0.0.0:631
2024-01-15 10:00:01 - INFO - mDNS services registered on 192.168.x.x:631
2024-01-15 10:05:23 - INFO - Print-Job: fmt=application/pdf, copies=1, job=文档.pdf, user=android-user, size=123456 bytes
2024-01-15 10:05:25 - INFO - Job #1 completed: 文档.pdf
2024-01-15 10:06:00 - INFO - Status: printer=TOSHIBA e-STUDIO240s | active_jobs=0 | total_jobs=1
```

## 5. 技术约束

### 5.1 运行环境

| 项目 | 要求 |
|---|---|
| **操作系统** | Windows 7 32-bit |
| **打印机连接方式** | USB / LPT（通过已安装的 Windows 驱动） |
| **编程语言** | Python |
| **打包方式** | PyInstaller 打包为独立可执行文件（.exe），不依赖额外运行环境 |

### 5.2 开发环境

| 项目 | 版本 |
|---|---|
| **Python** | 3.8.10 32-bit（最后一个支持 Win7 32-bit 的版本） |
| **打包工具** | PyInstaller ≥ 4.0 |
| **构建平台** | Windows 7 32-bit 或交叉构建 |

### 5.3 Python 依赖

| 包 | 用途 | 备注 |
|---|---|---|
| `pywin32` | Windows 打印机 API（win32print、win32ui）及 COM 自动化 | 核心依赖，也用于 Office COM 调用 |
| `Pillow` | 图像处理和 GDI 渲染（PIL.Image、ImageWin.Dib） | 核心依赖 |
| `PyMuPDF` | PDF 渲染为图像 | 核心依赖 |
| `zeroconf` | mDNS/DNS-SD 服务广播 | 核心依赖 |

### 5.4 运行时依赖（非 Python）

| 软件 | 用途 | 说明 |
|---|---|---|
| **Microsoft Office**（2007 或更高版本） | Word/Excel/PowerPoint 文档 → PDF 转换 | 通过 COM 自动化（win32com.client）调用本地安装的 Office 应用 |
| `win32com.client` | COM 自动化接口 | 由 `pywin32` 提供，无需额外安装 |

## 6. 部署说明

### 6.1 构建

```bash
# 在 Win7 32-bit 开发机上
pip install -r requirements.txt
build.bat   # 自动运行 PyInstaller 打包
```

### 6.2 部署步骤

1. 将 `dist\PrintSVC\` 文件夹完整复制到目标 Win7 32-bit 机器
2. 确保打印机已连好，驱动已安装并可正常本地打印
3. 双击 `start_printsvc.bat` 启动服务
4. 打开浏览器访问 `http://localhost:631/` 确认服务运行正常

### 6.3 配置项

通过 `printsvc.json` 配置（重启生效）：

```json
{
    "printer_name": "",
    "ipp_port": 631,
    "listen_address": "0.0.0.0",
    "service_name": "PrintSVC",
    "log_file": "printsvc.log",
    "log_level": "INFO",
    "mDNS_enabled": true
}
```

| 配置项 | 说明 | 默认值 |
|---|---|---|
| `printer_name` | 打印机名称，留空自动检测 | `""` |
| `ipp_port` | IPP 服务端口 | `631` |
| `listen_address` | 监听地址 | `0.0.0.0` |
| `log_file` | 日志文件路径，空则只输出到控制台 | `printsvc.log` |
| `log_level` | 日志级别 | `INFO` |
| `mDNS_enabled` | 启用 mDNS 发现 | `true` |

## 7. 项目结构

```
D:\Project\PrintSVC-DS\
├── main.py                          # 直接运行入口
├── run.py                           # PyInstaller 打包入口
├── build.bat                        # 一键打包脚本
├── printsvc.spec                    # PyInstaller 配置
├── requirements.txt                 # Python 依赖清单
├── printsvc.json                    # 运行时配置文件
├── PRINTSVC_README.txt              # 部署说明文档
│
└── printsvc/
    ├── __init__.py                  # 包版本
    ├── ipp.py                       # IPP 协议编解码器
    ├── server.py                    # HTTP/IPP 服务器 + Web 状态页
    ├── winprint.py                  # Windows GDI 打印后端
    ├── discovery.py                 # mDNS + SSDP 局域网发现
    ├── docrender.py                   # Office 文档(Word/Excel/PowerPoint) → PDF COM 渲染
    ├── config.py                    # JSON 配置管理
    ├── logger.py                    # 日志系统
    └── main.py                      # 主程序入口
```

## 8. 各模块设计

### 8.1 `ipp.py` — IPP 协议编解码

- 实现 IPP 1.1 二进制编码/解码（RFC 2910/2911）
- 编解码标签类型：integer、boolean、enum、keyword、uri、charset、mimeMediaType 等
- 解码请求时按组切分：Operation Attributes、Job Attributes、Printer Attributes
- 构建响应时提供标准属性集合：`make_printer_attributes()`、`make_job_attributes()`
- 支持的操作码：Get-Printer-Attributes、Print-Job、Validate-Job、Get-Jobs、Get-Job-Attributes、Cancel-Job

### 8.2 `server.py` — HTTP/IPP 服务器

- 内置 `HTTPserver` 接收 HTTP + IPP 请求
- 解析 `Content-Type: application/ipp` 的 POST 请求为 IPP 协议
- 调度 IPP 操作到对应的处理函数
- 提供 Web 状态页面（HTML + CSS + JSON API）
- 维护 `JobStore` 作业队列，后台线程异步执行打印
- 全局变量 `printer_name`、`job_store` 供其他模块访问

### 8.3 `winprint.py` — Windows 打印后端

- 枚举本地打印机及其状态
- 根据名称精确或模糊匹配打印机
- `print_image()`：通过 PIL.ImageWin.Dib 将图像渲染到 GDI
- `print_pdf()`：用 PyMuPDF 渲染 PDF 每页为图像后再 GDI 打印
- `print_raw()`：直接向打印机驱动发送 RAW 数据
- `get_printer_info()`：获取打印机详细状态

### 8.4 `docrender.py` — Office 文档渲染

- 通过 `win32com.client`（COM 自动化）调用本地安装的 Microsoft Office 应用
- `word_to_pdf()`：调用 Word.Application 将 .docx/.doc 另存为 PDF（FileFormat=17）
- `excel_to_pdf()`：调用 Excel.Application 将 .xlsx/.xls 导出为 PDF（ExportAsFixedFormat）
- `ppt_to_pdf()`：调用 PowerPoint.Application 将 .pptx/.ppt 导出为 PDF
- `office_to_pdf()`：统一的格式分发入口
- `is_office_format()`：判断 MIME 类型是否为 Office 格式
- 临时文件使用后自动清理
- 依赖本地 Office 环境，不依赖网络或第三方转换服务

### 8.4 `discovery.py` — 局域网发现

- `MDNSService`：注册 `_ipp._tcp`、`_printer._tcp`、`_pdl-datastream._tcp` 服务
- 广播打印机属性：支持的 PDL 格式、URL、型号、颜色/双面能力等
- `SSDPListener`：监听 UPnP M-SEARCH 请求并响应
- Android Mopria 通过 mDNS 发现，Windows 同时通过 mDNS 和 SSDP 发现

## 9. 连接说明

### Android（推荐，使用 Mopria）

1. 打开需要打印的文件
2. 选择"打印"（或菜单 → 打印）
3. 点击打印机选择 → "所有打印机" → PrintSVC 会自动出现在列表中
4. 选择 PrintSVC，调整选项（份数、色彩等），点击打印

### Windows 10/11

1. 设置 → 蓝牙和设备 → 打印机和扫描仪
2. 点击"添加设备"
3. 等待搜索 → 选择"PrintSVC" → 添加设备
4. 在任何应用中通过"打印"选择 PrintSVC 即可

### iOS / macOS

1. 打开文件 → 打印
2. 选择打印机 → PrintSVC 会自动出现在列表中
3. 点击打印

## 10. 注意事项

1. **防火墙设置**：需允许端口 631（IPP）和 5353（mDNS）入站
2. **权限要求**：PrintSVC 需要以管理员权限运行才能访问打印机
3. **打印机状态**：服务启动时会检测打印机，如果打印机离线或未连接会记录警告，但服务仍会正常启动
4. **日志轮转**：日志文件默认最大 10MB，自动轮转保留最近 5 个文件
5. **作业队列**：打印作业在内存中排队，程序重启后历史作业会丢失

## 11. Office 打印支持说明

### 11.1 支持的格式与 MIME 类型

| 格式 | MIME 类型 | 转换方式 |
|---|---|---|
| Word .docx | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | Word COM → PDF |
| Word .doc | `application/msword` | Word COM → PDF |
| Excel .xlsx | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | Excel COM → PDF |
| Excel .xls | `application/vnd.ms-excel` | Excel COM → PDF |
| PowerPoint .pptx | `application/vnd.openxmlformats-officedocument.presentationml.presentation` | PowerPoint COM → PDF |
| PowerPoint .ppt | `application/vnd.ms-powerpoint` | PowerPoint COM → PDF |

### 11.2 工作原理

```
手机/PC → PrintSVC(IPP) → 识别Office格式 → COM自动化
  → Word/Excel/PowerPoint(后台) → 导出PDF → GDI打印 → 打印机
```

### 11.3 技术要求

- 上位机必须安装 **Microsoft Office 2007 或更高版本**
- 无需 Office 许可证激活即可使用 COM 自动化（但推荐安装完整版）
- WPS Office 不兼容 COM 接口（需使用 Microsoft Office）
- Office 应用在后台以不可见方式运行，不影响前台使用
- 转换过程为全自动，无需用户交互

### 11.4 限制

- 同一时间只能处理一个 Office 文档转换（Office COM 单线程限制）
- 首次转换时 Office 应用启动可能需要几秒钟
- 转换质量与 Office 版本相关（建议 Office 2010 或更高版本）
- 大文件（> 50MB Excel 或 > 100页 Word）可能转换较慢
- 如果系统未安装 Office，Office 格式将打印失败，但 PDF 和图片仍可正常打印

### 11.5 打印流程

```mermaid
graph TD
    A[接收打印任务] --> B{文件格式判断}
    B -->|PDF| C[PyMuPDF 渲染]
    B -->|图片| D[Pillow 处理]
    B -->|Office文档| E[COM自动化调用Office]
    E --> F[导出为PDF]
    F --> C
    C --> G[GDI打印]
    D --> G
    G --> H[打印机]
```
