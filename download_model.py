#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模型下载工具 - 预先下载嵌入模型以避免首次运行时的等待

该脚本用于预下载 Qwen/Qwen3-Embedding-0.6B 嵌入模型，
避免在应用首次运行时进行约2GB的模型下载。

使用方法:
    python download_model.py

特性:
- 显示下载进度
- 自动创建缓存目录
- 模型验证测试
- 错误处理和日志记录
- Hugging Face token支持
"""

import os
import sys
import time
import logging
from pathlib import Path
from dotenv import load_dotenv

# 设置环境变量避免tokenizers并行化警告
os.environ["TOKENIZERS_PARALLELISM"] = "false"

def load_hf_token_from_env():
    """从 .env 文件加载 Hugging Face token"""
    # 加载 .env 文件
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        # 检查是否成功加载了 token
        if os.getenv('HUGGINGFACE_TOKEN'):
            logging.info("✅ 已从 .env 文件加载 Hugging Face token")
        else:
            logging.warning("⚠️ .env 文件中未找到 HUGGINGFACE_TOKEN")
    else:
        logging.warning("⚠️ 未找到 .env 文件")

def setup_logging():
    """设置日志配置"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )

def check_dependencies():
    """检查必要的依赖是否已安装"""
    try:
        import sentence_transformers
        import torch
        import numpy as np
        import huggingface_hub
        logging.info("✅ 所有必要依赖已安装")
        return True
    except ImportError as e:
        logging.error(f"❌ 缺少必要依赖: {e}")
        logging.error("请先安装依赖: pip install sentence-transformers torch numpy huggingface_hub")
        return False

def check_and_setup_hf_token():
    """检查和设置Hugging Face token"""
    # 检查环境变量中的token
    hf_token = os.getenv('HUGGINGFACE_TOKEN') or os.getenv('HF_TOKEN')
    
    if hf_token:
        logging.info("✅ 检测到Hugging Face token")
        # 设置到huggingface_hub使用的环境变量
        os.environ['HUGGINGFACE_HUB_TOKEN'] = hf_token
        return True
    
    # 检查是否已经登录
    try:
        from huggingface_hub import HfApi
        api = HfApi()
        user_info = api.whoami()
        if user_info:
            logging.info(f"✅ 已登录Hugging Face，用户: {user_info.get('name', 'Unknown')}")
            return True
    except Exception:
        pass
    
    # 提示用户设置token
    logging.warning("⚠️ 未检测到Hugging Face token")
    logging.info("💡 获取token的方法:")
    logging.info("   1. 访问: https://huggingface.co/settings/tokens")
    logging.info("   2. 创建一个新的token (Read权限即可)")
    logging.info("   3. 设置环境变量: set HUGGINGFACE_TOKEN=your_token_here")
    logging.info("   4. 或者运行: huggingface-cli login")
    
    # 询问用户是否要输入token
    print("\n选择操作:")
    print("1. 输入Hugging Face token")
    print("2. 尝试无token下载 (可能失败)")
    print("3. 退出")
    
    choice = input("请选择 (1/2/3): ").strip()
    
    if choice == '1':
        token = input("请输入您的Hugging Face token: ").strip()
        if token:
            os.environ['HUGGINGFACE_HUB_TOKEN'] = token
            logging.info("✅ Token已设置")
            return True
        else:
            logging.error("❌ Token不能为空")
            return False
    elif choice == '2':
        logging.warning("⚠️ 将尝试无token下载，可能会失败")
        return True
    else:
        logging.info("👋 用户选择退出")
        return False

def get_model_cache_dir():
    """获取模型缓存目录路径"""
    # 获取当前脚本所在目录（项目根目录）
    script_dir = Path(__file__).parent
    cache_dir = script_dir / "App" / "model_cache"
    return cache_dir

def download_embedding_model():
    """下载嵌入模型"""
    MODEL_NAME = 'Qwen/Qwen3-Embedding-0.6B'
    
    try:
        # 导入必要的库
        from sentence_transformers import SentenceTransformer
        
        # 设置模型缓存目录
        cache_dir = get_model_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        
        logging.info(f"🎯 开始下载模型: {MODEL_NAME}")
        logging.info(f"📁 缓存目录: {cache_dir}")
        logging.info("⏳ 正在下载模型，这可能需要几分钟时间（约2GB）...")
        
        # 检查Hugging Face token是否正确设置
        hf_token = os.getenv('HUGGINGFACE_TOKEN') or os.getenv('HUGGINGFACE_HUB_TOKEN')
        if hf_token:
            logging.info("🔑 使用Hugging Face token进行认证")
        else:
            logging.warning("⚠️ 未设置Hugging Face token，可能会遇到认证问题")
        
        start_time = time.time()
        
        # 初始化模型（这会触发下载）
        model = SentenceTransformer(
            MODEL_NAME, 
            cache_folder=str(cache_dir),
            device='cpu',  # 使用CPU避免CUDA问题
            trust_remote_code=True  # 信任远程代码
        )
        
        download_time = time.time() - start_time
        logging.info(f"✅ 模型下载完成！耗时: {download_time:.2f}秒")
        
        # 进行测试确保模型工作正常
        logging.info("🧪 正在测试模型...")
        test_texts = ["这是一个测试文本", "模型下载成功"]
        test_embeddings = model.encode(test_texts, show_progress_bar=False)
        
        logging.info(f"✅ 模型测试成功！")
        logging.info(f"📊 嵌入维度: {test_embeddings.shape}")
        logging.info(f"📁 模型已保存到: {cache_dir}")
        
        # 显示模型文件信息
        show_model_info(cache_dir)
        
        return True
        
    except Exception as e:
        error_msg = str(e)
        logging.error(f"❌ 模型下载失败: {error_msg}")
        
        # 提供针对性的错误建议
        if "401" in error_msg or "authentication" in error_msg.lower():
            logging.error("🔑 认证失败，请检查Hugging Face token")
            logging.error("💡 解决方案:")
            logging.error("   1. 确认token有效")
            logging.error("   2. 检查token是否有读取权限")
            logging.error("   3. 尝试重新运行脚本")
        elif "403" in error_msg or "forbidden" in error_msg.lower():
            logging.error("🚫 访问被拒绝，可能需要申请模型访问权限")
            logging.error("💡 请访问模型页面申请访问权限: https://huggingface.co/Qwen/Qwen3-Embedding-0.6B")
        elif "timeout" in error_msg.lower() or "connection" in error_msg.lower():
            logging.error("🌐 网络连接问题")
            logging.error("💡 解决方案:")
            logging.error("   1. 检查网络连接")
            logging.error("   2. 稍后重试")
            logging.error("   3. 考虑使用VPN")
        elif "expecting value" in error_msg.lower() or "json" in error_msg.lower():
            logging.error("📡 JSON解析错误，可能是网络或服务器问题")
            logging.error("💡 解决方案:")
            logging.error("   1. 检查网络连接是否稳定")
            logging.error("   2. 确认Hugging Face服务是否正常")
            logging.error("   3. 稍后重试")
            logging.error("   4. 考虑使用镜像源或VPN")
        else:
            logging.error("💡 通用解决方案:")
            logging.error("   1. 检查网络连接")
            logging.error("   2. 确认Hugging Face token")
            logging.error("   3. 重新运行脚本")
        
        return False

def show_model_info(cache_dir):
    """显示下载的模型信息"""
    try:
        model_dir = cache_dir / "models--Qwen--Qwen3-Embedding-0.6B"
        if model_dir.exists():
            # 计算模型文件大小
            total_size = 0
            file_count = 0
            for root, dirs, files in os.walk(model_dir):
                for file in files:
                    file_path = Path(root) / file
                    if file_path.exists():
                        total_size += file_path.stat().st_size
                        file_count += 1
            
            # 转换为可读格式
            size_mb = total_size / (1024 * 1024)
            size_gb = size_mb / 1024
            
            logging.info("📋 模型信息:")
            logging.info(f"   📁 文件数量: {file_count}")
            if size_gb >= 1:
                logging.info(f"   💾 总大小: {size_gb:.2f} GB")
            else:
                logging.info(f"   💾 总大小: {size_mb:.2f} MB")
            logging.info(f"   📍 位置: {model_dir}")
            
    except Exception as e:
        logging.warning(f"⚠️ 获取模型信息时出错: {e}")

def check_existing_model():
    """检查模型是否已经存在"""
    cache_dir = get_model_cache_dir()
    model_dir = cache_dir / "models--Qwen--Qwen3-Embedding-0.6B"
    
    if model_dir.exists() and any(model_dir.iterdir()):
        logging.info("✅ 检测到已存在的模型文件")
        show_model_info(cache_dir)
        return True
    return False

def main():
    """主函数"""
    print("🚀 旅游助手 - 模型下载工具")
    print("=" * 50)
    
    setup_logging()
    
    # 加载 Hugging Face token
    load_hf_token_from_env()
    
    # 检查依赖
    if not check_dependencies():
        sys.exit(1)
    
    # 检查Hugging Face token
    if not check_and_setup_hf_token():
        sys.exit(1)
    
    # 检查是否已有模型
    if check_existing_model():
        choice = input("\n🤔 模型已存在，是否重新下载？(y/N): ").strip().lower()
        if choice not in ['y', 'yes']:
            logging.info("⏭️ 跳过下载，使用现有模型")
            return
    
    # 下载模型
    logging.info("🎬 开始下载流程...")
    success = download_embedding_model()
    
    if success:
        print("\n" + "=" * 50)
        print("🎉 模型下载完成！")
        print("💡 提示：现在可以启动应用，将不会再次下载模型")
        print("🚀 运行应用: python App/app.py")
    else:
        print("\n" + "=" * 50)
        print("❌ 模型下载失败，请检查网络连接和错误信息")
        sys.exit(1)

if __name__ == "__main__":
    main()