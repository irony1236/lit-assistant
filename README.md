# 专业文献助手 (Lit Assistant)

基于 RAG 的学术文献智能问答系统，支持 PDF/TXT 文档检索与问答。

## 功能特性

- 支持 PDF / TXT / 扫描件(OCR) 多格式文档
- BM25 + 向量 混合检索
- 中文优化嵌入模型 (BAAI/bge-small-zh-v1.5)
- 会话级临时上传
- 来源引用信息返回

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填入你的 API Key：

```bash
cp .env.example .env
```

编辑 `.env`：
```
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
```

### 3. 安装 Tesseract OCR (可选)

如需处理扫描件 PDF，请安装 [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki)。

### 4. 启动服务

```bash
python app.py
```

访问 http://localhost:5000

### 5. 使用方法

1. 将 PDF/TXT 文件放入 `papers/` 文件夹
2. 点击「初始化知识库」
3. 开始提问
