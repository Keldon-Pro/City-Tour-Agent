# 城市旅游智能体

## 项目简介

“城市旅游智能体” 是一个结合 大语言模型 (LLM) 与 高德地图数据（POI / 天气 / 路线 / 地理编码） 的智能出行规划助手。用户可以输入出行偏好、预算、旅行目的等，系统生成结构化行程建议，并可查询实时地点、路线与天气信息。

当前实现已经去掉本地 Node.js MCP 组件，采用 Python 封装：
- 优先尝试调用 高德官方 Remote MCP Server（若可访问）
- 失败自动回退至 高德 Web Service REST API

## 功能概述
| 类别 | 功能 | 说明 |
|------|------|------|
| 行程规划 | LLM 生成多日行程 | 结合用户偏好、预算、主题目的描述 |
| 地理检索 | POI 搜索 / 周边检索 | 支持城市限定、坐标半径、类型过滤 |
| 地理解析 | 正/逆地理编码 | 地址⇄坐标互相转换 |
| 交通路线 | 驾车 / 步行 / 公交 / 骑行 | 返回距离、时间、分段路径信息 |
| 天气服务 | 实况 + 预报 | 使用高德天气接口（extensions=all） |
| 距离测算 | 多起点距离矩阵 | 用于快速比较路线或聚合分析 |
| 会话接口 | Chat + 工具调用 | LLM 输出可包含工具中间结果 |
| 照片展示 | 景点照片轮播（若有） | 优先使用详情接口返回的 photos |
| 日志记录 | 调用链与错误追踪 | 便于排查与行为分析 |

## 目录结构（精简版）
```
├── App/
│   ├── app.py                 # Flask 主应用（路由、格式化、调度）
│   ├── mcp_client_wrapper.py  # 高德 MCP / REST 统一封装
│   ├── static/                # 前端静态资源 (index.html, script.js, styles.css)
│   ├── templates/             # 模板（登录、文档等）
│   ├── logs/                  # 运行日志
│   └── data/                  # 缓存 / 上传 / 嵌入等
├── tests/                     # pytest 测试（单元 / 扩展）
├── Doc/                       # 设计与说明文档
├── requirements.txt           # Python 依赖
├── env.example                # 环境变量示例
└── README.md
```

## 快速开始
```bash
python -m venv .venv
./.venv/Scripts/activate  # Windows PowerShell
pip install -r requirements.txt
cp env.example .env  # 填写你的密钥
python App/app.py
```
启动后访问浏览器中的：`http://127.0.0.1:8000`

## 环境变量
| 变量名 | 必填 | 用途 | 说明 |
|--------|------|------|------|
| AMAP_API_KEY | 是 | 高德地图服务 | 访问 POI/路线/天气 等接口 |
| ARK_API_KEY | 可选 | LLM 推理 | 若未设置则仅工具层可用 |
| SECRET_KEY | 建议 | Flask 会话签名 | 不设将使用随机值（重启会话失效） |
| FLASK_DEBUG | 可选 | 调试模式 | 设为 1 启用 debug |

## 核心组件说明
### mcp_client_wrapper.py
封装所有地图相关方法：
- 先尝试 Remote MCP（tool_name + arguments JSON 调用）
- 失败回退 REST (`/v3/*`, `/v5/place/detail` 等)
- 统一结果截断：长列表仅取前 10 项，减少 token 消耗

### app.py
- 提供 REST 路由 (`/api/search`, `/api/weather`, `/api/directions/...`)
- 统一格式化输出（Markdown + 可嵌入 HTML 片段）
- 对景点结果做照片区块渲染（若包含适用类型与 photos）

## 部分 API 示例
| 路径 | 方法 | 说明 |
|------|------|------|
| `/api/search?keywords=酒店&city=海口` | GET | 关键词 POI 搜索 |
| `/api/around?location=lng,lat&keywords=美食` | GET | 周边检索 |
| `/api/weather/海口` | GET | 天气（含未来预报） |
| `/api/directions/driving?origin=lng1,lat1&destination=lng2,lat2` | GET | 驾车路线 |
| `/api/chat` | POST | LLM 主对话（带上下文与工具调用） |

## 测试
```bash
pytest -c tests/pytest.ini -m "not integration"   # 仅单元 / 快速测试
pytest -c tests/pytest.ini -m integration          # 可能真实请求
```

## 安全与部署简要
- 不要将真实 `*.env` 提交仓库
- 生产务必设置强随机 `SECRET_KEY`
- 可在前置层（如 Nginx）限制访问频率
- 若需要容器化 / CI / 扩展多城市规划，可在后续迭代添加

## 后续规划（Roadmap）
- 图片补全策略（不足 3 张尝试调用详情或静态地图占位）
- 行程缓存 + 用户偏好持久化
- LLM 提示词版本化与多模型支持
- 简化前端组件化（拆分 JS 模块）

## 致谢
- 高德开放平台
- 火山方舟 / 其他 LLM 服务提供方
- 开源 Python / Flask / pytest 生态

---
如需加上 Docker / 云部署 / 反向代理 / CI 示例，请提出需求再补充对应章节。
