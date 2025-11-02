import os
import sys
import re
import json
import logging
import datetime
import pytz
import gevent.monkey
import hashlib
import pandas as pd
from pathlib import Path

# 设置环境变量避免tokenizers并行化警告
os.environ["TOKENIZERS_PARALLELISM"] = "false"

gevent.monkey.patch_all()

from flask import (
    Flask, request, jsonify, send_from_directory, 
    render_template, redirect, url_for, session, 
    flash, send_file
)
from flask_cors import CORS
from openai import OpenAI
from dotenv import load_dotenv
import httpx
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
import unicodedata
import pickle
# 暂时注释掉以专门测试配置API
# from sentence_transformers import SentenceTransformer
from functools import wraps
from sklearn.metrics.pairwise import cosine_similarity

# 设置Python模块搜索路径，便于导入项目根目录下的模块
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from App.mcp_client_wrapper import MCPClientWrapper  # type: ignore


# 创建Flask应用实例
app = Flask(__name__, 
    static_url_path='/static',  # 静态文件URL路径
    static_folder='static',     # 静态文件目录
    template_folder='templates' # 模板文件目录
)

# 配置应用
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 限制上传文件大小为16MB
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'data', 'uploads')
app.config['CACHE_FOLDER'] = os.path.join(app.root_path, 'data', 'cache')
app.config['EMBEDDINGS_FOLDER'] = os.path.join(app.config['CACHE_FOLDER'], 'embeddings')
app.config['CHUNKS_FOLDER'] = os.path.join(app.config['CACHE_FOLDER'], 'chunks')
app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'docx', 'txt', 'md', 'json', 'xlsx'}

# 确保所有必需的目录存在
for folder in [
    app.config['UPLOAD_FOLDER'], 
    app.config['CACHE_FOLDER'],
    app.config['EMBEDDINGS_FOLDER'],
    app.config['CHUNKS_FOLDER']
]:
    os.makedirs(folder, exist_ok=True)

# 文档描述存储文件
app.config['DOC_DESCRIPTIONS_FILE'] = os.path.join(app.config['CACHE_FOLDER'], 'doc_descriptions.json')

# 应用配置存储文件
app.config['APP_CONFIG_FILE'] = os.path.join(app.config['CACHE_FOLDER'], 'app_config.json')
app.config['TRAVEL_PURPOSES_FILE'] = os.path.join(app.config['CACHE_FOLDER'], 'travel_purposes.json')
app.config['TRAVEL_PREFERENCES_FILE'] = os.path.join(app.config['CACHE_FOLDER'], 'travel_preferences.json')

# 全局变量：当前旅游城市
current_city = "海口"  # 默认值

def update_current_city():
    """更新当前城市全局变量"""
    global current_city
    try:
        config = load_app_config()
        current_city = config.get('city_config', {}).get('name', '海口')
        logging.info(f"当前城市已更新为: {current_city}")
    except Exception as e:
        logging.error(f"更新当前城市失败: {e}")
        current_city = '海口'  # 保持默认值

# 设置应用密钥 - 每次重启生成新密钥以确保会话安全
import secrets
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))  # 使用环境变量或随机密钥

# 设置会话配置 - 确保会话在浏览器关闭时过期
app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1小时后过期
app.config['SESSION_COOKIE_HTTPONLY'] = True     # 防止XSS攻击
app.config['SESSION_COOKIE_SECURE'] = False      # 开发环境设为False，生产环境应设为True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'    # CSRF保护

# 启用CORS和代理支持
CORS(app, supports_credentials=True)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# 删除重复的 /api/files 路由，保留功能完整的版本（在第1141行）

# 可用模型列表
AVAILABLE_MODELS = [
    "doubao-1-5-lite-32k-250115",
    "doubao-1-5-pro-32k-250115",
    "doubao-seed-1-6-250615"
]

# 模型详细信息字典
MODEL_INFO = {
    "doubao-1-5-lite-32k-250115": {
        "name": "Doubao-1.5-lite-32k",
        "type": "轻量版",
        "context_length": "32k", 
        "max_output": "12k",
        "version": "250115",
        "capabilities": ["通用任务", "高性价比", "快速响应"],
        "suitable_for": ["日常对话", "简单查询", "基础任务"],
        "cost_level": "低"
    },
    "doubao-1-5-pro-32k-250115": {
        "name": "Doubao-1.5-pro-32k",
        "type": "专业版",
        "context_length": "32k",
        "max_output": "12k",
        "version": "250115",
        "capabilities": ["通用任务", "工具调用", "专业推理", "代码", "中文"],
        "suitable_for": ["复杂推理", "工具调用", "专业分析", "代码生成"],
        "cost_level": "高"
    },
    "doubao-seed-1-6-250615": {
        "name": "Doubao-Seed-1.6",
        "type": "创意版",
        "context_length": "32k",
        "max_output": "12k",
        "version": "250615",
        "capabilities": ["创意生成", "文案写作", "艺术创作", "创新思维"],
        "suitable_for": ["创意写作", "文案生成", "艺术创作", "创新方案"],
        "cost_level": "中"
    }
}

# 用户认证装饰器
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            if request.is_json:
                return jsonify({'error': 'Unauthorized', 'redirect': url_for('login')}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def secure_chinese_filename(filename):
    """
    创建支持中文字符的安全文件名函数
    保留中文字符，只过滤危险的路径字符
    """
    if not filename:
        return filename
    
    # 定义危险字符列表（路径遍历和系统保留字符）
    dangerous_chars = ['/', '\\', '..', '<', '>', ':', '"', '|', '?', '*', '\0']
    
    # 移除危险字符
    safe_filename = filename
    for char in dangerous_chars:
        safe_filename = safe_filename.replace(char, '_')
    
    # 移除首尾空格和点
    safe_filename = safe_filename.strip('. ')
    
    # 如果文件名为空或只包含点，使用默认名称
    if not safe_filename or safe_filename == '.' or safe_filename == '..':
        safe_filename = 'unnamed_file'
    
    # 限制文件名长度（保留扩展名）
    name_part, ext_part = os.path.splitext(safe_filename)
    if len(name_part) > 200:  # 限制主文件名长度
        name_part = name_part[:200]
        safe_filename = name_part + ext_part
    
    return safe_filename

def safe_file_path(filename, base_folder):
    """
    安全地构建文件路径，防止路径遍历攻击
    """
    # 移除路径分隔符，只保留文件名
    filename = os.path.basename(filename)
    
    # 构建完整路径
    file_path = os.path.join(base_folder, filename)
    
    # 获取规范化的绝对路径
    file_path = os.path.abspath(file_path)
    base_folder = os.path.abspath(base_folder)
    
    # 验证文件路径是否在允许的目录内
    if not file_path.startswith(base_folder + os.sep) and file_path != base_folder:
        raise ValueError(f"非法文件路径: {filename}")
    
    return file_path

def validate_upload_request(request):
    """
    验证上传请求的有效性
    返回 (success, error_info, file_obj)
    """
    if 'file' not in request.files:
        return ErrorHandler.handle_error(
            ErrorHandler.VALIDATION_ERROR, 
            '没有文件被上传'
        ) + (None,)
    
    file = request.files['file']
    if file.filename == '':
        return ErrorHandler.handle_error(
            ErrorHandler.VALIDATION_ERROR, 
            '没有选择文件'
        ) + (None,)
    
    if not allowed_file(file.filename):
        return ErrorHandler.handle_error(
            ErrorHandler.VALIDATION_ERROR, 
            '不支持的文件类型'
        ) + (None,)
    
    return True, None, file

def generate_unique_filename(original_filename, upload_folder):
    """
    生成唯一的文件名，避免文件名冲突
    返回 (final_filename, file_path)
    """
    extension = os.path.splitext(original_filename)[1].lower()
    base_filename = secure_chinese_filename(os.path.splitext(original_filename)[0])
    filename = f"{base_filename}{extension}"
    file_path = os.path.join(upload_folder, filename)
    
    # 检查文件是否已存在，如果存在则添加数字后缀
    counter = 1
    while os.path.exists(file_path):
        new_filename = f"{base_filename}_{counter}{extension}"
        file_path = os.path.join(upload_folder, new_filename)
        counter += 1
    
    return os.path.basename(file_path), file_path

def save_uploaded_file(file, file_path):
    """
    保存上传的文件并设置权限
    """
    file.save(file_path)
    os.chmod(file_path, 0o644)  # 确保文件可读

def handle_file_upload_core(file, upload_folder, update_index=True):
    """
    核心文件上传处理逻辑
    返回 (success, info, filename)
    """
    try:
        # 确保上传目录存在
        os.makedirs(upload_folder, exist_ok=True)
        
        # 生成唯一文件名
        final_filename, file_path = generate_unique_filename(file.filename, upload_folder)
        
        # 保存文件
        save_uploaded_file(file, file_path)
        
        # 更新向量索引（如果需要）
        if update_index:
            message = '文件上传成功，向量索引已更新'
            update_embeddings()
        else:
            message = '文件上传成功，请点击"生成索引"按钮更新向量索引'
        
        success, info = ErrorHandler.handle_success(message, {'filename': final_filename})
        return success, info, final_filename
        
    except Exception as e:
        # 如果保存文件后出错，尝试删除已保存的文件
        if 'file_path' in locals() and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass
        success, info = ErrorHandler.handle_error(
            ErrorHandler.UPLOAD_ERROR,
            '文件处理过程中出错',
            f'文件上传处理错误: {str(e)}'
        )
        return success, info, None

def handle_file_deletion_core(filename, upload_folder):
    """
    核心文件删除处理逻辑
    返回 (success, info)
    """
    try:
        # 使用安全的文件路径构建
        file_path = safe_file_path(filename, upload_folder)
        if os.path.exists(file_path):
            # 删除文件
            os.remove(file_path)
            
            # 同时删除对应的描述
            descriptions = load_doc_descriptions()
            if filename in descriptions:
                del descriptions[filename]
                save_doc_descriptions(descriptions)
            
            return ErrorHandler.handle_success('文件和描述删除成功')
        else:
            return ErrorHandler.handle_error(
                ErrorHandler.NOT_FOUND_ERROR,
                '文件不存在'
            )
    except ValueError as e:
        return ErrorHandler.handle_error(
            ErrorHandler.SECURITY_ERROR,
            str(e),
            f'安全错误: {str(e)}'
        )
    except Exception as e:
        return ErrorHandler.handle_error(
            ErrorHandler.SERVER_ERROR,
            '删除失败',
            f'文件删除错误: {str(e)}'
        )

class ErrorHandler:
    """统一错误处理类"""
    
    # 错误类型常量
    VALIDATION_ERROR = 'validation_error'
    SECURITY_ERROR = 'security_error'
    NOT_FOUND_ERROR = 'not_found_error'
    SERVER_ERROR = 'server_error'
    UPLOAD_ERROR = 'upload_error'
    
    # HTTP状态码映射
    STATUS_CODE_MAP = {
        VALIDATION_ERROR: 400,
        SECURITY_ERROR: 400,
        NOT_FOUND_ERROR: 404,
        SERVER_ERROR: 500,
        UPLOAD_ERROR: 500
    }
    
    @staticmethod
    def handle_error(error_type, message, details=None, log_error=True):
        """
        统一错误处理方法
        :param error_type: 错误类型
        :param message: 用户友好的错误信息
        :param details: 详细错误信息（用于日志）
        :param log_error: 是否记录错误日志
        :return: (success, error_info)
        """
        if log_error:
            log_message = details or message
            logging.error(f'[{error_type.upper()}] {log_message}')
        
        return False, {
            'type': error_type,
            'message': message,
            'status_code': ErrorHandler.STATUS_CODE_MAP.get(error_type, 500)
        }
    
    @staticmethod
    def handle_success(message, data=None):
        """
        统一成功处理方法
        :param message: 成功信息
        :param data: 返回数据
        :return: (success, success_info)
        """
        result = {'message': message}
        if data:
            result.update(data)
        return True, result
    
    @staticmethod
    def format_response(success, info, response_type='json'):
        """
        格式化响应
        :param success: 是否成功
        :param info: 响应信息
        :param response_type: 响应类型 ('json' 或 'flash')
        :return: Flask响应对象
        """
        if response_type == 'json':
            if success:
                return jsonify({'success': True, **info})
            else:
                return jsonify({
                    'success': False,
                    'error': info['type'],
                    'msg': info['message']
                }), info['status_code']
        
        elif response_type == 'flash':
            if success:
                flash(info['message'], 'success')
            else:
                # 根据错误类型设置不同的flash类别
                if info['type'] == ErrorHandler.SECURITY_ERROR:
                    flash(f"安全错误: {info['message']}", 'error')
                elif info['type'] == ErrorHandler.NOT_FOUND_ERROR:
                    flash(f"未找到: {info['message']}", 'warning')
                elif info['type'] == ErrorHandler.VALIDATION_ERROR:
                    flash(f"输入错误: {info['message']}", 'warning')
                else:
                    flash(f"错误: {info['message']}", 'error')
            
            return redirect(url_for('document_management'))

def unified_error_handler(response_type='json'):
    """
    统一错误处理装饰器
    :param response_type: 响应类型 ('json' 或 'flash')
    """
    def decorator(func):
        @wraps(func)
        def decorated_function(*args, **kwargs):
            try:
                result = func(*args, **kwargs)
                # 如果函数返回的是错误处理结果
                if isinstance(result, tuple) and len(result) == 2:
                    success, info = result
                    if isinstance(info, dict) and ('type' in info or 'message' in info):
                        return ErrorHandler.format_response(success, info, response_type)
                return result
            except ValueError as e:
                success, info = ErrorHandler.handle_error(
                    ErrorHandler.SECURITY_ERROR, 
                    str(e), 
                    f'Security error in {func.__name__}: {str(e)}'
                )
                return ErrorHandler.format_response(success, info, response_type)
            except FileNotFoundError as e:
                success, info = ErrorHandler.handle_error(
                    ErrorHandler.NOT_FOUND_ERROR, 
                    '文件不存在', 
                    f'File not found in {func.__name__}: {str(e)}'
                )
                return ErrorHandler.format_response(success, info, response_type)
            except Exception as e:
                success, info = ErrorHandler.handle_error(
                    ErrorHandler.SERVER_ERROR, 
                    '服务器内部错误', 
                    f'Unexpected error in {func.__name__}: {str(e)}'
                )
                return ErrorHandler.format_response(success, info, response_type)
        return decorated_function
    return decorator

# 保持向后兼容
def json_error_handler(func):
    """统一JSON错误处理装饰器（向后兼容）"""
    return unified_error_handler('json')(func)

def load_doc_descriptions():
    """
    加载文档描述数据
    """
    try:
        descriptions_file = app.config['DOC_DESCRIPTIONS_FILE']
        if os.path.exists(descriptions_file):
            with open(descriptions_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        logging.error(f'加载文档描述失败: {str(e)}')
        return {}

def save_doc_descriptions(descriptions):
    """
    保存文档描述数据
    """
    try:
        descriptions_file = app.config['DOC_DESCRIPTIONS_FILE']
        with open(descriptions_file, 'w', encoding='utf-8') as f:
            json.dump(descriptions, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logging.error(f'保存文档描述失败: {str(e)}')
        return False

def check_cache_and_docs_status():
    """
    检查文档和向量缓存状态
    返回: dict 包含has_docs、has_cache、doc_list等信息
    """
    try:
        # 1. 检查文档描述是否存在
        descriptions = load_doc_descriptions()
        has_docs = bool(descriptions)
        
        # 2. 检查向量缓存是否存在且有效
        cache_data = load_embedding_cache()
        has_cache = False
        if cache_data is not None:
            # 验证缓存数据完整性
            if all(key in cache_data for key in ['texts', 'embeddings', 'meta']):
                texts = cache_data.get('texts', [])
                embeddings = cache_data.get('embeddings')
                meta = cache_data.get('meta', [])
                if texts and embeddings is not None and meta:
                    has_cache = True
        
        # 3. 构建文档列表（如果都存在）
        doc_list = []
        if has_docs and has_cache:
            for doc_name, description in descriptions.items():
                doc_list.append({
                    'name': doc_name,
                    'description': description
                })
        
        return {
            'has_docs': has_docs,
            'has_cache': has_cache,
            'doc_query_available': has_docs and has_cache,
            'doc_list': doc_list
        }
    
    except Exception as e:
        logging.error(f'检查缓存和文档状态失败: {str(e)}')
        return {
            'has_docs': False,
            'has_cache': False,
            'doc_query_available': False,
            'doc_list': []
        }

def get_system_status():
    """
    获取系统状态信息，包括模型和缓存状态
    返回: dict 包含模型状态、缓存状态等信息
    """
    global _embedding_model, _model_loading, _has_vector_cache
    
    cache_status = check_cache_and_docs_status()
    
    return {
        'model_loaded': _embedding_model is not None,
        'model_loading': _model_loading,
        'has_vector_cache': _has_vector_cache,
        'doc_query_available': cache_status['doc_query_available'],
        'startup_strategy': 'smart' if _has_vector_cache else 'minimal',
        'cache_status': cache_status
    }

def get_file_list_data(upload_folder):
    """
    获取文件列表数据
    返回文件信息列表
    """
    files = []
    descriptions = load_doc_descriptions()
    
    try:
        for filename in os.listdir(upload_folder):
            file_path = os.path.join(upload_folder, filename)
            if os.path.isfile(file_path):
                # 获取文件的修改时间
                mtime = os.path.getmtime(file_path)
                upload_time = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
                
                # 获取文件大小
                file_size = os.path.getsize(file_path)
                if file_size < 1024:
                    size_str = f"{file_size} B"
                elif file_size < 1024 * 1024:
                    size_str = f"{file_size/1024:.1f} KB"
                else:
                    size_str = f"{file_size/(1024*1024):.1f} MB"
                
                files.append({
                    'name': filename,
                    'uploadTime': upload_time,
                    'size': size_str,
                    'type': os.path.splitext(filename)[1].lstrip('.').upper() or 'Unknown',
                    'description': descriptions.get(filename, '')  # 添加描述字段
                })
    except Exception as e:
        logging.error(f'获取文件列表错误: {str(e)}')
    return files

# 登录相关路由
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')
    
    data = request.get_json() or {}
    username = data.get('username', '')
    password = data.get('password', '')
    
    # 验证用户名和密码
    if not username or not password:
        return jsonify({
            'success': False,
            'message': '用户名和密码不能为空'
        }), 400
    
    if (username == os.getenv('ADMIN_USERNAME') and 
        password == os.getenv('ADMIN_PASSWORD')):
        session['logged_in'] = True
        session.permanent = False  # 设置为临时会话，浏览器关闭时过期
        return jsonify({
            'success': True,
            'message': '登录成功',
            'redirect': url_for('document_management')
        })
    else:
        return jsonify({
            'success': False,
            'message': '用户名或密码错误'
        }), 401

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

# 基础模型配置
BASE_MODEL = "doubao-1-5-lite-32k-250115"  # 用于基础对话和简单任务

# 规划模型配置  
PLANNING_MODEL = "doubao-1-5-pro-32k-250115"  # 用于复杂推理、工具调用和行程规划

# 多工具调用配置
MAX_TOOL_ITERATIONS = 5
MAX_CONTEXT_LENGTH = 8000
LOOP_DETECTION_WINDOW = 4
REASONING_TIMEOUT = 30

# 模型配置
REASONING_MODEL = "doubao-1-5-pro-32k-250115"  # 用于推理判断
TOOL_GENERATION_MODEL = "doubao-1-5-lite-32k-250115"  # 用于对话处理（包括工具调用决策）
FINAL_RESPONSE_MODEL = "doubao-1-5-lite-32k-250115"  # 用于最终回复

# 推理判断提示词
REASONING_SYSTEM_PROMPT = """
背景：所在城市是{current_city}，用户会询问你关于{current_city}旅游的任何问题。
基于当前获得的工具调用结果，判断是否有足够信息完整回答用户问题。

判断标准：
- 用户问题的所有关键信息点是否都已获取？
- 是否还有明显缺失的数据？
- 当前信息是否足以给出满意的回答？
- 对于依赖调用场景：是否获得了执行下一步所需的参数？
- 对于独立调用场景：是否覆盖了用户询问的所有地点/事项？
- 对于附近搜索结果：如果搜索半径已满足用户要求，无需再次验证距离

返回格式：
SUFFICIENT: true/false
REASON: [详细的判断理由，说明已获得哪些信息，还缺少什么信息]
NEXT_INSTRUCTION: [如果不充分，生成下一条工具调用指令，格式为 [{{"name": "工具名", "parameters": {{...}}}}]]

【可用工具列表】
系统支持以下5个工具，工具名称必须严格使用：
1. "获取天气信息" - 查询指定城市的天气情况
2. "搜索兴趣点" - 关键词搜索POI信息
3. "附近搜索" - 以某个位置为中心搜索周边POI信息
4. "目的地距离" - 测量两个地点之间的距离
5. "文档查询" - 从本地知识库中检索相关信息并回答问题

【多工具调用策略】
某些复杂查询可能需要多轮工具调用：
1. **依赖调用场景**：后续工具需要前面工具的结果
    - 示例："万绿园附近的酒店" → 先搜索万绿园获取坐标 → 再搜索附近酒店
2. **独立调用场景**：用户询问多个独立的地点或信息
    - 示例："五公祠和海南省博物馆的位置" → 分别搜索两个地点
3. **距离测量场景**：需要先获取两个地点的坐标
    - 示例："从A地到B地多远" → 搜索A地坐标 → 搜索B地坐标 → 计算距离

工具使用说明：
获取天气信息：
## 工具说明：查询指定城市或地点的天气情况，适用于用户询问天气时。
## 参数：city（城市名或adcode，字符串）
## 调用工具的Prompt示例：
- "海口的天气如何？"
## 调用指令：
{"name": "获取天气信息", "parameters": {"city": "海口"}}

搜索兴趣点：
## 工具说明：关键词搜索某地的相关POI信息，适用于用户询问某地有什么好玩的、好吃的等。
## 参数：keywords（关键词，字符串），city（城市名，必填，字符串）
## 调用工具的Prompt示例：
- "海口的海甸岛上有什么豪华酒店？"
## 调用指令：
{"name": "搜索兴趣点", "parameters": {"keywords": "海甸岛豪华型酒店", "city": "海口"}}

附近搜索：
## 工具说明：以某个位置为中心点搜索周边POI信息，适用于用户询问"附近"、"周边"等场景。
## 参数说明：
1. location（必填）：中心点经纬度
    - 格式：经度,纬度（不带引号）
    - 示例：110.312589,20.055793
    - 注意：经纬度之间用英文逗号分隔，不要带空格
    - ⚠️ 重要：如果不知道地点的经纬度，需要先调用"搜索兴趣点"工具获取
2. keywords（必填）：搜索关键词
    - 示例："酒店"、"餐厅"、"景点"等
3. types（必填）：POI类型
    - 可以为空字符串：""
4. radius（必填）：搜索半径
    - 单位：米
    - 示例：1000（表示1公里）
    - 默认值：1000
## 调用示例：
- "万绿园附近有什么酒店？"
## 调用逻辑：
1. 如果不知道万绿园的经纬度，先调用：{"name": "搜索兴趣点", "parameters": {"keywords": "万绿园", "city": "海口"}}
2. 获得经纬度后，再调用：{"name": "附近搜索", "parameters": {"location": "110.312589,20.055793", "keywords": "酒店", "types": "", "radius": 1000}}

【注意事项】
1. 参数必须按照顺序提供：location -> keywords -> types -> radius
2. location 参数不要带双引号
3. types 参数即使为空也必须传入空字符串
4. radius 参数可以是数字或字符串类型

目的地距离：
## 工具说明：测量两个地点之间的距离，适用于用户询问距离、路程等场景。
## 参数：
- origin（起点经纬度，必填，字符串，格式为"经度,纬度"）
- destination（终点经纬度，必填，字符串，格式为"经度,纬度"）
- type（距离类型，可选，字符串，默认"1"）
    * "0"：直线距离
    * "1"：驾车导航距离
    * "3"：步行导航距离
## 调用工具的Prompt示例：
- "从海口湾广场到假日海滩有多远？"
## 调用逻辑：
⚠️ 如果不知道地点的经纬度，需要先分别调用"搜索兴趣点"工具获取起点和终点的坐标
1. 搜索起点：{{"name": "搜索兴趣点", "parameters": {{"keywords": "海口湾广场", "city": "海口"}}}}
2. 搜索终点：{{"name": "搜索兴趣点", "parameters": {{"keywords": "假日海滩", "city": "海口"}}}}
3. 计算距离：{{"name": "目的地距离", "parameters": {{"origin": "110.312589,20.055793", "destination": "110.237390,20.036904"}}}}

【搜索半径说明】
在使用"附近搜索"工具时，可以通过radius参数指定搜索半径：
- 参数名：radius
- 单位：米
- 默认值：1000（1公里）
- 建议值：
    * 200：步行可达范围（3-5分钟步行）
    * 500：步行稍远范围（5-8分钟步行）
    * 1000：默认范围（约15分钟步行）
    * 2000：骑行范围（约10分钟骑行）
    * 3000：驾车短途范围（约5-10分钟驾车）
    * 5000：驾车范围（约15-20分钟驾车）

使用示例：
1. 步行范围内的餐厅：{"location": "110.312589,20.055793", "keywords": "餐厅", "radius": 500}
2. 驾车范围内的景点：{"location": "110.312589,20.055793", "keywords": "景点", "radius": 5000}

文档查询：
## 工具说明：从本地知识库中检索相关信息并回答用户问题，适用范围参考文档列表。
## ⚠️ 使用前提：此工具需要管理员事先上传文档并生成向量索引，如果索引不存在会自动提示用户联系管理员。
## 参数：query（检索关键词，字符串）- **重要**：需要根据用户的完整问题提取核心检索关键词，去除语气词、无关信息，生成简洁、准确的查询词
## 参数生成原则：
- 从用户问题中提取最核心的查询意图
- 去除语气词、感叹词、无关的修饰语
- 保留关键的地点、类型、特征等实体信息
- 使用简洁明了的关键词组合
## 调用工具的Prompt示例：
- 用户问："哎呀，我想知道海南免税购物到底有什么限制啊？听说挺复杂的" → query: "海南免税购物限制"
- 用户问："能不能推荐一些海口的特色餐厅，我比较喜欢海鲜" → query: "海口特色餐厅 海鲜"
- 用户问："我们一家三口要去海口旅游，孩子8岁，有什么适合亲子游的酒店吗？" → query: "海口亲子酒店"
## 调用指令：
{"name": "文档查询", "parameters": {"query": "海南免税购物限制"}}

## 注意事项：
- 文档查询工具依赖管理员预先生成的向量索引
- 如果向量索引不存在，工具会自动返回提示信息，无需额外处理
- 建议在推荐本地知识（餐厅、酒店、攻略）时优先尝试此工具

【重要指令】
- 当你需要获取实时数据（如天气、地点、路线等），**必须**用如下格式输出工具调用：
<|FunctionCallBegin|>
[
{
    "name": "工具名称",
    "parameters": {
        "参数名": "参数值"
    }
}
]
<|FunctionCallEnd|>
- 工具调用格式必须严格包含在 <|FunctionCallBegin|> 和 <|FunctionCallEnd|> 之间，且内容为 JSON 数组。
- **每次只生成一条工具调用指令**，工具调用后请等待工具结果返回，再继续回复用户。
- 如果需要多个工具配合完成任务，会自动进行多轮调用，你只需专注于当前最需要的一个工具。
- **工具名称必须严格使用以下5个名称之一，不允许任何变体**：
    * "获取天气信息"
    * "搜索兴趣点" 
    * "附近搜索"
    * "目的地距离"
    * "文档查询"
- 有经纬度参数都必须用英文双引号包裹，作为字符串传递，例如"110.237390,20.036904"
"""

# 最终回复提示词
FINAL_RESPONSE_SYSTEM_PROMPT = """
你已经获得了所有需要的工具调用结果。请你只用自然语言回复用户，严禁再输出任何工具调用指令。

【任务】请根据工具返回信息，综合考虑用户的问题和需求，用友善、亲切的语气准确地回复用户。
【重要】如果既调用了"文档查询"工具，又调用了MCP工具，请结合两种工具的返回结果来回答用户问题，不能只用其中一种工具的结果。

【文档查询工具处理说明】
当工具返回信息中包含"**检索到的相关文档内容：**"时：
- 这表示是文档查询工具的返回结果，包含了从知识库检索到的相关文档内容
- 你需要基于这些文档内容来回答用户的问题
- 请直接提取和整理文档中的信息，用自然语言重新组织回答
- 保留文档中的"📚 **信息来源**"标注

【重要】回复内容要排版美观、可读性强，善用换行、缩进、emoji等。
每个推荐项必须单独一行或使用 Markdown 列表格式，避免所有信息挤在一起。
使用 Markdown 的有序或无序列表、分段、标题等格式，确保每条推荐清晰分明。
如有多个地点/饭店/景点，务必分条列出，每条单独一行。
地点只需要报具体位置(精确到路名)，不需要报该地点的经纬度（经纬度信息是给大模型看的，不是给人看的）。

【景点推荐场景】
- 当回复中包含景点照片信息时，必须保留原始照片框HTML代码（`<div class="poi-photo-container">`）。
- 如果没有景点照片，则不需要生成照片框HTML代码。
- 示例正确回复：
  ```
  ### 假日海滩
  - 地址：海口市秀英区滨海大道126号
  <div class="poi-photo-container" data-poi-index="1">
    <button class="poi-photo-nav poi-photo-nav-prev" onclick="changePhoto(-1, 1)">&#10094;</button>  <!-- 左箭头，表示上一张照片 -->
    <img src="photo1.jpg" alt="景点照片" class="poi-photo" style="display:block">
    <img src="photo2.jpg" alt="景点照片" class="poi-photo" style="display:none">
    <button class="poi-photo-nav poi-photo-nav-next" onclick="changePhoto(1, 1)">&#10095;</button>  <!-- 右箭头，表示下一张照片 -->
  </div>
  ```

【通用规则】
- 使用自然语言组织内容，但不得删除或修改已有的HTML标签。
- 保持Markdown格式（如`### 标题`、`- 列表项`）。
- 禁止新增工具调用指令。

【排版要求】
- 景点名称用三级标题（`###`）
- 每个景点单独分段
"""

# 行程表生成prompt模板  
ITINERARY_GENERATION_PROMPT = """
你是一个专业的{current_city}旅游规划助手。请根据以下对话历史，分析用户的旅行需求并生成详细的行程表。

## 当前用户行程表数据：
{current_itinerary_json}

## 行程表处理说明：
**如果上方有行程表JSON数据**：
- 这是用户当前保存的行程安排
- 请基于现有行程进行智能调整和优化
- 保持用户已确定的安排（fixed: true的项目）
- 根据用户的新需求进行增量式修改
- 优化时间安排和地理路线，但尽量保持原有结构

**如果上方没有行程表数据**：
- 这是用户首次规划行程
- 请根据对话历史生成全新的行程表
- 重点关注用户的时间、偏好和具体需求

## 对话历史：
{conversation_history}

## 必须提取的信息：
1. **旅行时间**：出发日期、结束日期、总天数
2. **旅行偏好**：景点类型、活动偏好、预算范围  
3. **人员构成**：同行人数、年龄段、特殊需求
4. **已推荐地点**：从对话中提及的所有景点、餐厅、酒店等
5. **固定安排**：已确定的时间安排（如航班、演出等）
6. **调整需求**：用户希望修改的具体内容（如果有现有行程）

## 智能排期原则：
1. **保持连续性**：有现有行程时，保持用户满意的安排不变
2. **时间合理性**：确保地点间转移时间充足，避免过于紧凑
3. **地理优化**：同一区域的地点安排在同一天或相邻时间
4. **活动搭配**：合理搭配室内外活动，考虑体力分配
5. **用餐时间**：在合适时间安排餐厅，避免饥饿或过饱
6. **固定优先**：优先安排固定时间的活动（航班、演出等）

## 输出要求：
严格按照以下JSON格式输出，不要添加任何其他文字说明：

{{
  "days": [
    {{
      "date": "YYYY-MM-DD",
      "day_number": 1,
      "locations": [
        {{
          "address": "具体地点名称",
          "time": "HH:MM或空字符串",
          "notes": "相关说明和建议",
          "fixed": false,
          "visit_order": 1
        }}
      ]
    }}
  ]
}}

**重要提醒**：
- 只输出JSON格式，不要有任何前缀或后缀文字
- 确保所有字段都存在且格式正确
- fixed字段：航班、演出、预订等设为true，用户明确要求保持的安排也设为true
- time字段：有明确时间的填写"HH:MM"，否则填写空字符串""
- notes字段：提供实用的游览建议和注意事项
- 如果是基于现有行程调整，请在notes中说明调整原因

**重要提醒**：
- 只输出JSON格式，不要有任何前缀或后缀文字
- 确保所有字段都存在且格式正确
- fixed字段：航班、演出、预订等设为true
- time字段：有明确时间的填写"HH:MM"，否则填写空字符串""
- notes字段：提供实用的游览建议和注意事项
"""

# 行程表生成辅助函数
def format_full_conversation_for_itinerary(messages):
    """格式化完整对话历史供行程表生成使用"""
    formatted = []
    # 跳过系统消息，使用最近15轮用户和助手的对话
    user_messages = [msg for msg in messages if msg.get("role") in ["user", "assistant"]]
    
    for msg in user_messages[-15:]:
        role = "用户" if msg.get("role") == "user" else "助手"
        content = msg.get("content", "")
        formatted.append(f"{role}: {content}")
    
    return "\n".join(formatted)

def generate_itinerary_from_conversation(messages, current_itinerary=None):
    """
    基于完整对话历史生成行程表，支持基于现有行程的增量调整
    
    Args:
        messages: 完整对话历史
        current_itinerary: 当前行程表数据（支持短期记忆功能）
    """
    try:
        logging.info("开始生成行程表")
        
        # 检查对话历史是否足够 - 如果信息不够，给出温馨提示
        user_messages = [msg for msg in messages if msg.get("role") == "user"]
        logging.info(f"用户消息数量: {len(user_messages)}")
        if len(user_messages) < 2:
            # 返回温馨提示而不是错误
            return {
                "success": True,
                "response": f"您好！我可以为您规划{current_city}旅游行程。请告诉我您想参观什么景点，或者您的旅行偏好，这样我就能为您推荐最合适的行程了！😊",
                "action": "friendly_prompt"
            }
        
        # 格式化完整对话历史
        logging.info("开始格式化对话历史")
        conversation_history = format_full_conversation_for_itinerary(messages)
        logging.info(f"对话历史格式化完成，长度: {len(conversation_history)}")
        
        # 构建包含当前行程的prompt
        logging.info("构建行程表生成prompt")
        
        # 调试：详细检查current_itinerary参数
        logging.info(f"调试 - 函数内current_itinerary类型: {type(current_itinerary)}")
        logging.info(f"调试 - 函数内current_itinerary内容: {current_itinerary}")
        
        itinerary_json_str = ""
        if current_itinerary and current_itinerary.get('days'):
            itinerary_json_str = f"当前用户行程表JSON数据：\n```json\n{json.dumps(current_itinerary, ensure_ascii=False, indent=2)}\n```\n\n"
            logging.info("已包含当前行程表数据作为短期记忆")
            logging.info(f"调试 - 行程表数据长度: {len(itinerary_json_str)}")
        else:
            logging.info("无当前行程表数据，将生成全新行程")
            if current_itinerary:
                logging.info(f"调试 - current_itinerary存在但没有days字段: {current_itinerary}")
            else:
                logging.info("调试 - current_itinerary为None或空")
        
        itinerary_prompt = ITINERARY_GENERATION_PROMPT.format(
            current_city=current_city,
            current_itinerary_json=itinerary_json_str,
            conversation_history=conversation_history
        )
        logging.info(f"行程表prompt构建完成，长度: {len(itinerary_prompt)}")
        
        # 调用大模型生成行程表
        logging.info("开始调用大模型生成行程表")
        response = client.chat.completions.create(
            model=PLANNING_MODEL,  # 使用规划模型处理复杂任务
            messages=[{"role": "user", "content": itinerary_prompt}]
        )
        logging.info("大模型调用成功")
        
        # 解析JSON输出
        itinerary_content = response.choices[0].message.content.strip()
        logging.info(f"大模型原始响应内容: {itinerary_content}")
        
        # 处理可能的markdown代码块包装
        if "```json" in itinerary_content:
            start = itinerary_content.find("```json") + 7
            end = itinerary_content.rfind("```")
            itinerary_content = itinerary_content[start:end].strip()
        elif "```" in itinerary_content:
            start = itinerary_content.find("```") + 3
            end = itinerary_content.rfind("```")
            itinerary_content = itinerary_content[start:end].strip()
        
        try:
            itinerary_json = json.loads(itinerary_content)
            logging.info(f"行程表JSON解析成功，包含 {len(itinerary_json.get('days', []))} 天行程")
            return {
                "success": True,
                "itinerary": itinerary_json
            }
        except json.JSONDecodeError as e:
            logging.error(f"JSON解析失败: {e}")
            return {
                "success": False,
                "error": "生成的行程表格式不正确",
                "raw_response": itinerary_content
            }
        
    except Exception as e:
        logging.error(f"行程表生成失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": f"行程表生成失败: {str(e)}"
        }

def analyze_current_itinerary(current_itinerary):
    """
    分析当前行程表，提供专业的旅行建议
    
    Args:
        current_itinerary: 当前行程表数据
    
    Returns:
        dict: 包含success状态和分析结果的字典
    """
    try:
        logging.info("开始分析行程表")
        
        # 检查是否有行程表数据
        if not current_itinerary or not current_itinerary.get('days'):
            return {
                "success": True,
                "response": "您还没有制定行程表呢！请先告诉我您的旅行需求，我为您生成行程表后再进行分析。😊"
            }
        
        # 调试：详细检查current_itinerary参数
        logging.info(f"调试 - 分析函数内current_itinerary类型: {type(current_itinerary)}")
        logging.info(f"调试 - 分析函数内current_itinerary天数: {len(current_itinerary.get('days', []))}")
        
        # 构建简化的行程分析prompt
        analysis_prompt = f"""请作为专业的旅行顾问，分析以下行程表并提供建议：

行程表数据：
{json.dumps(current_itinerary, ensure_ascii=False, indent=2)}

请从以下几个维度进行分析：

1. **时间安排合理性**
   - 每日景点数量是否合适
   - 游览时间分配是否充足
   - 交通时间是否考虑充分

2. **路线优化建议**
   - 景点间的地理距离和交通便利性
   - 是否存在重复路线或绕路情况
   - 推荐更高效的游览顺序

3. **体验质量评估**
   - 景点类型搭配是否丰富
   - 是否平衡了文化、自然、美食等不同体验
   - 节奏安排是否张弛有度

4. **实用性建议**
   - 根据海口当地情况提供注意事项
   - 推荐最佳游览时段
   - 天气、交通等实用信息

请用友好、专业的语调提供分析，重点突出可操作的建议。"""
        
        # 调用大模型进行分析
        response = client.chat.completions.create(
            model=PLANNING_MODEL,  # 使用强大模型进行深度分析
            messages=[{"role": "user", "content": analysis_prompt}]
        )
        
        analysis_result = response.choices[0].message.content.strip()
        
        return {
            "success": True,
            "response": analysis_result
        }
        
    except Exception as e:
        logging.error(f"行程分析失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": f"行程分析失败: {str(e)}"
        }

# 加载环境变量
load_dotenv()



# 配置日志
beijing_tz = pytz.timezone('Asia/Shanghai')

# 设置日志记录器使用北京时区
class BeijingTimeFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.datetime.fromtimestamp(record.created, tz=beijing_tz)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S %z")

# 避免重复配置日志处理器
root_logger = logging.getLogger()
if not root_logger.handlers:  # 只有在没有处理器时才配置
    # 配置控制台日志处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(BeijingTimeFormatter('%(asctime)s %(levelname)s %(message)s'))
    
    # 配置文件日志处理器
    log_dir = os.path.join(os.path.dirname(__file__), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    now = datetime.datetime.now(beijing_tz)
    log_date = now.strftime('%Y%m%d-%H%M%S')
    log_file_path = os.path.join(log_dir, f'log_{log_date}.log')
    file_handler = logging.FileHandler(log_file_path, encoding='utf-8', mode='a')
    file_handler.setFormatter(BeijingTimeFormatter('%(asctime)s %(levelname)s %(message)s', "%Y-%m-%d %H:%M:%S %z"))
    file_handler.setLevel(logging.DEBUG)
    
    # 配置根日志记录器
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    # werkzeug日志设置（不添加重复的处理器）
    flask_logger = logging.getLogger('werkzeug')
    flask_logger.setLevel(logging.INFO)
    # 不再添加console_handler，因为它会继承root_logger的处理器

# 添加请求日志中间件
@app.before_request
def log_request_info():
    root_logger.debug('Request: %s %s', request.method, request.url)
    if request.method in ('POST', 'PUT', 'PATCH') and request.is_json:
        try:
            root_logger.debug('Request JSON: %s', request.get_json())
        except Exception as e:
            root_logger.warning(f'Failed to parse JSON: {e}')

@app.after_request
def log_response_info(response):
    root_logger.debug('Response: %s', response.status)
    return response

http_client = httpx.Client(
    base_url="https://ark.cn-beijing.volces.com/api/v3",
    timeout=httpx.Timeout(60.0)
)

client = OpenAI(
    base_url="https://ark.cn-beijing.volces.com/api/v3",
    api_key=os.environ.get("ARK_API_KEY")
)


mcp_client = MCPClientWrapper()

# 全局模型缓存 - 智能加载策略
# 启动时检查向量缓存：
# 1. 有缓存索引：异步预加载模型，提升后续查询速度
# 2. 无缓存索引：不加载模型，禁用文档查询功能
_embedding_model = None
_embedding_cache = None
_model_loading = False  # 标记模型是否正在异步加载
_has_vector_cache = False  # 标记是否存在向量缓存
_async_model_task = None  # 异步加载任务
_async_model_task = None  # 异步加载任务

def check_vector_cache_exists():
    """
    检查向量缓存是否存在
    
    Returns:
        bool: True表示存在向量缓存，False表示不存在
    """
    cache_file = os.path.join(app.config['EMBEDDINGS_FOLDER'], 'embedding_cache.pkl')
    return os.path.exists(cache_file) and os.path.getsize(cache_file) > 0

def async_load_model():
    """
    异步加载模型（在后台线程中执行）
    """
    global _embedding_model, _model_loading
    
    if _embedding_model is not None or _model_loading:
        return  # 模型已加载或正在加载
    
    try:
        _model_loading = True
        logging.info("🔄 开始异步加载嵌入模型...")
        
        # 在后台线程中加载模型
        from sentence_transformers import SentenceTransformer
        import time
        
        MODEL_NAME = 'Qwen/Qwen3-Embedding-0.6B'
        start_time = time.time()
        
        # 设置模型缓存目录
        cache_dir = os.path.join(os.path.dirname(__file__), 'model_cache')
        os.makedirs(cache_dir, exist_ok=True)
        
        # 初始化模型
        _embedding_model = SentenceTransformer(
            MODEL_NAME, 
            cache_folder=cache_dir,
            device='cpu'
        )
        
        load_time = time.time() - start_time
        logging.info(f"✅ 模型异步加载完成，耗时: {load_time:.2f}秒")
        
        # 进行一次小测试确保模型工作正常
        test_embedding = _embedding_model.encode(["测试文本"], show_progress_bar=False)
        logging.info(f"🧪 异步加载的模型测试成功，embedding维度: {test_embedding.shape}")
        
    except Exception as e:
        logging.error(f"❌ 异步加载模型失败: {str(e)}")
        _embedding_model = None
    finally:
        _model_loading = False

def smart_startup_initialization():
    """
    智能启动初始化：根据向量缓存状态决定是否预加载模型
    """
    global _has_vector_cache, _async_model_task
    
    try:
        # 检查向量缓存是否存在
        _has_vector_cache = check_vector_cache_exists()
        
        if _has_vector_cache:
            logging.info("🔍 检测到向量缓存文件，启动异步模型加载...")
            # 存在向量缓存，异步预加载模型
            import threading
            _async_model_task = threading.Thread(target=async_load_model, daemon=True)
            _async_model_task.start()
        else:
            logging.info("📭 未检测到向量缓存文件，跳过模型加载，文档查询功能将被禁用")
            
    except Exception as e:
        logging.error(f"启动初始化检查失败: {str(e)}")
        _has_vector_cache = False

def get_embedding_model():
    """获取或初始化嵌入模型（智能加载单例模式）"""
    global _embedding_model, _model_loading, _async_model_task
    
    # 如果模型正在异步加载，等待完成
    if _model_loading and _async_model_task and _async_model_task.is_alive():
        logging.info("⏳ 等待异步模型加载完成...")
        _async_model_task.join(timeout=30)  # 最多等待30秒
        
    if _embedding_model is None:
        try:
            # 尝试导入SentenceTransformer，如果失败就跳过
            from sentence_transformers import SentenceTransformer
            
            MODEL_NAME = 'Qwen/Qwen3-Embedding-0.6B'
            import time
            start_time = time.time()
            logging.info(f"🚀 按需加载嵌入模型: {MODEL_NAME}")
            logging.info("⏳ 正在初始化SentenceTransformer模型，这可能需要一些时间...")
            
            # 设置模型缓存目录
            import os
            cache_dir = os.path.join(os.path.dirname(__file__), 'model_cache')
            os.makedirs(cache_dir, exist_ok=True)
            
            # 初始化模型，设置缓存目录
            _embedding_model = SentenceTransformer(
                MODEL_NAME, 
                cache_folder=cache_dir,
                device='cpu'  # 明确指定使用CPU，避免CUDA相关问题
            )
            
            load_time = time.time() - start_time
            logging.info(f"✅ SentenceTransformer模型加载完成，耗时: {load_time:.2f}秒")
            
            # 进行一次小测试确保模型工作正常
            test_embedding = _embedding_model.encode(["测试文本"], show_progress_bar=False)
            logging.info(f"🧪 模型测试成功，embedding维度: {test_embedding.shape}")
            
        except Exception as e:
            logging.error(f"❌ 嵌入模型加载失败: {str(e)}")
            # 在测试模式下，如果模型加载失败，返回None
            _embedding_model = None
            raise e
    else:
        logging.debug("♻️ 使用已缓存的嵌入模型实例")
    
    return _embedding_model

def load_embedding_cache():
    """
    加载向量缓存（单例模式）
    
    Returns:
        dict: 缓存数据，如果不存在返回None
    """
    global _embedding_cache
    cache_file = os.path.join(app.config['EMBEDDINGS_FOLDER'], 'embedding_cache.pkl')
    
    if _embedding_cache is None:
        if not os.path.exists(cache_file):
            logging.warning(f"向量缓存文件不存在: {cache_file}")
            return None
        
        try:
            logging.info(f"首次加载向量缓存文件: {cache_file}")
            with open(cache_file, 'rb') as f:
                _embedding_cache = pickle.load(f)
            _embedding_cache['_mtime'] = os.path.getmtime(cache_file)
            logging.info(f"向量缓存加载完成，包含 {len(_embedding_cache.get('texts', []))} 个文档")
        except Exception as e:
            logging.error(f"加载向量缓存失败: {str(e)}")
            return None
    
    # 检查缓存文件是否有更新
    elif os.path.exists(cache_file):
        try:
            cache_mtime = os.path.getmtime(cache_file)
            if _embedding_cache.get('_mtime', 0) < cache_mtime:
                logging.info("检测到向量缓存文件更新，重新加载")
                with open(cache_file, 'rb') as f:
                    _embedding_cache = pickle.load(f)
                    _embedding_cache['_mtime'] = cache_mtime
        except Exception as e:
            logging.error(f"重新加载向量缓存失败: {str(e)}")
            return None
    else:
        # 缓存文件被删除
        logging.warning("向量缓存文件已被删除")
        _embedding_cache = None
        return None
    
    return _embedding_cache

def is_document_query_available():
    """
    检查文档查询功能是否可用
    
    Returns:
        bool: True表示可以使用文档查询，False表示功能被禁用
    """
    global _has_vector_cache
    return _has_vector_cache and check_vector_cache_exists()

def get_model_status():
    """
    获取模型状态信息
    
    Returns:
        dict: 包含模型状态的信息
    """
    global _embedding_model, _model_loading, _has_vector_cache
    
    return {
        'has_vector_cache': _has_vector_cache,
        'model_loaded': _embedding_model is not None,
        'model_loading': _model_loading,
        'document_query_available': is_document_query_available()
    }

def clear_embedding_cache():
    """清除向量缓存（用于更新索引后）"""
    global _embedding_cache, _has_vector_cache
    _embedding_cache = None
    
    # 重新检查向量缓存状态
    _has_vector_cache = check_vector_cache_exists()
    
    logging.info("向量缓存已清除")

def perform_rag_query(query, original_question=None, top_k=5, similarity_threshold=0.3):
    """
    执行RAG查询的核心函数
    
    Args:
        query: 优化后的检索关键词（由大模型从用户问题中提取）
        original_question: 用户的原始完整问题（用于生成回答）
        top_k: 返回最相关的文档数量
        similarity_threshold: 相似度阈值
    
    Returns:
        tuple: (是否成功, 回答内容, 相关文档列表)
    """
    try:
        # 0. 首先检查文档查询功能是否可用
        if not is_document_query_available():
            return False, "❌ 文档查询功能当前不可用。系统未检测到向量索引文件，请联系管理员上传文档并生成索引后再使用此功能。", []
        
        # 1. 检查向量缓存是否存在
        cache_data = load_embedding_cache()
        if cache_data is None:
            return False, "❌ 知识库索引未生成。请联系管理员上传文档并生成向量索引后再使用此功能。", []
        
        # 验证缓存数据完整性
        if not all(key in cache_data for key in ['texts', 'embeddings', 'meta']):
            return False, "❌ 知识库索引数据不完整。请联系管理员重新生成向量索引。", []
        
        texts = cache_data['texts']
        embeddings = cache_data['embeddings']
        meta = cache_data['meta']
        
        # 检查数据是否为空
        if not texts or embeddings is None or not meta:
            return False, "❌ 知识库为空。请联系管理员上传文档并生成向量索引。", []
        
        # 确保embeddings是numpy数组
        import numpy as np
        if not isinstance(embeddings, np.ndarray):
            embeddings = np.array(embeddings)
        
        logging.info(f"数据检查完成 - texts: {len(texts)}, embeddings: {embeddings.shape}, meta: {len(meta)}")
        
        # 2. 对优化后的查询关键词进行向量化（使用缓存的模型）
        try:
            model = get_embedding_model()
            query_embedding = model.encode([query], show_progress_bar=False)
            logging.info(f"查询向量化完成，query: {query}, embedding shape: {query_embedding.shape}")
        except Exception as e:
            logging.error(f"查询向量化失败: {str(e)}")
            return False, f"❌ 向量化查询失败: {str(e)}", []
        
        # 3. 计算相似度并筛选相关文档
        logging.info(f"计算相似度，embeddings shape: {embeddings.shape}")
        similarities = cosine_similarity(query_embedding, embeddings)[0]
        logging.info(f"相似度计算完成，similarities shape: {similarities.shape}")
        
        # 获取相似度最高的文档索引
        top_indices = similarities.argsort()[-top_k:][::-1]
        
        # 确保索引为int类型
        top_indices = [int(idx) for idx in top_indices]
        
        # 过滤低相似度结果
        relevant_docs = []
        relevant_texts = []
        for idx in top_indices:
            similarity_score = float(similarities[idx])
            if similarity_score >= similarity_threshold:
                relevant_docs.append({
                    'text': texts[idx],
                    'meta': meta[idx],
                    'similarity': similarity_score
                })
                relevant_texts.append(texts[idx])
        
        if not relevant_docs:
            return False, "未找到相关信息，请尝试重新描述您的问题", []
        
        # 4. 构建相关文档内容，但不生成回答（留给最终回复阶段处理）
        context_docs = []
        for doc in relevant_docs[:3]:  # 只使用前3个最相关的
            context_docs.append({
                'text': doc['text'],
                'source': doc['meta']['source'],
                'similarity': doc['similarity']
            })
        
        # 5. 格式化文档内容用于返回
        context_text = "\n\n".join([doc['text'] for doc in relevant_docs[:3]])
        
        # 6. 添加信息来源标注
        sources = list(set([doc['meta']['source'] for doc in relevant_docs]))
        source_text = f"\n\n📚 **信息来源**: {', '.join(sources)}"
        
        # 返回检索到的文档内容，而不是生成的回答
        formatted_context = f"**检索到的相关文档内容：**\n\n{context_text}{source_text}"
        
        return True, formatted_context, relevant_docs
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logging.error(f"RAG查询失败: {str(e)}")
        logging.error(f"详细错误信息: {error_details}")
        return False, f"❌ 查询过程中发生错误: {str(e)}", []

def split_long_content(content, source, name, index, chunk_type, max_chars=500, overlap=50, use_semantic=False):
    """
    将长文本内容分割成小块。
    支持语义分块和基于字符的分块。
    """
    if len(content) <= max_chars:
        return [{
            'source': source,
            'name': f'{name} - {chunk_type.capitalize()} {index+1}',
            'description': content,
            'path': str(Path(app.config['UPLOAD_FOLDER']) / name),
            'chunk_id': f'{source}_{chunk_type}_{index}',
            'chunk_type': chunk_type,
            'chunk_index': index,
            'metadata': {
                'original_length': len(content),
                'is_split': False
            }
        }]
    
    chunks = []
    start = 0
    chunk_counter = 0
    
    if use_semantic:
        sentences = []
        for sent in content.replace('。', '.').replace('？', '?').replace('！', '!').split('.'):
            if sent.strip():
                for subsent in sent.split('?'):
                    if subsent.strip():
                        for final_sent in subsent.split('!'):
                            if final_sent.strip():
                                sentences.append(final_sent.strip() + '.')
        
        if not sentences:
            use_semantic = False
        else:
            current_chunk = ''
            for sentence in sentences:
                if len(current_chunk) + len(sentence) > max_chars and current_chunk:
                    chunks.append({
                        'source': source,
                        'name': f'{name} - {chunk_type.capitalize()} {index+1} - Part {chunk_counter+1}',
                        'description': current_chunk,
                        'path': str(Path(app.config['UPLOAD_FOLDER']) / name),
                        'chunk_id': f'{source}_{chunk_type}_{index}_part_{chunk_counter}',
                        'chunk_type': chunk_type,
                        'chunk_index': f'{index}.{chunk_counter}',
                        'metadata': {
                            'original_length': len(current_chunk),
                            'is_split': True,
                            'split_method': 'semantic'
                        }
                    })
                    words = current_chunk.split()
                    if len(words) > 5:
                        overlap_text = ' '.join(words[-min(len(words)//3, 20):])
                        current_chunk = overlap_text + ' '
                    else:
                        current_chunk = ''
                    chunk_counter += 1
                current_chunk += sentence + ' '
            
            if current_chunk.strip():
                chunks.append({
                    'source': source,
                    'name': f'{name} - {chunk_type.capitalize()} {index+1} - Part {chunk_counter+1}',
                    'description': current_chunk.strip(),
                    'path': str(Path(app.config['UPLOAD_FOLDER']) / name),
                    'chunk_id': f'{source}_{chunk_type}_{index}_part_{chunk_counter}',
                    'chunk_type': chunk_type,
                    'chunk_index': f'{index}.{chunk_counter}',
                    'metadata': {
                        'original_length': len(current_chunk),
                        'is_split': True,
                        'split_method': 'semantic'
                    }
                })
    
    if not use_semantic:
        while start < len(content):
            end = start + max_chars
            if end < len(content):
                end = content.rfind('.', start, end)
                if end == -1 or end <= start:
                    # 如果没找到句号，尝试找换行符
                    end = content.rfind("\n", start, end)
                if end == -1 or end <= start:
                    # 如果没找到换行符，尝试找逗号
                    end = content.rfind(",", start, end)
                if end == -1 or end <= start:
                    # 如果没找到任何标点，就直接在词边界处截断
                    end = content.rfind(" ", start, start + max_chars)
                if end == -1 or end <= start:
                    # 如果都没找到，就直接在最大长度处截断
                    end = start + max_chars
            
            chunk_content = content[start:end].strip()
            if chunk_content:
                chunks.append({
                    'source': source,
                    'name': f'{name} - {chunk_type.capitalize()} {index+1} - Part {chunk_counter+1}',
                    'description': chunk_content,
                    'path': str(Path(app.config['UPLOAD_FOLDER']) / name),
                    'chunk_id': f'{source}_{chunk_type}_{index}_part_{chunk_counter}',
                    'chunk_type': chunk_type,
                    'chunk_index': f'{index}.{chunk_counter}',
                    'metadata': {
                        'original_length': len(chunk_content),
                        'is_split': True,
                        'split_method': 'character'
                    }
                })
            if end < len(content):
                start = max(start + max_chars - overlap, end - overlap)
            else:
                start = end
            chunk_counter += 1
    
    return chunks

def load_documents(chunk_size=500, chunk_overlap=50, use_semantic_chunking=False):
    """加载并处理所有文档"""
    docs = []
    
    # 处理 JSON 文件
    for file in Path(app.config['UPLOAD_FOLDER']).glob('*.json'):
        with open(file, 'r', encoding='utf-8') as f:
            content = json.load(f)
            if isinstance(content, dict) and 'records' in content:
                content = content['records']
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        item = {'content': str(item)}
                    item['source'] = file.stem
                    item['path'] = str(file)
                    docs.append(item)
            else:
                doc = {'content': str(content), 'source': file.stem, 'path': str(file)}
                docs.append(doc)
    
    # 处理 Excel 文件
    for file in Path(app.config['UPLOAD_FOLDER']).glob('*.xlsx'):
        try:
            df = pd.read_excel(file)
            for index, row in df.iterrows():
                content = ' '.join([f"{col}: {val}" for col, val in row.items() if pd.notna(val)])
                doc = {
                    'source': file.stem,
                    'name': f'{file.stem} - 行 {index + 1}',
                    'description': content,
                    'path': str(file),
                    'chunk_id': f'{file.stem}_row_{index}',
                    'chunk_type': 'excel_row',
                    'chunk_index': index,
                    'metadata': {
                        'row_number': index + 1,
                        'columns': list(row.index)
                    }
                }
                docs.append(doc)
        except Exception as e:
            logging.warning(f'读取Excel文件 {file} 时出错：{e}')
    
    # 处理 Markdown 和 HTML 文件
    for ext in ['*.md', '*.html']:
        for file in Path(app.config['UPLOAD_FOLDER']).glob(ext):
            with open(file, 'r', encoding='utf-8') as f:
                doc = {
                    'source': file.stem,
                    'name': file.name,
                    'description': f.read(),
                    'path': str(file)
                }
                docs.append(doc)
    
    # 处理 Word 文档
    try:
        from docx import Document
        for file in Path(app.config['UPLOAD_FOLDER']).glob('*.docx'):
            doc = Document(file)
            meta_info = {
                'title': doc.core_properties.title or file.stem,
                'author': doc.core_properties.author or '未知作者',
                'created': str(doc.core_properties.created or '未知时间'),
                'last_modified': str(doc.core_properties.modified or '未知时间')
            }
            docs.append({
                'source': file.stem,
                'name': f'{file.name} - 元数据',
                'description': f'标题: {meta_info["title"]}\n作者: {meta_info["author"]}\n创建时间: {meta_info["created"]}\n修改时间: {meta_info["last_modified"]}',
                'path': str(file),
                'chunk_id': f'{file.stem}_metadata',
                'chunk_type': 'metadata',
                'chunk_index': 0,
                'metadata': meta_info
            })
            for i, para in enumerate(doc.paragraphs):
                content = para.text.strip()
                if content:
                    chunks = split_long_content(content, file.stem, file.name, i, 'paragraph', 
                                             max_chars=chunk_size, overlap=chunk_overlap, 
                                             use_semantic=use_semantic_chunking)
                    docs.extend(chunks)
    except ImportError:
        logging.warning('警告：未安装 python-docx 模块，无法加载 Word 文档')
    
    # 处理 PDF 文件
    try:
        import PyPDF2
        for file in Path(app.config['UPLOAD_FOLDER']).glob('*.pdf'):
            with open(file, 'rb') as f:
                pdf_reader = PyPDF2.PdfReader(f)
                meta_info = {}
                if pdf_reader.metadata:
                    meta_info = {
                        'title': pdf_reader.metadata.get('/Title', file.stem),
                        'author': pdf_reader.metadata.get('/Author', '未知作者'),
                        'created': pdf_reader.metadata.get('/CreationDate', '未知时间'),
                        'producer': pdf_reader.metadata.get('/Producer', '未知生产者')
                    }
                else:
                    meta_info = {
                        'title': file.stem,
                        'author': '未知作者',
                        'pages': len(pdf_reader.pages)
                    }
                docs.append({
                    'source': file.stem,
                    'name': f'{file.name} - 元数据',
                    'description': f'标题: {meta_info.get("title")}\n作者: {meta_info.get("author")}\n页数: {len(pdf_reader.pages)}',
                    'path': str(file),
                    'chunk_id': f'{file.stem}_metadata',
                    'chunk_type': 'metadata',
                    'chunk_index': 0,
                    'metadata': meta_info
                })
                for i, page in enumerate(pdf_reader.pages):
                    content = page.extract_text().strip()
                    if content:
                        chunks = split_long_content(content, file.stem, file.name, i, 'page',
                                                 max_chars=chunk_size, overlap=chunk_overlap,
                                                 use_semantic=use_semantic_chunking)
                        docs.extend(chunks)
    except ImportError:
        logging.warning('警告：未安装 PyPDF2 模块，无法加载 PDF 文档')
    except Exception as e:
        logging.warning(f'警告：加载 PDF 文档时出错：{e}')
    
    return docs

def format_doc(doc):
    """格式化文档内容用于向量化"""
    source = doc.get('source', '未知来源')
    name = doc.get('name', '未知名称')
    chunk_type = doc.get('chunk_type', '')
    
    # 特殊处理Excel数据
    if chunk_type == 'excel_row':
        description = doc.get('description', '暂无描述')
        row_number = doc.get('metadata', {}).get('row_number', '未知行号')
        return f'[{source}] 第{row_number}行：{description}'
    
    # 处理其他类型的文档
    description = (
        doc.get('description') or
        doc.get('content') or
        doc.get('recommendation_reason') or
        doc.get('features') or
        doc.get('tags') or
        '暂无描述'
    )
    if isinstance(description, list):
        description = '，'.join(description)
    return f'[{source}] {name}：{description}'

def get_docs_hash(docs):
    """计算文档集合的哈希值"""
    m = hashlib.md5()
    for doc in docs:
        m.update(format_doc(doc).encode('utf-8'))
    return m.hexdigest()

def update_embeddings(file_path=None):
    """处理文档并更新向量索引"""
    try:
        # 加载所有文档
        docs = load_documents(chunk_size=500, chunk_overlap=50, use_semantic_chunking=True)
        if not docs:
            logging.warning('没有找到可处理的文档')
            return False
            
        # 格式化文档
        texts = [format_doc(doc) for doc in docs]
        
        # 生成向量嵌入（按需加载模型）
        try:
            logging.info(f"📚 开始为 {len(texts)} 个文档生成向量嵌入...")
            model = get_embedding_model()  # 这里会按需加载模型
            logging.info(f"🔢 正在生成向量嵌入...")
            embeddings = model.encode(texts, show_progress_bar=True)
            logging.info(f"✅ 向量生成完成，embedding shape: {embeddings.shape}")
        except Exception as e:
            logging.error(f"❌ 向量生成失败: {str(e)}")
            raise e
        
        # 计算文档哈希值
        docs_hash = get_docs_hash(docs)
        
        # 保存缓存
        cache_data = {
            'hash': docs_hash,
            'texts': texts,
            'embeddings': embeddings,
            'meta': [{'source': doc.get('source'), 'name': doc.get('name', ''), 'path': doc.get('path', '')} for doc in docs]
        }
        
        # 确保缓存目录存在
        cache_file = os.path.join(app.config['EMBEDDINGS_FOLDER'], 'embedding_cache.pkl')
        with open(cache_file, 'wb') as f:
            pickle.dump(cache_data, f)
        
        logging.info('✅ Embedding 已缓存到本地')
        
        # 清除内存中的缓存，下次查询时会重新加载
        clear_embedding_cache()
        
        # 更新全局状态：现在有向量缓存了
        global _has_vector_cache
        _has_vector_cache = True
        logging.info("🔄 向量缓存状态已更新，文档查询功能现已可用")
        
        return True
        
    except Exception as e:
        logging.error(f'Error processing documents: {str(e)}')
        raise e

# 登录相关路由已在第134-167行定义，这里删除重复定义

# 文档管理路由
@app.route('/document')
@login_required
def document_management():
    files = []
    for filename in os.listdir(app.config['UPLOAD_FOLDER']):
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.isfile(file_path):
            file_size = os.path.getsize(file_path)
            # 转换文件大小为可读格式
            if file_size < 1024:
                size_str = f"{file_size} B"
            elif file_size < 1024 * 1024:
                size_str = f"{file_size/1024:.1f} KB"
            else:
                size_str = f"{file_size/(1024*1024):.1f} MB"
                
            files.append({
                'id': filename,
                'filename': filename,
                'size': size_str,
                'type': os.path.splitext(filename)[1].lstrip('.').upper() or 'Unknown',
                'modified': datetime.datetime.fromtimestamp(
                    os.path.getmtime(file_path)
                ).strftime('%Y-%m-%d %H:%M:%S')
            })
    return render_template('doc.html', documents=files)

# 基本设置管理路由
@app.route('/settings')
@login_required
def basic_settings():
    """基本设置管理页面"""
    # 复用doc.html模板，但设置默认显示基本设置页面
    return render_template('doc.html', documents=[], default_view='settings')

@app.route('/upload', methods=['POST'])
@login_required
@unified_error_handler('json')
def upload_file():
    """文件上传路由（支持向量索引更新）"""
    # 验证上传请求
    success, error_info, file = validate_upload_request(request)
    if not success:
        return success, error_info
    
    # 处理文件上传（不自动更新索引）
    success, info, filename = handle_file_upload_core(
        file, app.config['UPLOAD_FOLDER'], update_index=False
    )
    
    if success:
        return jsonify({'success': True, 'msg': info['message'], 'filename': filename})
    else:
        return success, info

@app.route('/delete/<filename>')
@login_required
@unified_error_handler('flash')
def delete_file(filename):
    """删除文件路由（使用flash消息）"""
    return handle_file_deletion_core(filename, app.config['UPLOAD_FOLDER'])

@app.route('/download/<filename>')
@login_required
@unified_error_handler('flash')
def download_file(filename):
    """下载文件路由"""
    try:
        # 使用安全的文件路径构建
        file_path = safe_file_path(filename, app.config['UPLOAD_FOLDER'])
        if not os.path.exists(file_path):
            return ErrorHandler.handle_error(
                ErrorHandler.NOT_FOUND_ERROR,
                '文件不存在'
            )
        return send_file(file_path, as_attachment=True)
    except ValueError as e:
        return ErrorHandler.handle_error(
            ErrorHandler.SECURITY_ERROR,
            str(e)
        )

# API路由
@app.route('/api/files', methods=['GET'])
@login_required
@json_error_handler
def api_list_files():
    """获取文件列表API"""
    files = get_file_list_data(app.config['UPLOAD_FOLDER'])
    return jsonify({'files': files})

@app.route('/api/upload', methods=['POST'])
@login_required
@unified_error_handler('json')
def api_upload_file():
    """API文件上传路由（不更新向量索引）"""
    # 验证上传请求
    success, error_info, file = validate_upload_request(request)
    if not success:
        return success, error_info
    
    # 处理文件上传（不自动更新索引）
    success, info, filename = handle_file_upload_core(
        file, app.config['UPLOAD_FOLDER'], update_index=False
    )
    
    if success:
        return jsonify({'success': True, 'msg': info['message'], 'filename': filename})
    else:
        return success, info

@app.route('/api/index_status', methods=['GET'])
@login_required
@unified_error_handler('json')
def get_index_status():
    """获取索引状态API"""
    cache_file = os.path.join(app.config['EMBEDDINGS_FOLDER'], 'embedding_cache.pkl')
    
    if os.path.exists(cache_file):
        # 获取缓存文件信息
        mtime = os.path.getmtime(cache_file)
        cache_time = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
        cache_size = os.path.getsize(cache_file) / (1024 * 1024)  # Convert to MB
        
        return ErrorHandler.handle_success(
            '索引缓存存在',
            {
                'hasCache': True,
                'cacheName': 'embedding_cache.pkl',
                'cacheTime': cache_time,
                'cacheSize': f'{cache_size:.2f}MB'
            }
        )
    else:
        return ErrorHandler.handle_success(
            '暂无索引缓存',
            {'hasCache': False}
        )

@app.route('/api/delete/<filename>', methods=['DELETE'])
@login_required
@unified_error_handler('json')
def api_delete_file(filename):
    """API删除文件路由（返回JSON响应）"""
    return handle_file_deletion_core(filename, app.config['UPLOAD_FOLDER'])

@app.route('/api/model_status', methods=['GET'])
@unified_error_handler('json')
def api_model_status():
    """获取模型状态API"""
    status = get_model_status()
    return ErrorHandler.handle_success(
        '模型状态查询成功',
        status
    )

@app.route('/api/generate_index', methods=['POST'])
@login_required
@unified_error_handler('json')
def api_generate_index():
    """生成向量索引API"""
    # 检查是否有可处理的文件
    files = []
    for filename in os.listdir(app.config['UPLOAD_FOLDER']):
        if os.path.isfile(os.path.join(app.config['UPLOAD_FOLDER'], filename)):
            files.append(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    
    if not files:
        return ErrorHandler.handle_error(
            ErrorHandler.VALIDATION_ERROR,
            '没有找到可处理的文件'
        )
        
    # 直接调用update_embeddings函数生成向量索引
    if update_embeddings():
        cache_file = os.path.join(app.config['EMBEDDINGS_FOLDER'], 'embedding_cache.pkl')
        return ErrorHandler.handle_success(
            '向量索引生成成功',
            {'cache_path': cache_file}
        )
    else:
        return ErrorHandler.handle_error(
            ErrorHandler.SERVER_ERROR,
            '向量索引生成失败：没有找到可处理的文档'
        )

@app.route('/api/doc_description/<filename>', methods=['GET', 'PUT'])
@login_required
@unified_error_handler('json')
def manage_doc_description(filename):
    """管理文档描述API"""
    try:
        # 验证文件是否存在
        file_path = safe_file_path(filename, app.config['UPLOAD_FOLDER'])
        if not os.path.exists(file_path):
            return ErrorHandler.handle_error(
                ErrorHandler.NOT_FOUND_ERROR,
                '文件不存在'
            )
        
        descriptions = load_doc_descriptions()
        
        if request.method == 'GET':
            # 获取文档描述
            description = descriptions.get(filename, '')
            return ErrorHandler.handle_success(
                '获取描述成功',
                {'filename': filename, 'description': description}
            )
        
        elif request.method == 'PUT':
            # 更新文档描述
            data = request.get_json() or {}
            new_description = data.get('description', '').strip()
            
            # 更新描述
            descriptions[filename] = new_description
            
            if save_doc_descriptions(descriptions):
                return ErrorHandler.handle_success(
                    '描述更新成功',
                    {'filename': filename, 'description': new_description}
                )
            else:
                return ErrorHandler.handle_error(
                    ErrorHandler.SERVER_ERROR,
                    '保存描述失败'
                )
                
    except ValueError as e:
        return ErrorHandler.handle_error(
            ErrorHandler.SECURITY_ERROR,
            str(e)
        )

# ===============================
# 配置管理 API
# ===============================

def load_app_config():
    """加载应用配置"""
    try:
        config_file = app.config['APP_CONFIG_FILE']
        if os.path.exists(config_file):
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            # 返回默认配置
            return {
                'city_config': {
                    'name': '海口',
                    'updated_at': datetime.datetime.now().isoformat()
                }
            }
    except Exception as e:
        logging.error(f"加载应用配置失败: {e}")
        return {
            'city_config': {
                'name': '海口',
                'updated_at': datetime.datetime.now().isoformat()
            }
        }

def save_app_config(config_data):
    """保存应用配置"""
    try:
        config_file = app.config['APP_CONFIG_FILE']
        # 确保目录存在
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logging.error(f"保存应用配置失败: {e}")
        return False

def load_travel_purposes():
    """加载旅行目的配置"""
    try:
        purposes_file = app.config['TRAVEL_PURPOSES_FILE']
        if os.path.exists(purposes_file):
            with open(purposes_file, 'r', encoding='utf-8') as f:
                purposes = json.load(f)
                # 过滤掉未完成编辑的新增项目
                filtered_purposes = [p for p in purposes if p.get('name') != '新增旅游目的']
                return filtered_purposes
        else:
            # 返回默认旅行目的
            return [
                {'id': 1, 'name': '休闲度假'},
                {'id': 2, 'name': '亲子游玩'},
                {'id': 3, 'name': '美食探索'},
                {'id': 4, 'name': '文化体验'},
                {'id': 5, 'name': '看演唱会'},
                {'id': 6, 'name': '免税购物'},
                {'id': 7, 'name': '村落探索'},
                {'id': 8, 'name': '露营'},
                {'id': 9, 'name': '赶海'}
            ]
    except Exception as e:
        logging.error(f"加载旅行目的配置失败: {e}")
        return []

def save_travel_purposes(purposes_data):
    """保存旅行目的配置"""
    try:
        purposes_file = app.config['TRAVEL_PURPOSES_FILE']
        # 确保目录存在
        os.makedirs(os.path.dirname(purposes_file), exist_ok=True)
        
        with open(purposes_file, 'w', encoding='utf-8') as f:
            json.dump(purposes_data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logging.error(f"保存旅行目的配置失败: {e}")
        return False

def load_travel_preferences():
    """加载旅游偏好配置 - 仅支持新的简化格式"""
    try:
        preferences_file = app.config['TRAVEL_PREFERENCES_FILE']
        if os.path.exists(preferences_file):
            with open(preferences_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('travel_preferences', {})
        
        # 返回默认配置（新格式）
        return get_default_preferences()
    except Exception as e:
        logging.error(f"加载旅游偏好配置失败: {e}")
        return get_default_preferences()

def get_default_preferences():
    """获取默认偏好配置（扁平化混合格式）"""
    return {
        "住宿类型": [
            "经济型酒店",
            "商务酒店", 
            "度假酒店",
            "民宿客栈",
            "青年旅舍",
            {
                "type": "input",
                "name": "指定酒店品牌",
                "placeholder": "例如：全季、如家、汉庭"
            },
            {
                "type": "input", 
                "name": "特殊住宿需求",
                "placeholder": "例如：海景房、无烟房"
            }
        ],
        "餐饮选择": [
            "海南特色菜",
            "海鲜大排档",
            "火锅烧烤",
            "快餐简餐",
            "精品餐厅",
            "街边小食",
            {
                "type": "input",
                "name": "指定餐厅",
                "placeholder": "例如：麦当劳、肯德基"
            },
            {
                "type": "input",
                "name": "饮食禁忌",
                "placeholder": "例如：不吃辣、素食"
            }
        ],
        "出行方式": [
            "公共交通",
            "出租车网约车", 
            "自驾租车",
            "共享单车",
            "步行",
            {
                "type": "input",
                "name": "其他交通工具",
                "placeholder": "例如：摩托车、电动车"
            }
        ],
        "特殊需求": [
            "无障碍设施",
            "儿童友好",
            "老人友好", 
            "宠物友好",
            {
                "type": "input",
                "name": "健康需求",
                "placeholder": "例如：轮椅通道、过敏提醒"
            },
            {
                "type": "input",
                "name": "其他要求",
                "placeholder": "请描述其他特殊需求"
            }
        ]
    }

def save_travel_preferences(preferences_data):
    """保存旅游偏好配置"""
    try:
        preferences_file = app.config['TRAVEL_PREFERENCES_FILE']
        # 确保目录存在
        os.makedirs(os.path.dirname(preferences_file), exist_ok=True)
        
        # 构建完整的数据结构
        data = {
            'travel_preferences': preferences_data,
            'updated_at': datetime.datetime.now(beijing_tz).isoformat()
        }
        
        with open(preferences_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logging.error(f"保存旅游偏好配置失败: {e}")
        return False

@app.route('/api/config/city', methods=['GET', 'PUT'])
@unified_error_handler('json')
def manage_city_config():
    """城市配置管理API"""
    try:
        app_config = load_app_config()
        
        if request.method == 'GET':
            # 获取城市配置 - 不需要认证
            # 确保返回实际的城市配置，而不是默认值
            city_config = app_config.get('city_config', {'name': '海口'})
            logging.info(f"获取城市配置: {city_config}")
            return ErrorHandler.handle_success(
                '获取城市配置成功',
                {
                    'city_config': city_config,
                    'timestamp': datetime.datetime.now().isoformat()
                }
            )
        
        elif request.method == 'PUT':
            # 更新城市配置 - 需要认证
            if not session.get('logged_in'):
                return ErrorHandler.handle_error(
                    ErrorHandler.SECURITY_ERROR,
                    '需要管理员权限'
                )
            
            data = request.get_json() or {}
            city_name = data.get('name', '').strip()
            
            if not city_name:
                return ErrorHandler.handle_error(
                    ErrorHandler.VALIDATION_ERROR,
                    '城市名称不能为空'
                )
            
            # 更新配置
            app_config['city_config'] = {
                'name': city_name,
                'updated_at': datetime.datetime.now().isoformat()
            }
            
            if save_app_config(app_config):
                logging.info(f"城市配置已更新为: {city_name}")
                # 更新全局变量
                update_current_city()
                return ErrorHandler.handle_success(
                    f'城市配置已保存: {city_name}',
                    {
                        'city_config': app_config['city_config'],
                        'timestamp': datetime.datetime.now().isoformat()
                    }
                )
            else:
                return ErrorHandler.handle_error(
                    ErrorHandler.SERVER_ERROR,
                    '保存城市配置失败'
                )
    
    except Exception as e:
        logging.error(f"城市配置管理出错: {e}")
        return ErrorHandler.handle_error(
            ErrorHandler.SERVER_ERROR,
            '城市配置操作失败',
            str(e)
        )

@app.route('/api/config/travel_purposes', methods=['GET', 'PUT', 'POST', 'DELETE'])
@unified_error_handler('json')
def manage_travel_purposes():
    """旅行目的配置管理API"""
    try:
        purposes = load_travel_purposes()
        
        if request.method == 'GET':
            # 获取旅行目的配置 - 不需要认证
            return ErrorHandler.handle_success(
                '获取旅行目的配置成功',
                {
                    'travel_purposes': purposes,
                    'timestamp': datetime.datetime.now().isoformat()
                }
            )
        
        elif request.method == 'PUT':
            # 更新整个旅行目的列表 - 需要认证
            if not session.get('logged_in'):
                return ErrorHandler.handle_error(
                    ErrorHandler.SECURITY_ERROR,
                    '需要管理员权限'
                )
            
            data = request.get_json() or {}
            new_purposes = data.get('purposes', [])
            
            if not isinstance(new_purposes, list):
                return ErrorHandler.handle_error(
                    ErrorHandler.VALIDATION_ERROR,
                    '旅行目的数据格式不正确'
                )
            
            if save_travel_purposes(new_purposes):
                logging.info(f"旅行目的配置已更新，共{len(new_purposes)}项")
                return ErrorHandler.handle_success(
                    f'旅行目的配置已保存，共{len(new_purposes)}项',
                    {
                        'travel_purposes': new_purposes,
                        'timestamp': datetime.datetime.now().isoformat()
                    }
                )
            else:
                return ErrorHandler.handle_error(
                    ErrorHandler.SERVER_ERROR,
                    '保存旅行目的配置失败'
                )
        
        elif request.method == 'POST':
            # 添加新的旅行目的 - 需要认证
            if not session.get('logged_in'):
                return ErrorHandler.handle_error(
                    ErrorHandler.SECURITY_ERROR,
                    '需要管理员权限'
                )
            data = request.get_json() or {}
            name = data.get('name', '').strip()
            icon = data.get('icon', 'fa-map-marker-alt').strip()
            
            if not name:
                return ErrorHandler.handle_error(
                    ErrorHandler.VALIDATION_ERROR,
                    '旅行目的名称不能为空'
                )
            
            # 生成新ID
            max_id = max([p.get('id', 0) for p in purposes]) if purposes else 0
            new_purpose = {
                'id': max_id + 1,
                'name': name,
                'icon': icon
            }
            
            purposes.append(new_purpose)
            
            if save_travel_purposes(purposes):
                logging.info(f"新增旅行目的: {name}")
                return ErrorHandler.handle_success(
                    f'添加旅行目的成功: {name}',
                    {
                        'new_purpose': new_purpose,
                        'travel_purposes': purposes,
                        'timestamp': datetime.datetime.now().isoformat()
                    }
                )
            else:
                return ErrorHandler.handle_error(
                    ErrorHandler.SERVER_ERROR,
                    '保存旅行目的失败'
                )
        
        elif request.method == 'DELETE':
            # 删除指定的旅行目的 - 需要认证
            if not session.get('logged_in'):
                return ErrorHandler.handle_error(
                    ErrorHandler.SECURITY_ERROR,
                    '需要管理员权限'
                )
            
            data = request.get_json() or {}
            purpose_id = data.get('id')
            
            if purpose_id is None:
                return ErrorHandler.handle_error(
                    ErrorHandler.VALIDATION_ERROR,
                    '请指定要删除的旅行目的ID'
                )
            
            # 查找并删除指定的旅行目的
            original_count = len(purposes)
            purposes = [p for p in purposes if p.get('id') != purpose_id]
            
            if len(purposes) == original_count:
                return ErrorHandler.handle_error(
                    ErrorHandler.NOT_FOUND_ERROR,
                    f'未找到ID为{purpose_id}的旅行目的'
                )
            
            if save_travel_purposes(purposes):
                logging.info(f"删除旅行目的ID: {purpose_id}")
                return ErrorHandler.handle_success(
                    f'删除旅行目的成功',
                    {
                        'deleted_id': purpose_id,
                        'travel_purposes': purposes,
                        'timestamp': datetime.datetime.now().isoformat()
                    }
                )
            else:
                return ErrorHandler.handle_error(
                    ErrorHandler.SERVER_ERROR,
                    '保存旅行目的配置失败'
                )
    
    except Exception as e:
        logging.error(f"旅行目的配置管理出错: {e}")
        return ErrorHandler.handle_error(
            ErrorHandler.SERVER_ERROR,
            '旅行目的配置操作失败',
            str(e)
        )

@app.route('/api/config/travel_preferences', methods=['GET', 'PUT'])
@unified_error_handler('json')
def manage_travel_preferences():
    """旅游偏好配置管理API"""
    try:
        if request.method == 'GET':
            # 获取旅游偏好配置 - 不需要认证
            preferences = load_travel_preferences()
            return ErrorHandler.handle_success(
                '获取旅游偏好配置成功',
                {
                    'travel_preferences': preferences,
                    'timestamp': datetime.datetime.now(beijing_tz).isoformat()
                }
            )
        
        elif request.method == 'PUT':
            # 更新旅游偏好配置 - 需要认证
            if not session.get('logged_in'):
                return ErrorHandler.handle_error(
                    ErrorHandler.SECURITY_ERROR,
                    '需要管理员权限'
                )
            
            data = request.get_json() or {}
            preferences = data.get('preferences', {})
            
            if not preferences:
                return ErrorHandler.handle_error(
                    ErrorHandler.VALIDATION_ERROR,
                    '旅游偏好配置不能为空'
                )
            
            # 验证新格式配置：直接是类别名-偏好项的映射
            for category_name, items in preferences.items():
                if not isinstance(items, list):
                    return ErrorHandler.handle_error(
                        ErrorHandler.VALIDATION_ERROR,
                        f'偏好类别 {category_name} 必须是数组格式'
                    )
                
                # 验证每个偏好项的格式（支持文档中的混合格式）
                for item in items:
                    if isinstance(item, str):
                        # 字符串格式的预定选项，符合文档规范
                        continue
                    elif isinstance(item, dict):
                        # 对象格式需要验证必要字段
                        if item.get('type') == 'input':
                            # 自定义输入项格式（按文档规范）
                            if 'name' not in item:
                                return ErrorHandler.handle_error(
                                    ErrorHandler.VALIDATION_ERROR,
                                    f'自定义输入项必须包含 name 字段'
                                )
                        else:
                            return ErrorHandler.handle_error(
                                ErrorHandler.VALIDATION_ERROR,
                                f'偏好项格式错误，对象类型必须是自定义输入项（type: "input"）'
                            )
                    else:
                        return ErrorHandler.handle_error(
                            ErrorHandler.VALIDATION_ERROR,
                            f'偏好项必须是字符串或对象格式'
                        )
            
            if save_travel_preferences(preferences):
                logging.info(f"更新旅游偏好配置成功，包含 {len(preferences)} 个类别")
                return ErrorHandler.handle_success(
                    '旅游偏好配置保存成功',
                    {
                        'travel_preferences': preferences,
                        'timestamp': datetime.datetime.now(beijing_tz).isoformat()
                    }
                )
            else:
                return ErrorHandler.handle_error(
                    ErrorHandler.SERVER_ERROR,
                    '保存旅游偏好配置失败'
                )
    
    except Exception as e:
        logging.error(f"旅游偏好配置管理出错: {e}")
        return ErrorHandler.handle_error(
            ErrorHandler.SERVER_ERROR,
            '旅游偏好配置操作失败',
            str(e)
        )

@app.route('/api/config/travel_preferences/categories', methods=['POST', 'DELETE'])
@unified_error_handler('json')
def manage_preference_categories():
    """偏好类别管理API"""
    try:
        # 需要管理员权限
        if not session.get('logged_in'):
            return ErrorHandler.handle_error(
                ErrorHandler.SECURITY_ERROR,
                '需要管理员权限'
            )
        
        if request.method == 'POST':
            # 添加新的偏好类别
            data = request.get_json() or {}
            category_id = data.get('id', '').strip()
            category_name = data.get('name', '').strip()
            category_icon = data.get('icon', 'fas fa-star').strip()
            
            if not category_name:
                return ErrorHandler.handle_error(
                    ErrorHandler.VALIDATION_ERROR,
                    '类别名称不能为空'
                )
            
            # 如果没有提供ID，自动生成
            if not category_id:
                import time
                category_id = f'category_{int(time.time())}'
            
            # 加载当前配置
            current_preferences = load_travel_preferences()
            
            # 检查是否已存在
            if 'categories' in current_preferences:
                if category_id in current_preferences['categories']:
                    return ErrorHandler.handle_error(
                        ErrorHandler.VALIDATION_ERROR,
                        f'类别ID {category_id} 已存在'
                    )
                
                # 计算新的排序值
                max_order = max([cat.get('order', 0) for cat in current_preferences['categories'].values()], default=0)
                
                # 添加新类别
                current_preferences['categories'][category_id] = {
                    'name': category_name,
                    'icon': category_icon,
                    'order': max_order + 1
                }
                
                # 初始化空的偏好列表
                if 'preferences' not in current_preferences:
                    current_preferences['preferences'] = {}
                current_preferences['preferences'][category_id] = []
            else:
                return ErrorHandler.handle_error(
                    ErrorHandler.VALIDATION_ERROR,
                    '数据格式不正确，缺少 categories 字段'
                )
            
            # 保存配置
            if save_travel_preferences(current_preferences):
                return ErrorHandler.handle_success(
                    f'偏好类别 {category_name} 已添加',
                    {
                        'category_id': category_id,
                        'category': current_preferences['categories'][category_id]
                    }
                )
            else:
                return ErrorHandler.handle_error(
                    ErrorHandler.SERVER_ERROR,
                    '保存偏好类别失败'
                )
        
        elif request.method == 'DELETE':
            # 删除偏好类别
            data = request.get_json() or {}
            category_id = data.get('id', '').strip()
            
            if not category_id:
                return ErrorHandler.handle_error(
                    ErrorHandler.VALIDATION_ERROR,
                    '类别ID不能为空'
                )
            
            # 加载当前配置
            current_preferences = load_travel_preferences()
            
            if 'categories' not in current_preferences or category_id not in current_preferences['categories']:
                return ErrorHandler.handle_error(
                    ErrorHandler.VALIDATION_ERROR,
                    f'类别ID {category_id} 不存在'
                )
            
            # 删除类别和相关偏好
            category_name = current_preferences['categories'][category_id]['name']
            del current_preferences['categories'][category_id]
            if 'preferences' in current_preferences and category_id in current_preferences['preferences']:
                del current_preferences['preferences'][category_id]
            
            # 保存配置
            if save_travel_preferences(current_preferences):
                return ErrorHandler.handle_success(
                    f'偏好类别 {category_name} 已删除'
                )
            else:
                return ErrorHandler.handle_error(
                    ErrorHandler.SERVER_ERROR,
                    '删除偏好类别失败'
                )
    
    except Exception as e:
        logging.error(f"偏好类别管理出错: {e}")
        return ErrorHandler.handle_error(
            ErrorHandler.SERVER_ERROR,
            '偏好类别管理操作失败',
            str(e)
        )

# 文档管理路由已在第774-798行定义，这里删除重复定义

# 生成向量索引路由
@app.route('/generate_index', methods=['POST'])
@login_required
@unified_error_handler('json')
def generate_index():
    """生成向量索引路由（兼容旧接口）"""
    try:
        update_embeddings()
        return ErrorHandler.handle_success('向量索引已生成')
    except Exception as e:
        return ErrorHandler.handle_error(
            ErrorHandler.SERVER_ERROR,
            '生成索引时出错',
            f'Error generating index: {str(e)}'
        )

# 主页路由
@app.route('/')
def index():
    return app.send_static_file('index.html')


# 示例行程表接口：从数据缓存目录读取并返回JSON
@app.route('/api/sample-itinerary', methods=['GET'])
def get_sample_itinerary():
    try:
        sample_path = os.path.join(app.root_path, 'data', 'cache', 'travel_sample.json')
        with open(sample_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except FileNotFoundError:
        return jsonify({"status": "error", "message": "示例文件不存在"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# 管理端：读取与保存示例行程JSON（用于路线推荐页右侧文本框）
@app.route('/admin/sample-itinerary', methods=['GET', 'POST'])
@login_required
def admin_sample_itinerary():
    sample_path = os.path.join(app.config['CACHE_FOLDER'], 'travel_sample.json')
    try:
        if request.method == 'GET':
            with open(sample_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return jsonify({
                'code': 0,
                'data': data,
                'msg': 'ok'
            })

        # 保存覆盖
        payload = request.get_json(silent=True) or {}
        content = payload.get('content', '')
        if isinstance(content, str) and content.strip():
            try:
                parsed = json.loads(content)
            except Exception as e:
                return jsonify({'code': 1, 'msg': f'JSON解析失败: {str(e)}'}), 400
        else:
            parsed = payload.get('data')  # 兼容直接传对象
        if parsed is None:
            return jsonify({'code': 1, 'msg': '缺少待保存内容'}), 400

        # 写入文件
        with open(sample_path, 'w', encoding='utf-8') as f:
            json.dump(parsed, f, ensure_ascii=False, indent=2)

        return jsonify({'code': 0, 'msg': '保存成功'})
    except FileNotFoundError:
        return jsonify({'code': 1, 'msg': '示例文件不存在'}), 404
    except Exception as e:
        return jsonify({'code': 1, 'msg': str(e)}), 500


@app.route('/api/chat', methods=['POST'])
def chat():
    logging.info("进入 /api/chat 路由")
    
    # 更新当前城市配置
    update_current_city()
    
    try:
        data = request.json
        # logging.info(f"收到请求数据: {data}")
        messages = data.get('messages', [])
        current_itinerary = data.get('current_itinerary')  # 新增：接收当前行程表数据
        # logging.info(f"messages: {messages[-1]}")
        
        # 调试：打印current_itinerary的详细信息
        logging.info(f"调试 - current_itinerary 类型: {type(current_itinerary)}")
        logging.info(f"调试 - current_itinerary 内容: {current_itinerary}")
        if current_itinerary:
            logging.info("收到当前行程表数据，将启用短期记忆功能")
            if isinstance(current_itinerary, dict):
                logging.info(f"调试 - current_itinerary.keys(): {current_itinerary.keys()}")
                logging.info(f"调试 - current_itinerary.get('days'): {current_itinerary.get('days')}")
        else:
            logging.info("调试 - 未收到current_itinerary数据或数据为空")
        tool_use = []
        now_beijing = lambda: datetime.datetime.now(beijing_tz).isoformat()
        
        # 以下是原有的正常聊天处理逻辑
        # 1. 检查缓存和文档状态
        cache_status = check_cache_and_docs_status()
        
        # 2. 根据缓存状态动态构建系统提示词
        # 构建工具列表
        if cache_status['doc_query_available']:
            # 文档查询可用的情况
            tools_section = """
                【可用工具列表】
                系统支持以下5个工具，工具名称必须严格使用：
                1. "获取天气信息" - 查询指定城市的天气情况
                2. "搜索兴趣点" - 关键词搜索POI信息
                3. "附近搜索" - 以某个位置为中心搜索周边POI信息
                4. "目的地距离" - 测量两个地点之间的距离
                5. "文档查询" - 从本地知识库中检索相关信息并回答问题"""
        else:
            # 文档查询不可用的情况
            tools_section = """
                【可用工具列表】
                系统支持以下4个工具，工具名称必须严格使用：
                1. "获取天气信息" - 查询指定城市的天气情况
                2. "搜索兴趣点" - 关键词搜索POI信息
                3. "附近搜索" - 以某个位置为中心搜索周边POI信息
                4. "目的地距离" - 测量两个地点之间的距离"""
        
        # 构建工具名称列表
        tool_names_section = """                  * "获取天气信息"
                  * "搜索兴趣点" 
                  * "附近搜索"
                  * "目的地距离" """
        if cache_status['doc_query_available']:
            tool_names_section += """
                  * "文档查询" """
        
        # 工具数量文字
        tool_count = "5个名称之一" if cache_status['doc_query_available'] else "4个名称之一"

        system_message = {
            "role": "system",
            "content": f"""你是一个专业的{current_city}旅游规划助手。

【核心任务】分析用户需求，判断是否需要调用工具、生成行程表、分析行程或直接回答。

【可用工具类型】
- 天气信息查询
- 景点/酒店/餐厅搜索
- 附近地点搜索  
- 距离测量
- 本地知识库查询{"（当前可用）" if cache_status['doc_query_available'] else "（当前不可用）"}

【判断原则】
- 需要实时数据（天气、具体位置、营业信息、路线距离等）→ 回复"NEED_TOOLS"
- 需要具体的景点/酒店/餐厅推荐 → 回复"NEED_TOOLS"  
- 需要制定详细行程规划 → 回复"NEED_TOOLS"
- 用户明确要求生成、制定、安排行程表/日程 → 回复"ITINERARY_UPDATE"
- 用户询问如何整理之前的推荐成具体行程 → 回复"ITINERARY_UPDATE"
- 对话中已有充分信息，用户希望整合成可执行计划 → 回复"ITINERARY_UPDATE"
- 用户询问现有行程是否合理、时间安排、路线评估等分析性问题 → 回复"ITINERARY_ANALYZE"
- 用户要求分析、评估、点评当前行程表 → 回复"ITINERARY_ANALYZE"
- 可以基于常识直接回答的一般性问题 → 直接回答

【回复要求】
- 需要工具时：只回复"NEED_TOOLS"
- 需要生成行程表时：只回复"ITINERARY_UPDATE"
- 需要分析行程表时：只回复"ITINERARY_ANALYZE"
- 直接回答时：提供完整、专业的回答，使用Markdown格式，适当使用emoji

⚠️ **重要**：不要生成任何工具调用指令，只做判断。"""
        }
        messages.insert(0, system_message)
        # 记录新增加的消息到日志
        if messages:
            logging.info(f"[NEW_MESSAGE] role={messages[-1].get('role')} content={messages[-1].get('content')}\n")
        
        # 2. 初始化工具调用历史和用户问题
        user_question = messages[-1] if messages and messages[-1].get('role') == 'user' else {"role": "user", "content": ""}
        
        # 3. 三路由架构处理
        logging.info("路由架构判断")
        
        # 调用模型进行初始判断
        try:
            response = client.chat.completions.create(
                model=BASE_MODEL,
                messages=messages
            )
            initial_response = response.choices[0].message.content.strip()
            logging.info(f"初始判断结果: {initial_response}")
            
            # 根据响应类型进行路由
            if initial_response == "NEED_TOOLS":
                # 路由1: 工具调用
                logging.info("路由到工具调用处理")
                final_reply, tool_call_history, call_failed = reasoning_based_tool_calling(
                    user_question, messages, tool_use, now_beijing, cache_status['doc_query_available']
                )
                
                if call_failed:
                    return jsonify({
                        "status": "error", 
                        "message": final_reply
                    }), 500
                else:
                    return jsonify({
                        "status": "success",
                        "response": final_reply,
                        "tool_use": tool_use
                    })
                    
            elif initial_response == "ITINERARY_UPDATE":
                # 路由2: 行程表生成
                logging.info("路由到行程表生成")
                
                # 获取完整对话历史（不包括system_message）
                conversation_for_itinerary = messages[1:]  # 去掉system_message
                
                # 生成行程表，传递当前行程表数据支持短期记忆
                itinerary_result = generate_itinerary_from_conversation(
                    conversation_for_itinerary, 
                    current_itinerary  # 传递当前行程表数据
                )
                
                if itinerary_result["success"]:
                    # 检查是否是温馨提示
                    if itinerary_result.get("action") == "friendly_prompt":
                        logging.info("返回温馨提示给用户")
                        return jsonify({
                            "status": "success",
                            "response": itinerary_result["response"]
                        })
                    else:
                        # 正常的行程表生成
                        logging.info(f"行程表生成成功，准备返回给前端")
                        return jsonify({
                            "status": "success",
                            "response": "已为您生成行程表，请查看左边栏！",
                            "action": "generate_itinerary",
                            "itinerary": itinerary_result["itinerary"]
                        })
                else:
                    return jsonify({
                        "status": "error",
                        "message": f"生成行程表失败: {itinerary_result['error']}"
                    }), 500
                    
            elif initial_response == "ITINERARY_ANALYZE":
                # 路由3: 行程分析（新增）
                logging.info("路由到行程分析")
                
                # 分析行程表
                analysis_result = analyze_current_itinerary(
                    current_itinerary  # 只传递当前行程表数据
                )
                
                if analysis_result["success"]:
                    return jsonify({
                        "status": "success",
                        "response": analysis_result["response"],
                        "action": "analyze_itinerary"
                    })
                else:
                    return jsonify({
                        "status": "error",
                        "message": f"分析行程表失败: {analysis_result['error']}"
                    }), 500
                    
            else:
                # 路由4: 直接回答
                logging.info("路由到直接回答")
                return jsonify({
                    "status": "success",
                    "response": initial_response,
                    "tool_use": tool_use
                })
                
        except Exception as e:
            logging.error("四路由架构处理异常", exc_info=True)
            return jsonify({
                "status": "error",
                "message": f"处理请求时出错: {str(e)}"
            }), 500
    except Exception as e:
        logging.error("/api/chat 路由发生异常", exc_info=True)
        import traceback
        print(traceback.format_exc())
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


def format_mcp_data(mcp_data):
    """格式化MCP数据，便于LLM理解和回答"""
    if mcp_data["type"] == "weather":
        return format_weather_data(mcp_data)
    elif mcp_data["type"] == "location":
        return format_location_data(mcp_data)
    elif mcp_data["type"] == "poi":
        return format_poi_data(mcp_data)
    elif mcp_data["type"] == "direction":
        return format_direction_data(mcp_data)
    return json.dumps(mcp_data, ensure_ascii=False, indent=2)

def format_weather_data(mcp_data):
    """格式化天气数据"""
    data = mcp_data["data"]
    result = f"## {mcp_data['city']}天气信息\n\n"
    
    if data.get("forecasts") and len(data["forecasts"]) > 0:
        forecast = data["forecasts"][0]
        result += f"**预报时间**: {forecast.get('reporttime', '未知')}\n\n"
        
        result += "**未来几天天气预报**:\n\n"
        for cast in forecast.get("casts", []):
            result += f"- **{cast.get('date', '未知')}**: 白天 {cast.get('dayweather', '未知')} {cast.get('daytemp', '未知')}°C, 夜间 {cast.get('nightweather', '未知')} {cast.get('nighttemp', '未知')}°C\n"
    
    return result

def format_location_data(mcp_data):
    """格式化地理位置数据"""
    data = mcp_data["data"]
    result = f"## {mcp_data['address']}位置信息\n\n"
    
    if data.get("geocodes") and len(data["geocodes"]) > 0:
        geo = data["geocodes"][0]
        result += f"**完整地址**: {geo.get('formatted_address', '未知')}\n"
        result += f"**坐标**: {geo.get('location', '未知')}\n"
        result += f"**省份**: {geo.get('province', '未知')}\n"
        result += f"**城市**: {geo.get('city', '未知')}\n"
        result += f"**区县**: {geo.get('district', '未知')}\n"
    
    return result

def format_poi_data(mcp_data):
    """格式化POI数据，展示完整的搜索结果信息"""
    data = mcp_data["data"]
    result = f"## {mcp_data['city']}的{mcp_data['keywords']}信息\n\n"
    
    if data.get("pois") and len(data["pois"]) > 0:
        total_count = int(data.get("count", 0))
        pois = data["pois"]
        
        # 🔧 优化：当总结果数>20时，只处理前10条以节省token
        if total_count > 20:
            pois = pois[:10]  # 只取前10条
            result += f"> ⚠️ 共找到 {total_count} 条结果，为节省资源，仅展示前 10 条\n\n"
        else:
            result += f"共找到 {len(pois)} 条结果：\n\n"
        
    # 注意：之前这里错误地使用 data["pois"]，导致即使上面截断了也仍然遍历所有结果，引起回复过长被大模型截断
    for i, poi in enumerate(pois, 1):
            result += f"### {i}. {poi.get('name', '未知名称')}\n"
            
            # 类型信息
            if poi.get('type'):
                result += f"- **类型**: {poi.get('type')}\n"
            
            # 地理位置信息
            location_parts = []
            if poi.get('cityname'): location_parts.append(poi['cityname'])
            if poi.get('adname'): location_parts.append(poi['adname'])
            if poi.get('address'): location_parts.append(poi['address'])
            if location_parts:
                result += f"- **地址**: {' '.join(location_parts)}\n"
            
            # 坐标
            if poi.get('location'):
                result += f"- **坐标**: {poi.get('location')}\n"
            
            # ID信息
            if poi.get('id'):
                result += f"- **POI ID**: {poi.get('id')}\n"
            
            # 联系方式
            if poi.get('tel') and poi['tel']:
                result += f"- **电话**: {poi.get('tel')}\n"
            
            # 营业时间和评分
            if poi.get('biz_ext'):
                biz_ext = poi['biz_ext']
                if biz_ext.get('opentime2'):
                    result += f"- **开放时间**: {biz_ext.get('opentime2')}\n"
                if biz_ext.get('rating'):
                    result += f"- **评分**: ⭐{biz_ext.get('rating')}\n"
                if biz_ext.get('cost'):
                    result += f"- **参考价格**: ¥{biz_ext.get('cost')}\n"
                if biz_ext.get('level'):
                    result += f"- **等级**: {biz_ext.get('level')}\n"
            
            # 照片信息 - 只有在类型包含'风景名胜'或'景点'时才显示照片框
            if poi.get('photos') and poi['photos'] and ('风景名胜' in poi.get('type', '') or '景点' in poi.get('type', '')):
                photos = poi['photos'][:3]  # 最多显示3张图片
                result += "- **照片**:\n"
                # 仅当 poi['id'] 存在时才生成相片框
                if poi.get('id'):
                    poi_id = poi['id']
                    result += f'<div class="poi-photo-container" data-poi-index="{poi_id}">\n'
                    
                    # 添加翻页按钮 - `<` 放在左侧，`>` 放在右侧，只有照片数量大于1时显示
                    if len(photos) > 1:
                        # 使用单引号包裹参数，避免嵌套双引号破坏 HTML 属性，防止前端或模型截断
                        result += f"  <button class=\"poi-photo-nav poi-photo-nav-prev\" onclick=\"changePhoto(-1, '{poi_id}')\" style=\"left: 10px;\">&#10094;</button>\n"
                    
                    # 照片框架
                    result += '  <div class="poi-photo-frame">\n'
                    for j, photo in enumerate(photos):
                        display_style = "block" if j == 0 else "none"  # 默认显示第一张
                        result += f'    <img src="{photo.get("url")}" alt="{photo.get("title", "景点照片")}" class="poi-photo" style="display: {display_style};">\n'
                    result += '  </div>\n'
                    
                    if len(photos) > 1:
                        result += f"  <button class=\"poi-photo-nav poi-photo-nav-next\" onclick=\"changePhoto(1, '{poi_id}')\" style=\"right: 10px;\">&#10095;</button>\n"
                    
                    result += '</div>\n'
                else:
                    result += "(暂无唯一标识，无法显示照片框)\n"
            
            result += "\n"
    
    return result

def format_direction_data(mcp_data):
    """格式化路线数据"""
    result = f"## 从{mcp_data['origin']}到{mcp_data['destination']}的路线信息\n\n"
    
    driving_data = mcp_data.get("driving")
    walking_data = mcp_data.get("walking")
    transit_data = mcp_data.get("transit")
    
    if driving_data and driving_data.get("route") and driving_data["route"].get("paths") and len(driving_data["route"]["paths"]) > 0:
        path = driving_data["route"]["paths"][0]
        result += "### 驾车路线\n"
        result += f"- **距离**: {path.get('distance', '未知')}米 (约{round(int(path.get('distance', 0))/1000, 2)}公里)\n"
        duration_min = int(int(path.get('duration', 0))/60)
        result += f"- **预计耗时**: {duration_min}分钟\n\n"
        
        if path.get("steps") and len(path["steps"]) > 0:
            result += "**详细路线指引**:\n"
            for i, step in enumerate(path["steps"][:5], 1):  # 只显示前5步指引
                result += f"{i}. {step.get('instruction', '未知')}\n"
            if len(path["steps"]) > 5:
                result += "...(更多指引省略)\n"
        
        result += "\n"
    
    if walking_data and walking_data.get("route") and walking_data["route"].get("paths") and len(walking_data["route"]["paths"]) > 0:
        path = walking_data["route"]["paths"][0]
        result += "### 步行路线\n"
        result += f"- **距离**: {path.get('distance', '未知')}米 (约{round(int(path.get('distance', 0))/1000, 2)}公里)\n"
        duration_min = int(int(path.get('duration', 0))/60)
        result += f"- **预计耗时**: {duration_min}分钟\n"
        
        if int(path.get('distance', 0)) > 3000:
            result += "\n> ⚠️ 注意：步行距离较远，建议考虑其他交通方式\n"
    
    if transit_data and transit_data.get('route') and transit_data['route'].get('transits'):
        transits = transit_data['route']['transits']
        if len(transits) > 0:
            first = transits[0]
            duration_min = int(int(first.get('duration', 0))/60)
            result += "### 公交路线\n"
            result += f"- **预计耗时**: {duration_min}分钟\n"
            result += f"- **步行距离**: {first.get('walking_distance', '未知')}米\n"
            result += f"- **换乘次数**: {first.get('segments') and len(first['segments']) - 1 or 0}\n"
            if first.get('segments'):
                result += "- **换乘方案**:\n"
                for i, seg in enumerate(first['segments'], 1):
                    if seg.get('bus') and seg['bus'].get('buslines'):
                        line = seg['bus']['buslines'][0]
                        result += f"    {i}. 乘坐 {line.get('name', '未知线路')}，上车站：{line.get('departure_stop', {}).get('name', '未知')}，下车站：{line.get('arrival_stop', {}).get('name', '未知')}\n"
            result += "\n"
        else:
            result += "未查询到可用的公交方案。\n"
    
    return result

def build_dynamic_reasoning_prompt(doc_query_available=True):
    """根据文档查询可用性动态构建推理提示词"""
    base_prompt = f"""
背景：所在城市是{current_city}，用户会询问你关于{current_city}旅游的任何问题。
基于当前获得的工具调用结果，判断是否有足够信息完整回答用户问题。

判断标准：
- 用户问题的所有关键信息点是否都已获取？
- 是否还有明显缺失的数据？
- 当前信息是否足以给出满意的回答？
- 对于依赖调用场景：是否获得了执行下一步所需的参数？
- 对于独立调用场景：是否覆盖了用户询问的所有地点/事项？
- 对于附近搜索结果：如果搜索半径已满足用户要求，无需再次验证距离

返回格式：
SUFFICIENT: true/false
REASON: [详细的判断理由，说明已获得哪些信息，还缺少什么信息]
NEXT_INSTRUCTION: [如果不充分，生成下一条工具调用指令，格式为 [{{"name": "工具名", "parameters": {{...}}}}]]
"""
    
    if doc_query_available:
        tools_section = """
【可用工具列表】
系统支持以下5个工具，工具名称必须严格使用：
1. "获取天气信息" - 查询指定城市的天气情况
2. "搜索兴趣点" - 关键词搜索POI信息
3. "附近搜索" - 以某个位置为中心搜索周边POI信息
4. "目的地距离" - 测量两个地点之间的距离
5. "文档查询" - 从本地知识库中检索相关信息并回答问题
"""
        tool_names = '"获取天气信息"、"搜索兴趣点"、"附近搜索"、"目的地距离"、"文档查询"'
    else:
        tools_section = """
【可用工具列表】
系统支持以下4个工具，工具名称必须严格使用：
1. "获取天气信息" - 查询指定城市的天气情况
2. "搜索兴趣点" - 关键词搜索POI信息
3. "附近搜索" - 以某个位置为中心搜索周边POI信息
4. "目的地距离" - 测量两个地点之间的距离

⚠️ **重要提醒**：文档查询工具当前不可用（缺少文档或向量索引），不要尝试生成"文档查询"工具调用指令。
"""
        tool_names = '"获取天气信息"、"搜索兴趣点"、"附近搜索"、"目的地距离"'
    
    strategy_section = """
【多工具调用策略】
某些复杂查询可能需要多轮工具调用：
1. **依赖调用场景**：后续工具需要前面工具的结果
    - 示例："万绿园附近的酒店" → 先搜索万绿园获取坐标 → 再搜索附近酒店
2. **独立调用场景**：用户询问多个独立的地点或信息
    - 示例："五公祠和海南省博物馆的位置" → 分别搜索两个地点
3. **距离测量场景**：需要先获取两个地点的坐标
    - 示例："从A地到B地多远" → 搜索A地坐标 → 搜索B地坐标 → 计算距离
"""
    
    tools_details = """
工具使用说明：
获取天气信息：
## 工具说明：查询指定城市或地点的天气情况，适用于用户询问天气时。
## 参数：city（城市名或adcode，字符串）
## 调用工具的Prompt示例：
- "海口的天气如何？"
## 调用指令：
{"name": "获取天气信息", "parameters": {"city": "海口"}}

搜索兴趣点：
## 工具说明：关键词搜索某地的相关POI信息，适用于用户询问某地有什么好玩的、好吃的等。
## 参数：keywords（关键词，字符串），city（城市名，必填，字符串）
## 调用工具的Prompt示例：
- "海口的海甸岛上有什么豪华酒店？"
## 调用指令：
{"name": "搜索兴趣点", "parameters": {"keywords": "海甸岛豪华型酒店", "city": "海口"}}

附近搜索：
## 工具说明：以某个位置为中心点搜索周边POI信息，适用于用户询问"附近"、"周边"等场景。
## 参数说明：
1. location（必填）：中心点经纬度
   - 格式：经度,纬度（不带引号）
   - 示例：110.312589,20.055793
   - 注意：经纬度之间用英文逗号分隔，不要带空格
   - ⚠️ 重要：如果不知道地点的经纬度，需要先调用"搜索兴趣点"工具获取
2. keywords（必填）：搜索关键词
   - 示例："酒店"、"餐厅"、"景点"等
3. types（必填）：POI类型
   - 可以为空字符串：""
4. radius（必填）：搜索半径
   - 单位：米
   - 示例：1000（表示1公里）
   - 默认值：1000
## 调用示例：
- "万绿园附近有什么酒店？"
## 调用逻辑：
1. 如果不知道万绿园的经纬度，先调用：{"name": "搜索兴趣点", "parameters": {"keywords": "万绿园", "city": "海口"}}
2. 获得经纬度后，再调用：{"name": "附近搜索", "parameters": {"location": "110.312589,20.055793", "keywords": "酒店", "types": "", "radius": 1000}}

目的地距离：
## 工具说明：测量两个地点之间的距离，适用于用户询问距离、路程等场景。
## 参数：
- origin（起点经纬度，必填，字符串，格式为"经度,纬度"）
- destination（终点经纬度，必填，字符串，格式为"经度,纬度"）
- type（距离类型，可选，字符串，默认"1"）
  * "0"：直线距离
  * "1"：驾车导航距离
  * "3"：步行导航距离
## 调用工具的Prompt示例：
- "从海口湾广场到假日海滩有多远？"
## 调用逻辑：
⚠️ 如果不知道地点的经纬度，需要先分别调用"搜索兴趣点"工具获取起点和终点的坐标
1. 搜索起点：{{"name": "搜索兴趣点", "parameters": {{"keywords": "海口湾广场", "city": "海口"}}}}
2. 搜索终点：{{"name": "搜索兴趣点", "parameters": {{"keywords": "假日海滩", "city": "海口"}}}}
3. 计算距离：{"name": "目的地距离", "parameters": {"origin": "110.312589,20.055793", "destination": "110.237390,20.036904"}}
"""
    
    if doc_query_available:
        # 获取文档列表
        cache_status = check_cache_and_docs_status()
        doc_list_section = ""
        if cache_status.get('doc_list'):
            doc_list_section = "\n## 可查询的文档资料：\n"
            for doc in cache_status['doc_list']:
                doc_list_section += f"- {doc['name']}: {doc['description']}\n"
        
        doc_query_details = """
文档查询：
## 工具说明：从本地知识库中检索相关信息并回答用户问题，适用于询问海南旅游攻略、免税政策、酒店餐厅推荐等本地知识。""" + doc_list_section + """
## ⚠️ **重要**：文档查询的范围仅限于上述文档列表中的内容，只有当用户问题与列表中的文档内容相关时才使用此工具！
## 参数：query（检索关键词，字符串）- **重要**：需要根据用户的完整问题提取核心检索关键词，去除语气词、无关信息，生成简洁、准确的查询词
## 参数生成原则：
- 从用户问题中提取最核心的查询意图
- 去除语气词、感叹词、无关的修饰语
- 保留关键的地点、类型、特征等实体信息
- 使用简洁明了的关键词组合
## 调用工具的Prompt示例：
- 用户问："哎呀，我想知道海南免税购物到底有什么限制啊？听说挺复杂的" → query: "海南免税购物限制"
- 用户问："能不能推荐一些海口的特色餐厅，我比较喜欢海鲜" → query: "海口特色海鲜餐厅"
- 用户问："我们一家三口要去海口旅游，孩子8岁，有什么适合亲子游的酒店吗？" → query: "海口亲子酒店"
## 调用指令：
{"name": "文档查询", "parameters": {"query": "海南免税购物限制"}}
"""
    else:
        doc_query_details = ""
    
    final_instructions = f"""
【重要指令】
- 工具名称必须严格使用以下工具名称：{tool_names}
- 有经纬度参数都必须用英文双引号包裹，作为字符串传递，例如"110.237390,20.036904"
- 参数必须按照顺序提供：location -> keywords -> types -> radius
- location 参数不要带双引号
- types 参数即使为空也必须传入空字符串
- radius 参数可以是数字或字符串类型
"""
    
    return base_prompt + tools_section + strategy_section + tools_details + doc_query_details + final_instructions

def build_context_for_llm_call(user_question, tool_call_history, call_type, doc_query_available=True):
    """为不同类型的LLM调用构建完整上下文"""
    context = [user_question]  # 始终以用户问题开头
    
    # 添加所有工具调用历史
    for record in tool_call_history:
        context.append({
            "role": "assistant",
            "content": record["instruction"]
        })
        context.append({
            "role": "system", 
            "content": f"MCP工具返回信息：\n{record['result']}"
        })
    
    # 根据调用类型添加相应的系统提示词
    if call_type == "reasoning":
        # 根据文档查询可用性动态构建推理提示词
        reasoning_prompt = build_dynamic_reasoning_prompt(doc_query_available)
        context.append({
            "role": "system",
            "content": reasoning_prompt
        })
    elif call_type == "final_response":
        context.append({
            "role": "system",
            "content": FINAL_RESPONSE_SYSTEM_PROMPT
        })
    
    return context

def detect_tool_call_loop(tool_call_history):
    """检测工具调用循环，避免无限重复"""
    if len(tool_call_history) < LOOP_DETECTION_WINDOW:
        return False
    
    # 提取最近的工具调用签名
    recent_instructions = [
        record["instruction"] 
        for record in tool_call_history[-LOOP_DETECTION_WINDOW:]
    ]
    
    # 检查是否有重复的工具调用
    return len(recent_instructions) != len(set(recent_instructions))

def optimize_context_length(context, max_tokens=MAX_CONTEXT_LENGTH):
    """智能压缩上下文，保留最重要信息"""
    # 简单实现：估算token数量
    estimated_tokens = sum(len(str(msg.get("content", ""))) // 4 for msg in context)
    
    if estimated_tokens <= max_tokens:
        return context
    
    # 保留策略：用户问题 + 最近N轮工具调用 + 系统提示词
    if len(context) <= 3:
        return context
        
    user_question = context[0]
    system_prompt = context[-1]
    
    # 保留最近的工具调用历史
    middle_content = context[1:-1]
    # 保留最近6条消息（约3轮工具调用）
    optimized_middle = middle_content[-6:] if len(middle_content) > 6 else middle_content
    
    return [user_question] + optimized_middle + [system_prompt]

def parse_reasoning_result(llm_reply):
    """解析LLM的推理判断结果"""
    try:
        # 解析SUFFICIENT字段
        sufficient_match = re.search(r'SUFFICIENT:\s*(true|false)', llm_reply, re.IGNORECASE)
        sufficient = sufficient_match.group(1).lower() == 'true' if sufficient_match else False
        
        # 解析REASON字段
        reason_match = re.search(r'REASON:\s*(.+?)(?=\nNEXT_INSTRUCTION|$)', llm_reply, re.DOTALL)
        reason = reason_match.group(1).strip() if reason_match else "无法解析推理原因"
        
        # 解析NEXT_INSTRUCTION字段
        next_instruction = None
        if not sufficient:
            instruction_match = re.search(r'NEXT_INSTRUCTION:\s*(.+?)$', llm_reply, re.DOTALL | re.MULTILINE)
            if instruction_match:
                next_instruction = instruction_match.group(1).strip()
                # 尝试提取JSON格式的工具调用指令
                json_match = re.search(r'\[(.*?)\]', next_instruction, re.DOTALL)
                if json_match:
                    next_instruction = json_match.group(0)
        
        return {
            "sufficient": sufficient,
            "reason": reason,
            "next_instruction": next_instruction
        }
    except Exception as e:
        logging.error(f"解析推理结果失败: {str(e)}")
        return {
            "sufficient": True,  # 默认终止，避免无限循环
            "reason": "解析推理结果失败，强制终止",
            "next_instruction": None
        }

def analyze_information_sufficiency(user_question, tool_call_history, doc_query_available=True):
    """LLM分析信息充分性并决定下一步行动"""
    try:
        # 构建推理上下文
        context = build_context_for_llm_call(user_question, tool_call_history, "reasoning", doc_query_available)
        
        # 优化上下文长度
        context = optimize_context_length(context)
        
        # 输出发送给推理模型的上下文
        logging.info(f"[CONTEXT_TO_REASONING] 使用模型: {REASONING_MODEL}, 超时: {REASONING_TIMEOUT}秒")
        logging.info(f"[CONTEXT_TO_REASONING] 发送给推理模型的上下文:\n{format_context_for_debug(context, full_output_for_reasoning=True)}")
        
        # 调用LLM进行推理判断
        completion = client.chat.completions.create(
            model=REASONING_MODEL,
            messages=context,
            timeout=REASONING_TIMEOUT
        )
        
        llm_reply = completion.choices[0].message.content
        logging.info(f"[REASONING_REPLY] {llm_reply}")
        
        # 解析推理结果
        return parse_reasoning_result(llm_reply)
        
    except Exception as e:
        logging.error(f"推理判断失败: {str(e)}")
        return {
            "sufficient": True,  # 出错时默认终止
            "reason": f"推理判断失败: {str(e)}",
            "next_instruction": None
        }

def reasoning_based_tool_calling(user_question, initial_messages, tool_use, now_beijing, doc_query_available=True):
    """
    基于推理判断的多工具调用核心算法
    
    流程：
    1. 对话阶段：理解用户意图，决定是否需要工具调用
    2. 循环执行：工具调用 → 推理判断 → 继续或结束
    3. 最终回复：基于工具结果生成用户友好的回复
    """
    tool_call_history = []
    iteration = 0
    
    # 对话阶段：处理用户输入，决定是否需要工具调用
    try:
        # 输出发送给对话模型的上下文
        logging.info(f"[CONTEXT_TO_CHAT] 使用模型: {TOOL_GENERATION_MODEL}")
        logging.info(f"[CONTEXT_TO_CHAT] 发送给对话模型的上下文:\n{format_context_for_debug(initial_messages)}")
        
        completion = client.chat.completions.create(
            model=TOOL_GENERATION_MODEL,
            messages=initial_messages,
        )
        llm_reply = completion.choices[0].message.content
        logging.info(f"[CHAT_REPLY] {llm_reply}")
        
        # 检查是否需要工具调用
        if "NEED_TOOLS" in llm_reply:
            # 需要工具调用，让推理模型来决定第一个工具调用
            logging.info("[CHAT_DECISION] 对话模型判断需要工具调用，转交推理模型处理")
            # 直接进入推理阶段，让推理模型生成第一个工具调用指令
            reasoning_result = analyze_information_sufficiency(user_question, tool_call_history, doc_query_available)
            if reasoning_result["next_instruction"]:
                current_instruction = reasoning_result["next_instruction"]
            else:
                return "抱歉，无法确定需要调用什么工具。", tool_call_history, True
        else:
            # 不需要工具调用，直接返回LLM回复
            logging.info("[CHAT_DECISION] 对话模型判断不需要工具调用，直接回复")
            return llm_reply, tool_call_history, False
        
    except Exception as e:
        logging.error(f"对话阶段处理失败: {str(e)}")
        return "抱歉，对话处理失败。", tool_call_history, True
    
    # 循环执行工具调用和推理判断
    while iteration < MAX_TOOL_ITERATIONS:
        iteration += 1
        logging.info(f"[ITERATION] 第{iteration}轮工具调用")
        
        # 检测循环
        if detect_tool_call_loop(tool_call_history):
            logging.warning("检测到工具调用循环，终止执行")
            break
        
        # 解析和执行工具调用
        try:
            tool_calls = json.loads(current_instruction)
            if not tool_calls or not isinstance(tool_calls, list):
                break
                
            tool = tool_calls[0]
            tool_name = tool.get("name")
            params = tool.get("parameters", {})
            
            # 记录工具调用
            tool_use.append({
                "type": "tool_call",
                "icon": "⚙️",
                "title": f"调用[{tool_name}]工具......",
                "tool_name": tool_name,
                "content": json.dumps(params, ensure_ascii=False),
                "timestamp": now_beijing(),
                "collapsible": True
            })
            
            # 执行MCP工具调用
            tool_result, tool_failed = call_mcp_tool_and_format_result(
                tool_name, params, tool_use, now_beijing, mcp_client
            )
            
            if tool_failed:
                logging.error(f"工具调用失败: {tool_name}")
                break
            
            # 更新工具调用历史
            tool_call_history.append({
                "instruction": current_instruction,
                "result": tool_result,
                "timestamp": now_beijing(),
                "iteration": iteration
            })
            
            logging.info(f"[TOOL_RESULT] 第{iteration}轮工具调用完成")
            
        except Exception as e:
            logging.error(f"执行工具调用失败: {str(e)}")
            break
        
        # 推理判断信息充分性
        reasoning_result = analyze_information_sufficiency(user_question, tool_call_history, doc_query_available)
        
        if reasoning_result["sufficient"]:
            logging.info(f"[REASONING] 信息充分，终止循环。原因: {reasoning_result['reason']}")
            break
        else:
            logging.info(f"[REASONING] 信息不足，继续调用。原因: {reasoning_result['reason']}")
            if reasoning_result["next_instruction"]:
                current_instruction = reasoning_result["next_instruction"]
            else:
                logging.warning("无法生成下一条指令，终止循环")
                break
    
    # 生成最终回复
    try:
        final_context = build_context_for_llm_call(user_question, tool_call_history, "final_response", doc_query_available)
        final_context = optimize_context_length(final_context)
        
        # 输出发送给最终回复模型的上下文
        logging.info(f"[CONTEXT_TO_FINAL_RESPONSE] 使用模型: {FINAL_RESPONSE_MODEL}")
        logging.info(f"[CONTEXT_TO_FINAL_RESPONSE] 发送给最终回复模型的上下文:\n{format_context_for_debug(final_context)}")
        
        completion = client.chat.completions.create(
            model=FINAL_RESPONSE_MODEL,
            messages=final_context,
        )
        
        final_reply = completion.choices[0].message.content
        logging.info(f"[FINAL_REPLY] {final_reply}")
        
        return final_reply, tool_call_history, False
        
    except Exception as e:
        logging.error(f"生成最终回复失败: {str(e)}")
        return "抱歉，生成最终回复失败。", tool_call_history, True

def call_mcp_tool_and_format_result(tool_name, params, tool_use, now, mcp_client, user_question=None):
    """
    根据工具名和参数调用MCP，并格式化结果，返回 (tool_result, tool_failed)
    
    Args:
        tool_name: 工具名称
        params: 工具参数
        tool_use: 工具使用记录列表
        now: 时间戳函数
        mcp_client: MCP客户端
        user_question: 用户原始问题（用于文档查询等需要完整上下文的工具）
    """
    tool_result = None
    tool_failed = False
    try:
        match tool_name:
            case "获取天气信息":
                city = params.get("location") or params.get("city") or "海口"
                weather_data = mcp_client.get_weather(city)
                tool_use.append({
                    "type": "tool_result",
                    "icon": "⚙️",
                    "title": f"[{tool_name}]工具返回信息......",
                    "content": json.dumps(weather_data, ensure_ascii=False, indent=2),
                    "formatted": tool_result,
                    "collapsible": True,
                    "timestamp": now()
                })
                if weather_data:
                    tool_result = format_weather_data({"city": city, "data": weather_data, "type": "weather"})
                else:
                    tool_failed = True

            case "搜索兴趣点":
                keywords = params.get("keywords")
                city = params.get("city", "")
                search_data = mcp_client.search_pois(keywords, city)
                tool_use.append({
                    "type": "tool_result",
                    "icon": "⚙️",
                    "title": f"[{tool_name}]工具返回信息......",
                    "content": json.dumps(search_data, ensure_ascii=False, indent=2),
                    "formatted": tool_result,
                    "collapsible": True,
                    "timestamp": now()
                })
                if search_data:
                    tool_result = format_poi_data({"keywords": keywords, "city": city, "data": search_data, "type": "poi"})
                else:
                    tool_failed = True


            case "附近搜索":
                # 按照 mcp_client_wrapper.py 中 search_around 函数的参数顺序获取参数
                location = params.get("location")
                keywords = params.get("keywords")
                types = params.get("types", "")  # 确保types为空字符串而不是None
                radius = params.get("radius", 1000)
                
                # 检查必填参数
                if not all([location, keywords is not None, types is not None]):
                    logging.error(f"附近搜索缺少必填参数: location={location}, keywords={keywords}, types={types}")
                    tool_failed = True
                # 检查location格式
                elif "," in location:
                    try:
                        # 调用时严格按照函数定义的参数顺序
                        search_data = mcp_client.search_around(
                            location=location,
                            keywords=keywords,
                            types=types,
                            radius=radius
                        )
                        tool_use.append({
                            "type": "tool_result",
                            "icon": "⚙️",
                            "title": f"[{tool_name}]工具返回信息......",
                            "content": json.dumps(search_data, ensure_ascii=False, indent=2),
                            "formatted": tool_result,
                            "collapsible": True,
                            "timestamp": now()
                        })
                        if search_data:
                            # 可重用format_poi_data格式化
                            tool_result = format_poi_data({"keywords": keywords, "city": "", "data": search_data, "type": "poi"})
                        else:
                            logging.error("search_around 返回了空数据")
                            tool_failed = True
                    except Exception as e:
                        logging.error(f"调用 search_around 时发生错误: {str(e)}")
                        tool_failed = True
                else:
                    logging.error(f"location 格式错误: {location}")
                    tool_failed = True
                    
            case "目的地距离":
                origins = params.get("origin")
                destination = params.get("destination")
                type_ = params.get("type", "1")
                if origins and destination:
                    distance_data = mcp_client.get_distance(origins, destination, type_)
                    tool_use.append({
                        "type": "tool_result",
                        "icon": "⚙️",
                        "title": f"[{tool_name}]工具返回信息......",
                        "content": json.dumps(distance_data, ensure_ascii=False, indent=2),
                        "formatted": tool_result,
                        "collapsible": True,
                        "timestamp": now()
                    })
                    if distance_data and distance_data.get("results"):
                        result = distance_data["results"][0]
                        dist = result.get("distance", "未知")
                        duration = result.get("duration", None)
                        tool_result = f"## 距离测量结果\n\n- **起点**: {origins}\n- **终点**: {destination}\n- **距离**: {dist}米 (约{round(int(dist)/1000, 2) if dist != '未知' else '未知'}公里)\n"
                        if duration:
                            tool_result += f"- **预计耗时**: {int(int(duration)/60)}分钟\n"
                    else:
                        tool_failed = True
                else:
                    tool_failed = True
            case "文档查询":
                query = params.get("query")
                if not query:
                    tool_failed = True
                    tool_result = "缺少查询关键词参数"
                else:
                    # 优先使用优化后的query进行检索，用户原始问题用于生成回答
                    original_question = user_question.get('content', '') if user_question else None
                    success, answer, docs = perform_rag_query(query, original_question)
                    tool_use.append({
                        "type": "tool_result",
                        "icon": "📚",
                        "title": f"[{tool_name}]工具返回信息......",
                        "content": json.dumps({
                            "query": query,
                            "answer": answer,
                            "relevant_docs_count": len(docs),
                            "similarity_scores": [doc.get('similarity', 0) for doc in docs[:3]] if docs else []
                        }, ensure_ascii=False, indent=2),
                        "formatted": answer if success else None,
                        "collapsible": True,
                        "timestamp": now()
                    })
                    if success:
                        tool_result = answer
                    else:
                        tool_failed = True
                        tool_result = answer  # 错误信息
            case _:
                tool_failed = True
    except Exception as e:
        tool_failed = True
    return tool_result, tool_failed

@app.route('/api/weather/<city>', methods=['GET'])
def get_weather(city):
    try:
        # 使用MCP客户端获取天气信息
        weather_data = mcp_client.get_weather(city)
        
        if not weather_data:
            return jsonify({
                "status": "error",
                "message": f"无法获取{city}的天气信息"
            }), 404
            
        return jsonify({
            "status": "success",
            "data": weather_data
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/tools', methods=['GET'])
def list_tools():
    try:
        # 使用MCP客户端列出所有可用工具
        tools = mcp_client.list_tools()
        
        return jsonify({
            "status": "success",
            "data": tools
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/geo/<address>', methods=['GET'])
def get_geo_location(address):
    try:
        # 从查询参数获取可选的城市名（如果没有则默认为空字符串）
        city = request.args.get('city', '')
        
        # 使用MCP客户端获取地理编码信息，传入地址和城市
        geo_data = mcp_client.get_geo_location(address, city)
        
        # 如果没有获取到地理编码数据，返回404错误和提示信息
        if not geo_data:
            return jsonify({
                "status": "error",
                "message": f"无法获取地址'{address}'的地理编码"
            }), 404
            
        # 获取成功则返回地理编码数据，状态为success
        return jsonify({
            "status": "success",
            "data": geo_data
        })
        
    except Exception as e:
        # 捕获异常，返回500错误和异常信息
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/directions/driving', methods=['GET'])
def get_driving_directions():
    try:
        origin = request.args.get('origin')
        destination = request.args.get('destination')
        
        if not origin or not destination:
            return jsonify({
                "status": "error",
                "message": "缺少起点或终点参数"
            }), 400
            
        # 使用MCP客户端获取驾车路径规划
        directions_data = mcp_client.get_driving_directions(origin, destination)
        
        if not directions_data:
            return jsonify({
                "status": "error",
                "message": "无法获取驾车路径规划"
            }), 404
            
        return jsonify({
            "status": "success",
            "data": directions_data
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/directions/walking', methods=['GET'])
def get_walking_directions():
    try:
        origin = request.args.get('origin')
        destination = request.args.get('destination')
        
        if not origin or not destination:
            return jsonify({
                "status": "error",
                "message": "缺少起点或终点参数"
            }), 400
            
        # 使用MCP客户端获取步行路径规划
        directions_data = mcp_client.get_walking_directions(origin, destination)
        
        if not directions_data:
            return jsonify({
                "status": "error",
                "message": "无法获取步行路径规划"
            }), 404
            
        return jsonify({
            "status": "success",
            "data": directions_data
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/search', methods=['GET'])
def search_pois():
    try:
        keywords = request.args.get('keywords')
        city = request.args.get('city', '')
        page = int(request.args.get('page', 1))
        offset = int(request.args.get('offset', 20))
        
        if not keywords:
            return jsonify({
                "status": "error",
                "message": "缺少关键字参数"
            }), 400
            
        # 使用MCP客户端搜索POI
        search_data = mcp_client.search_pois(keywords, city, page, offset)
        
        if not search_data:
            return jsonify({
                "status": "error",
                "message": f"无法获取关键字'{keywords}'的搜索结果"
            }), 404
            
        return jsonify({
            "status": "success",
            "data": search_data
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/poi/detail', methods=['GET'])
def get_poi_detail():
    """
    获取POI详细信息
    参数：
        - id: POI ID（通过text_search/around_search获取）
    """
    try:
        poi_id = request.args.get('id')
        if not poi_id:
            return jsonify({
                "status": "error",
                "message": "缺少POI ID参数"
            }), 400
        detail = mcp_client.get_poi_detail(poi_id)
        if not detail:
            return jsonify({
                "status": "error",
                "message": f"无法获取POI ID为'{poi_id}'的详细信息"
            }), 404
        return jsonify({
            "status": "success",
            "data": detail
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/distance', methods=['GET'])
def get_distance():
    """
    测量两点间距离
    参数：
        - origins: 起点经纬度，格式为"经度,纬度"，支持多个起点用|分隔，如"110.1,20.1|110.2,20.2"
        - destination: 终点经纬度，格式为"经度,纬度"
        - type: 距离类型（可选，默认1）
            - 0：直线距离
            - 1：驾车导航距离
            - 3：步行导航距离
    """
    try:
        origins = request.args.get('origins')
        destination = request.args.get('destination')
        type_ = request.args.get('type', '1')
        if not origins or not destination:
            return jsonify({
                "status": "error",
                "message": "缺少origins或destination参数"
            }), 400
        result = mcp_client.get_distance(origins, destination, type_)
        if not result:
            return jsonify({
                "status": "error",
                "message": "无法测量距离"
            }), 404
        return jsonify({
            "status": "success",
            "data": result
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/directions/transit', methods=['GET'])
def get_transit_directions():
    """
    公交/地铁/火车等综合公共交通路径规划
    参数：
        - origin: 起点经纬度，格式为"经度,纬度"
        - destination: 终点经纬度，格式为"经度,纬度"
        - city: 起点城市名称或adcode
        - cityd: 终点城市名称或adcode（跨城时必填）
    """
    try:
        origin = request.args.get('origin')
        destination = request.args.get('destination')
        city = request.args.get('city')
        cityd = request.args.get('cityd', None)
        if not origin or not destination or not city:
            return jsonify({
                "status": "error",
                "message": "缺少origin、destination或city参数"
            }), 400
        result = mcp_client.get_transit_directions(origin, destination, city, cityd)
        if not result:
            return jsonify({
                "status": "error",
                "message": "无法获取公交路径规划"
            }), 404
        return jsonify({
            "status": "success",
            "data": result
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/directions/bicycling', methods=['GET'])
def get_bicycling_directions():
    """
    骑行路径规划
    参数：
        - origin: 起点经纬度，格式为"经度,纬度"
        - destination: 终点经纬度，格式为"经度,纬度"
    """
    try:
        origin = request.args.get('origin')
        destination = request.args.get('destination')
        if not origin or not destination:
            return jsonify({
                "status": "error",
                "message": "缺少origin或destination参数"
            }), 400
        result = mcp_client.get_bicycling_directions(origin, destination)
        if not result:
            return jsonify({
                "status": "error",
                "message": "无法获取骑行路径规划"
            }), 404
        return jsonify({
            "status": "success",
            "data": result
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/heartbeat', methods=['GET', 'POST'])
def heartbeat():
    logging.info(f"收到心跳请求: {datetime.datetime.now(beijing_tz).isoformat()}")
    return '', 204

@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查端点，检查应用和模型状态"""
    try:
        health_status = {
            "status": "healthy",
            "timestamp": datetime.datetime.now(beijing_tz).isoformat(),
            "checks": {}
        }
        
        # 检查基础服务
        health_status["checks"]["app"] = "ok"
        
        # 检查模型状态
        try:
            global _embedding_model
            if _embedding_model is not None:
                health_status["checks"]["embedding_model"] = "loaded"
            else:
                health_status["checks"]["embedding_model"] = "not_loaded"
        except Exception as e:
            health_status["checks"]["embedding_model"] = f"error: {str(e)}"
        
        # 检查向量缓存状态
        try:
            cache_data = load_embedding_cache()
            if cache_data is not None:
                doc_count = len(cache_data.get('texts', []))
                health_status["checks"]["vector_cache"] = f"loaded ({doc_count} docs)"
            else:
                health_status["checks"]["vector_cache"] = "not_available"
        except Exception as e:
            health_status["checks"]["vector_cache"] = f"error: {str(e)}"
        
        # 检查MCP客户端状态
        try:
            # 简单测试MCP客户端是否可用
            if mcp_client:
                health_status["checks"]["mcp_client"] = "ok"
            else:
                health_status["checks"]["mcp_client"] = "not_available"
        except Exception as e:
            health_status["checks"]["mcp_client"] = f"error: {str(e)}"
        
        # 判断整体健康状态
        failed_checks = [k for k, v in health_status["checks"].items() if "error" in str(v)]
        if failed_checks:
            health_status["status"] = "degraded"
            health_status["failed_checks"] = failed_checks
        
        return jsonify(health_status)
        
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.datetime.now(beijing_tz).isoformat()
        }), 500

@app.route('/api/check_rag_status', methods=['GET'])
def check_rag_status():
    """检查RAG系统状态的API端点"""
    try:
        cache_data = load_embedding_cache()
        
        if cache_data is None:
            return jsonify({
                "status": "unavailable",
                "message": "知识库索引未生成",
                "has_cache": False,
                "doc_count": 0
            })
        
        # 检查数据完整性
        if not all(key in cache_data for key in ['texts', 'embeddings', 'meta']):
            return jsonify({
                "status": "error",
                "message": "知识库索引数据不完整",
                "has_cache": True,
                "doc_count": 0
            })
        
        doc_count = len(cache_data.get('texts', []))
        if doc_count == 0:
            return jsonify({
                "status": "empty",
                "message": "知识库为空",
                "has_cache": True,
                "doc_count": 0
            })
        
        # 获取缓存文件信息
        cache_file = os.path.join(app.config['EMBEDDINGS_FOLDER'], 'embedding_cache.pkl')
        cache_time = None
        cache_size = None
        
        if os.path.exists(cache_file):
            mtime = os.path.getmtime(cache_file)
            cache_time = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
            cache_size = os.path.getsize(cache_file) / (1024 * 1024)  # MB
        
        return jsonify({
            "status": "ready",
            "message": "知识库就绪",
            "has_cache": True,
            "doc_count": doc_count,
            "cache_time": cache_time,
            "cache_size_mb": round(cache_size, 2) if cache_size else None,
            "sources": list(set([meta.get('source', 'unknown') for meta in cache_data.get('meta', [])]))
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"检查状态失败: {str(e)}",
            "has_cache": False,
            "doc_count": 0
        }), 500

@app.route('/api/rag_query', methods=['POST'])
def test_rag_query():
    """测试RAG查询功能的API端点"""
    try:
        data = request.get_json()
        query = data.get('query')
        original_question = data.get('original_question')
        
        if not query:
            return jsonify({
                "status": "error",
                "message": "缺少查询参数"
            }), 400
        
        success, answer, docs = perform_rag_query(query, original_question)
        
        return jsonify({
            "status": "success" if success else "error",
            "query": query,
            "original_question": original_question,
            "answer": answer,
            "relevant_docs_count": len(docs),
            "docs": [{"text": doc["text"][:200] + "..." if len(doc["text"]) > 200 else doc["text"], 
                     "source": doc["meta"]["source"], 
                     "similarity": doc["similarity"]} for doc in docs[:3]] if docs else []
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

def format_context_for_debug(messages, max_content_length=2000, full_output_for_reasoning=False):
    """
    格式化消息上下文，用于调试日志输出
    :param messages: 要格式化的消息列表
    :param max_content_length: 最大内容长度（用于截断）
    :param full_output_for_reasoning: 是否为推理模型完整输出（不截断MCP工具返回信息）
    """
    formatted_messages = []
    total_chars = 0
    total_estimated_tokens = 0
    
    for i, msg in enumerate(messages):
        content = msg.get('content', '')
        content_length = len(content)
        estimated_tokens = content_length // 4  # 粗略估算token数
        total_chars += content_length
        total_estimated_tokens += estimated_tokens
        
        # 对于推理模型，如果消息内容是MCP工具返回信息，则完整输出
        if full_output_for_reasoning and msg.get('role') == 'system' and content.startswith('MCP工具返回信息：'):
            display_content = content  # 完整输出，不截断
        elif content_length > max_content_length:
            display_content = content[:max_content_length] + f"...(剩余{content_length - max_content_length}字符)"
        else:
            display_content = content
        
        formatted_messages.append({
            'index': i + 1,
            'role': msg.get('role'),
            'content_length': content_length,
            'estimated_tokens': estimated_tokens,
            'content': display_content
        })
    
    summary = {
        'total_messages': len(messages),
        'total_characters': total_chars,
        'estimated_total_tokens': total_estimated_tokens,
        'messages': formatted_messages
    }
    
    return json.dumps(summary, ensure_ascii=False, indent=2)

# 在gunicorn环境中启用智能加载策略
if __name__ != '__main__':
    # 这表示我们在gunicorn worker中运行
    logging.info("在gunicorn worker中运行，启用智能加载策略...")
    smart_startup_initialization()  # 根据缓存状态智能决定是否预加载模型

if __name__ == '__main__':
    print("Flask is starting...")
    
    # 初始化当前城市配置
    update_current_city()
    
    # 在开发模式下也启用智能加载策略
    smart_startup_initialization()  # 根据缓存状态智能决定是否预加载模型
    
    # 方案1: 禁用重载器避免重复加载模型（推荐）
    app.run(
        debug=True, 
        use_reloader=False,  # 禁用重载器，避免重复初始化和模型加载
        host='127.0.0.1',
        port=8000
    )
    
    print("Flask has started.")