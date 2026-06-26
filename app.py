"""
Flask 主程序 - 专业文献助手 API
提供: 聊天 / 初始化 / 上传 / 状态 接口
"""

import os
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, session

from rag_engine import RAGEngine

app = Flask(__name__)
app.secret_key = "rag-lit-assistant-secret-key-change-in-production"

# ============ RAG 引擎 ============
rag = RAGEngine()

# ============ 路径 ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PAPERS_DIR = os.path.join(BASE_DIR, "papers")
TEMP_DIR = os.path.join(BASE_DIR, "temp_uploads")

os.makedirs(PAPERS_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".txt"}


def _get_session_id() -> str:
    """获取/创建会话 ID"""
    if "session_id" not in session:
        session["session_id"] = uuid.uuid4().hex[:12]
    return session["session_id"]


def allowed_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


# ==================== 页面路由 ====================


@app.route("/")
def index():
    return render_template("index.html")


# ==================== API 路由 ====================


@app.route("/chat", methods=["POST"])
def chat():
    """
    聊天 API
    Request: {"question": "..."}
    Response: {"reply": "...", "sources": [{"file": "...", "snippet": "..."}]}
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "无效的请求数据"}), 400

    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "请输入问题内容"}), 400

    if len(question) > 2000:
        return jsonify({"error": "问题过长（最多 2000 字符）"}), 400

    sid = _get_session_id()
    result = rag.ask(question, session_id=sid)
    return jsonify(result)


@app.route("/init", methods=["POST"])
def init_knowledge():
    """初始化/重建知识库"""
    try:
        result = rag.build_vectorstore(PAPERS_DIR)
        if result["success"]:
            return jsonify({
                "success": True,
                "message": result["message"],
                "details": result.get("details", []),
            })
        else:
            return jsonify({
                "success": False,
                "message": result["message"],
                "details": result.get("details", []),
            }), 400
    except Exception as e:
        return jsonify({"success": False, "message": f"初始化失败: {str(e)}", "details": []}), 500


@app.route("/init_progress", methods=["GET"])
def get_init_progress():
    """获取初始化进度（供前端轮询）"""
    return jsonify(rag.get_init_progress())


@app.route("/status", methods=["GET"])
def get_status():
    """系统状态"""
    pdf_count = 0
    txt_count = 0
    if os.path.exists(PAPERS_DIR):
        pdf_count = len([f for f in os.listdir(PAPERS_DIR) if f.lower().endswith(".pdf")])
        txt_count = len([f for f in os.listdir(PAPERS_DIR) if f.lower().endswith(".txt")])

    return jsonify({
        "initialized": rag.vectorstore is not None,
        "pdf_count": pdf_count,
        "txt_count": txt_count,
        "chroma_exists": rag.vectorstore is not None,
    })


@app.route("/upload", methods=["POST"])
def upload_file():
    """
    上传文献（PDF/TXT）
    - 保存到 temp_uploads/{session_id}/
    - 为该会话构建临时向量索引
    """
    if "file" not in request.files:
        return jsonify({"success": False, "message": "未选择文件"}), 400

    file = request.files["file"]
    if not file or file.filename == "":
        return jsonify({"success": False, "message": "文件名为空"}), 400

    if not allowed_file(file.filename):
        return jsonify({
            "success": False,
            "message": f"不支持的文件格式，仅支持: {', '.join(ALLOWED_EXTENSIONS)}"
        }), 400

    sid = _get_session_id()
    session_dir = os.path.join(TEMP_DIR, sid)
    os.makedirs(session_dir, exist_ok=True)

    save_path = os.path.join(session_dir, file.filename)
    file.save(save_path)

    try:
        result = rag.add_temp_upload(sid, save_path)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": f"上传处理失败: {str(e)}"}), 500


@app.route("/clear_session", methods=["POST"])
def clear_session():
    """清理当前会话的临时上传"""
    sid = _get_session_id()
    rag.remove_session_temp(sid)

    session_dir = os.path.join(TEMP_DIR, sid)
    if os.path.exists(session_dir):
        import shutil
        shutil.rmtree(session_dir)

    return jsonify({"success": True, "message": "会话临时数据已清理"})


# ==================== 错误处理 ====================


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "资源不存在"}), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "服务器内部错误"}), 500


# ==================== 启动 ====================

if __name__ == "__main__":
    print("=" * 50)
    print("  [*] 专业文献助手 RAG 系统 v2")
    print("=" * 50)
    print(f"  [论文文件夹] {PAPERS_DIR}")
    print(f"  [向量数据库] {os.path.join(BASE_DIR, 'chroma_db')}")
    print(f"  [临时上传]   {TEMP_DIR}")
    print(f"  [访问地址]   http://localhost:5000")
    print(f"  [说明]       支持 PDF/TXT | 混合检索 | 临时上传")
    print("=" * 50)

    app.run(debug=True, host="0.0.0.0", port=5000)
