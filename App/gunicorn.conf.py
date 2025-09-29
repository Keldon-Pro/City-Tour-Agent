import multiprocessing
import os

# Server socket
bind = "0.0.0.0:8000"
backlog = 2048

# Worker processes
workers = 1  # 减少worker数量以便于调试，避免多个worker同时加载模型
worker_class = 'gevent'
worker_connections = 1000
timeout = 600  # 增加超时时间到600秒，足够模型加载
keepalive = 1
max_requests = 1000  # 增加最大请求数
max_requests_jitter = 100  # 添加抖动避免同时重启

# Logging
accesslog = '-'  # 输出到stdout
errorlog = '-'   # 输出到stderr
loglevel = 'debug'  # 设置日志级别为debug
access_log_format = '%({x-real-ip}i)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Process naming
proc_name = 'haikou-tour'

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# SSL
keyfile = None
certfile = None

# Debugging
reload = True  # 启用自动重载
reload_engine = 'auto'
spew = False
check_config = False

# Server hooks
def on_starting(server):
    """
    在服务器启动时记录配置信息
    """
    print("Server configuration:")
    print(f"Workers: {workers}")
    print(f"Worker class: {worker_class}")
    print(f"Timeout: {timeout}")
    print(f"Max requests: {max_requests}")
    print(f"Log level: {loglevel}")
    print(f"Debug mode: {spew}")
    print(f"Auto reload: {reload}")

def on_reload(server):
    """
    在服务器重新加载时执行
    """
    print("Server reloading...")

def worker_int(worker):
    """
    Worker收到SIGINT信号时执行
    """
    print(f"Worker {worker.pid} received SIGINT")

def pre_fork(server, worker):
    """
    Worker进程fork之前执行
    """
    print(f"Worker {worker.age} about to fork")

def post_fork(server, worker):
    """
    Worker进程fork之后执行
    """
    print(f"Worker {worker.pid} spawned")
