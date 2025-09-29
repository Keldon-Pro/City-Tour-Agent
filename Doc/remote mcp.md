# 高德地图远程 MCP Server 集成方案

## 背景与思路

### 原有方案的问题
- 之前使用本地运行的高德地图 MCP Server，需要在本地部署 Node.js 环境
- 需要维护本地 MCP Server 的运行状态，增加了应用的复杂性和资源消耗
- 存在依赖管理和版本兼容性问题

### 新方案优势
- **降低应用负担**：使用高德官方提供的远程 MCP Server，无需本地部署
- **零运维成本**：采用全托管云服务架构，无需关心服务器维护、资源扩容等问题
- **自动升级**：高德官方会持续进行迭代更新，无须用户自己任何额外操作
- **更易理解**：官方对原始 JSON 结果进行了语义化转换，更易于大模型理解

## 接入方案

### 1. 使用 Streamable HTTP 方式（推荐）
- **Server URL**: `https://mcp.amap.com/mcp?key=您的高德API密钥`
- **协议**: Streamable HTTP
- **认证**: 通过 URL 参数传递 API Key

### 2. 工具映射策略
由于我们只需要其中的几个核心工具，需要在 Python 客户端中进行工具名映射：

#### 高德官方工具名 → 本地方法名映射
- `maps_geocode` → `get_geo_location()`
- `maps_regeocode` → `get_regeocode()`
- `maps_text_search` → `search_pois()`
- `maps_around_search` → `search_around()`
- `maps_detail_search` → `get_poi_detail()`
- `maps_weather` → `get_weather()`
- `maps_distance` → `get_distance()`
- `maps_direction_walking` → `get_walking_directions()`
- `maps_direction_driving` → `get_driving_directions()`
- `maps_direction_transit` → `get_transit_directions()`
- `maps_direction_bicycling` → `get_bicycling_directions()`
- `maps_ip_location` → `get_ip_location()`

### 3. 实现架构
- 使用 Python requests 库直接调用远程 MCP Server
- 简化原有的 Node.js subprocess 调用逻辑
- 保持原有的方法接口不变，确保向后兼容

## 待实现功能
~~1. 重构 `mcp_client_wrapper.py` 类~~ ✅ 已完成
~~2. 移除本地 Node.js 相关依赖~~ ✅ 已完成  
~~3. 实现基于 HTTP 的远程 MCP 调用~~ ✅ 已完成
~~4. 更新环境变量配置（AMAP_API_KEY）~~ ✅ 已完成
5. 测试所有工具的功能完整性 ⏳ 待测试

## 重构完成情况

### ✅ 已完成的工作
1. **完全重构 MCPClientWrapper 类**
   - 移除了所有 Node.js subprocess 调用逻辑
   - 实现了基于 requests 库的 HTTP 客户端
   - 保持了所有原有方法的接口兼容性

2. **工具名映射实现**
   - `get_geo_location()` → `maps_geocode`
   - `get_regeocode()` → `maps_regeocode`
   - `search_pois()` → `maps_text_search`
   - `search_around()` → `maps_around_search`
   - `get_poi_detail()` → `maps_detail_search`
   - `get_weather()` → `maps_weather`
   - `get_distance()` → `maps_distance`
   - `get_walking_directions()` → `maps_direction_walking`
   - `get_driving_directions()` → `maps_direction_driving`
   - `get_transit_directions()` → `maps_direction_transit`
   - `get_bicycling_directions()` → `maps_direction_bicycling`
   - `get_ip_location()` → `maps_ip_location`

3. **环境变量配置更新**
   - 更新 `env.example` 文件
   - 将 `AMAP_MAPS_API_KEY` 统一为 `AMAP_API_KEY`
   - 移除本地 MCP Server URL，改用高德官方地址

4. **错误处理和优化**
   - 实现了完善的异常处理机制
   - 添加了 token 优化策略（大结果集自动截取前10条）
   - 保持了调试和日志输出功能

### 📋 使用说明
1. **设置环境变量**：
   ```bash
   set AMAP_API_KEY=your_amap_api_key_here
   ```

2. **代码中使用**：
   ```python
   from mcp_client_wrapper import MCPClientWrapper
   
   # 自动从环境变量获取API Key
   mcp_client = MCPClientWrapper()
   
   # 或者直接传递API Key
   mcp_client = MCPClientWrapper(api_key="your_api_key")
   ```

3. **测试连接**：
   ```bash
   python mcp_client_wrapper.py
   ```

### ⏳ 下一步工作
- 在实际应用中测试所有工具的功能完整性
- 验证与现有 Flask 应用的集成兼容性
- 监控远程调用的性能和稳定性