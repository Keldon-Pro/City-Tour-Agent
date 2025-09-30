# 贡献指南

欢迎为 City Tour Agent 项目做出贡献！这个文档将帮助您了解如何参与项目开发。

## 目录
- [开始之前](#开始之前)
- [开发环境设置](#开发环境设置)
- [贡献流程](#贡献流程)
- [代码规范](#代码规范)
- [提交规范](#提交规范)
- [测试指南](#测试指南)
- [文档贡献](#文档贡献)

## 开始之前

### 行为准则
参与此项目即表示您同意遵守我们的[行为准则](CODE_OF_CONDUCT.md)。请确保在所有交互中保持尊重和专业。

### 贡献类型
我们欢迎以下类型的贡献：
- 🐛 Bug报告和修复
- ✨ 新功能开发
- 📚 文档改进
- 🧪 测试增强
- 🎨 UI/UX改进
- 🌐 国际化和本地化

## 开发环境设置

### 前置要求
- Python 3.8 或更高版本
- Git
- 一个代码编辑器（推荐 VS Code）

### 克隆仓库
```bash
# Fork项目到您的GitHub账户，然后克隆您的fork
git clone https://github.com/your-username/City-Tour-Agent.git
cd City-Tour-Agent

# 添加上游仓库
git remote add upstream https://github.com/Keldon-Pro/City-Tour-Agent.git
```

### 环境配置
```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 复制环境配置文件
cp env.example .env
# 编辑 .env 文件，填入必要的配置
```

### 验证设置
```bash
# 运行测试确保环境正常
python -m pytest tests/

# 启动应用
python App/app.py
```

## 贡献流程

### 1. 选择或创建Issue
- 查看[Issues页面](https://github.com/Keldon-Pro/City-Tour-Agent/issues)寻找感兴趣的问题
- 对于新手，寻找标有`good first issue`的问题
- 如果想添加新功能，请先创建Issue讨论

### 2. 创建分支
```bash
# 确保main分支是最新的
git checkout main
git pull upstream main

# 创建新分支
git checkout -b feature/your-feature-name
# 或
git checkout -b fix/issue-description
```

### 3. 开发
- 编写代码
- 添加或更新测试
- 更新相关文档
- 确保代码通过所有测试

### 4. 提交更改
```bash
# 暂存更改
git add .

# 提交（遵循提交规范）
git commit -m "feat(scope): 添加新功能描述"

# 推送到您的fork
git push origin feature/your-feature-name
```

### 5. 创建Pull Request
- 访问GitHub上的仓库页面
- 点击"New Pull Request"
- 填写详细的PR描述
- 关联相关Issue

## 代码规范

### Python风格指南
- 遵循 [PEP 8](https://pep8.org/) 规范
- 使用 4 个空格缩进
- 行长度限制为 88 字符
- 使用有意义的变量和函数名

### 代码质量工具
```bash
# 代码格式化
black .

# 代码检查
flake8 .

# 类型检查
mypy .
```

### 文件组织
- 将相关功能组织在适当的模块中
- 保持函数和类的单一职责
- 添加适当的注释和文档字符串

## 提交规范

使用约定式提交格式：
```
<type>(<scope>): <subject>

<body>

<footer>
```

### 类型
- `feat`: 新功能
- `fix`: Bug修复
- `docs`: 文档更新
- `style`: 代码格式
- `refactor`: 重构
- `test`: 测试
- `chore`: 构建/工具

### 范围
- `app`: 应用核心
- `mcp`: MCP客户端
- `ui`: 用户界面
- `api`: API接口
- `docs`: 文档

## 测试指南

### 运行测试
```bash
# 运行所有测试
python -m pytest

# 运行特定测试文件
python -m pytest tests/test_specific.py

# 生成覆盖率报告
python -m pytest --cov=App
```

### 编写测试
- 为新功能编写单元测试
- 确保测试覆盖率不低于80%
- 使用描述性的测试函数名
- 遵循AAA模式（Arrange, Act, Assert）

## 文档贡献

### 文档类型
- **README.md**: 项目概述和快速开始
- **Doc/**: 详细技术文档
- **代码注释**: 内联文档
- **API文档**: 接口说明

### 文档规范
- 使用清晰、简洁的语言
- 提供实际的代码示例
- 保持文档与代码同步
- 使用Markdown格式

## 获得帮助

如果您在贡献过程中遇到问题：

1. 查看[FAQ](Doc/FAQ.md)
2. 搜索现有的[Issues](https://github.com/Keldon-Pro/City-Tour-Agent/issues)
3. 在[Discussions](https://github.com/Keldon-Pro/City-Tour-Agent/discussions)中提问
4. 联系维护者

## 致谢

感谢您考虑为 City Tour Agent 做出贡献！每一个贡献都让这个项目变得更好。

---

更多详细信息请参考[版本记录](Doc/版本记录.md)中的开源协作指南部分。