// 文档上传相关功能
document.addEventListener('DOMContentLoaded', function() {
    const uploadForm = document.getElementById('uploadForm');
    const fileInput = document.getElementById('fileInput');
    const uploadStatus = document.getElementById('uploadStatus');

    if (uploadForm) {
        uploadForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            const formData = new FormData();
            const file = fileInput.files[0];

            if (!file) {
                showAlert('请选择文件', 'error');
                return;
            }

            formData.append('file', file);

            try {
                const response = await fetch('/upload', {
                    method: 'POST',
                    body: formData
                });

                const result = await response.json();
                
                if (response.ok) {
                    showAlert('文件上传成功', 'success');
                    setTimeout(() => {
                        window.location.reload();
                    }, 1500);
                } else {
                    showAlert(result.message || '上传失败', 'error');
                }
            } catch (error) {
                showAlert('上传出错：' + error.message, 'error');
            }
        });
    }

    // 删除文件
    document.querySelectorAll('.delete-file').forEach(button => {
        button.addEventListener('click', async function(e) {
            e.preventDefault();
            const fileId = this.dataset.fileId;
            
            if (confirm('确定要删除这个文件吗？')) {
                try {
                    const response = await fetch(`/delete/${fileId}`, {
                        method: 'GET'
                    });

                    if (response.ok) {
                        showAlert('文件删除成功', 'success');
                        setTimeout(() => {
                            window.location.reload();
                        }, 1500);
                    } else {
                        const result = await response.json();
                        showAlert(result.message || '删除失败', 'error');
                    }
                } catch (error) {
                    showAlert('删除出错：' + error.message, 'error');
                }
            }
        });
    });
});

// 显示提示信息
function showAlert(message, type = 'info') {
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type}`;
    alertDiv.textContent = message;

    const container = document.querySelector('.doc-container') || document.body;
    container.insertBefore(alertDiv, container.firstChild);

    setTimeout(() => {
        alertDiv.remove();
    }, 3000);
}

// 登录表单处理
const loginForm = document.getElementById('loginForm');
if (loginForm) {
    loginForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        const username = document.getElementById('username').value;
        const password = document.getElementById('password').value;

        try {
            const response = await fetch('/login', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: `username=${encodeURIComponent(username)}&password=${encodeURIComponent(password)}`
            });

            if (response.redirected) {
                window.location.href = response.url;
            } else {
                const result = await response.text();
                showAlert('登录失败：用户名或密码错误', 'error');
            }
        } catch (error) {
            showAlert('登录出错：' + error.message, 'error');
        }
    });
}

// 文件大小格式化
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}
