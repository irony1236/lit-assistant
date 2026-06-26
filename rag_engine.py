"""
专业文献助手 - RAG 引擎 v2
============================
功能:
  - 支持 PDF / TXT / 扫描件(OCR) 多格式文档
  - BM25 + 向量 混合检索 (自定义加权融合)
  - 分段优化: chunk_size=800, overlap=200
  - 会话级临时上传索引
  - 来源引用信息返回
"""

import os
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import chromadb
import fitz  # PyMuPDF
from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader
from langchain_community.retrievers import BM25Retriever as LangChainBM25Retriever
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter
from PIL import Image

# ==================== Tesseract OCR 支持 ====================
import pytesseract

# 配置 Tesseract 路径（Windows 默认安装位置）
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
os.environ["TESSDATA_PREFIX"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tessdata")


def _text_quality(text: str) -> float:
    """评估文本质量：返回有效字符（中文 + ASCII）的百分比"""
    if not text:
        return 0.0
    total = len(text)
    valid = sum(1 for c in text if "一" <= c <= "鿿" or 32 <= ord(c) <= 126)
    return valid / total if total > 0 else 0.0

# ==================== 常量 ====================
COLLECTION_NAME = "papers_docs"
TEMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_uploads")
MAX_TEMP_AGE_HOURS = 24


# ==================== 文档加载器 ====================
class DocumentLoader:
    """多格式文档加载器，支持 PDF / TXT / 扫描件 OCR"""

    @staticmethod
    def load_pdf_with_pypdf(path: str) -> List[Document]:
        """使用 PyPDFLoader 加载 PDF"""
        try:
            loader = PyPDFLoader(path)
            docs = loader.load()
            total_chars = sum(len(d.page_content) for d in docs)
            if total_chars < 20:
                return []
            return docs
        except Exception:
            return []

    @staticmethod
    def load_scanned_pdf_with_ocr(path: str) -> List[Document]:
        """使用 PyMuPDF + Tesseract OCR 对扫描件或乱码 PDF 做 OCR"""
        try:
            doc = fitz.open(path)
            pages = []
            total = len(doc)
            for page_num in range(total):
                page = doc.load_page(page_num)
                mat = fitz.Matrix(2.0, 2.0)  # 2x 分辨率提高识别精度
                pix = page.get_pixmap(matrix=mat)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

                text = pytesseract.image_to_string(img, lang="chi_sim+eng", config="--psm 6")

                pages.append(
                    Document(
                        page_content=text.strip(),
                        metadata={"source": path, "page": page_num + 1, "ocr": True},
                    )
                )
                if (page_num + 1) % 10 == 0:
                    print(f"    OCR 进度: {page_num + 1}/{total} 页")

            doc.close()
            return pages
        except pytesseract.TesseractNotFoundError:
            raise RuntimeError(
                "Tesseract 未找到，请确认已安装:\n"
                "  winget install --id UB-Mannheim.TesseractOCR\n"
                "  或从 https://github.com/UB-Mannheim/tesseract/wiki 下载"
            )
        except Exception as e:
            raise RuntimeError(f"OCR 处理失败: {e}")

    @staticmethod
    def load_txt(path: str) -> List[Document]:
        """加载 TXT 文件，自动检测编码"""
        encodings = ["utf-8", "gbk", "gb2312", "gb18030", "big5", "utf-16"]
        for enc in encodings:
            try:
                with open(path, "r", encoding=enc) as f:
                    text = f.read()
                return [
                    Document(
                        page_content=text,
                        metadata={"source": path, "encoding": enc},
                    )
                ]
            except (UnicodeDecodeError, UnicodeError):
                continue
        # fallback: chardet
        try:
            import chardet
            with open(path, "rb") as f:
                raw = f.read(10000)
                enc = chardet.detect(raw)["encoding"] or "utf-8"
            with open(path, "r", encoding=enc, errors="replace") as f:
                text = f.read()
            return [
                Document(page_content=text, metadata={"source": path, "encoding": enc})
            ]
        except Exception as e:
            print(f"  [SKIP] {path}: 无法解码 ({e})")
            return []

    @classmethod
    def load_document(cls, path: str, force_ocr: bool = False) -> Tuple[List[Document], str]:
        """加载文档（自动检测格式和质量，必要时回退 OCR）"""
        ext = Path(path).suffix.lower()
        fname = Path(path).name

        if ext == ".txt":
            docs = cls.load_txt(path)
            if docs:
                return docs, f"[TXT] {fname}"
            return [], f"[SKIP] {fname}: 编码无法识别"

        if ext == ".pdf":
            if force_ocr:
                try:
                    ocr_docs = cls.load_scanned_pdf_with_ocr(path)
                    if ocr_docs:
                        total = sum(len(d.page_content) for d in ocr_docs)
                        return ocr_docs, f"[OCR] {fname} ({len(ocr_docs)}p, {total}chars)"
                except RuntimeError as e:
                    return [], f"[SKIP] {fname}: {e}"

            # 先用 pypdf 提取
            docs = cls.load_pdf_with_pypdf(path)
            if docs:
                total_chars = sum(len(d.page_content) for d in docs)
                avg_quality = sum(_text_quality(d.page_content) for d in docs) / len(docs)

                if total_chars > 50 and avg_quality >= 0.60:
                    return docs, f"[PDF] {fname} ({len(docs)}p, {total_chars}chars, {avg_quality:.0%} quality)"

                print(f"  [OCR] {fname}: 质量仅 {avg_quality:.0%} ({total_chars}chars)，尝试 OCR...")
            else:
                print(f"  [OCR] {fname}: pypdf 失败，尝试 OCR...")

            # OCR 回退
            try:
                ocr_docs = cls.load_scanned_pdf_with_ocr(path)
                if ocr_docs:
                    total = sum(len(d.page_content) for d in ocr_docs)
                    return ocr_docs, f"[OCR] {fname} ({len(ocr_docs)}p, {total}chars)"
            except RuntimeError as e:
                return [], f"[SKIP] {fname}: {e}"

        return [], f"[SKIP] {fname}: 不支持格式 ({ext})"


# ==================== 混合检索器 ====================
class HybridRetriever:
    """
    混合检索器：融合 BM25 关键词检索 + 向量语义检索
    权重各 0.5，取合并排序后 Top-k
    """

    def __init__(self, bm25_retriever: LangChainBM25Retriever, vector_retriever):
        self.bm25 = bm25_retriever
        self.vector = vector_retriever

    def invoke(self, query: str, k: int = 5) -> List[Document]:
        """执行混合检索"""
        # BM25 检索（多取一些以便融合）
        bm25_k = max(k * 3, 6)
        bm25_results = self.bm25.invoke(query)[:bm25_k]

        # 向量检索
        vec_results = self.vector.invoke(query)[:bm25_k]

        # 融合排序(Reciprocal Rank Fusion)
        all_docs = {}
        for rank, doc in enumerate(bm25_results + vec_results):
            # 用 content 前 100 字作为 key 去重
            key = doc.page_content[:100]
            if key not in all_docs:
                all_docs[key] = doc
            # RRF score: 1 / (k + rank) for each source
            # BM25 rank and vector rank give combined score

        # 二次排序：优先保留在两个列表中都出现的文档
        vec_keys = {d.page_content[:100] for d in vec_results}
        bm25_keys = {d.page_content[:100] for d in bm25_results}

        scored = []
        for key, doc in all_docs.items():
            score = 0
            if key in vec_keys:
                score += 0.5
            if key in bm25_keys:
                score += 0.5
            # 位置加分
            if key in vec_keys:
                idx = next(i for i, d in enumerate(vec_results) if d.page_content[:100] == key)
                score += 0.5 / (idx + 1)
            if key in bm25_keys:
                idx = next(i for i, d in enumerate(bm25_results) if d.page_content[:100] == key)
                score += 0.5 / (idx + 1)
            scored.append((score, doc))

        scored.sort(key=lambda x: -x[0])
        return [doc for _, doc in scored[:k]]


# ==================== 主 RAG 引擎 ====================
class RAGEngine:
    """专业文献助手 RAG 引擎"""

    # 全局初始化进度（供前端轮询）
    init_progress: Dict[str, object] = {
        "running": False,
        "phase": "",
        "current": 0,
        "total": 0,
        "message": "",
    }

    @classmethod
    def get_init_progress(cls) -> Dict[str, object]:
        """返回当前初始化进度（线程安全读取）"""
        return dict(cls.init_progress)

    def __init__(self, persist_directory: str = "./chroma_db"):
        self.persist_directory = persist_directory

        # Chroma 客户端
        self.chroma_client = chromadb.PersistentClient(path=persist_directory)

        # 嵌入模型（中文优化）
        print("正在加载嵌入模型（中文优化）...")
        self.embeddings = HuggingFaceEmbeddings(
            model_name="BAAI/bge-small-zh-v1.5",
            model_kwargs={"device": "cpu"},
        )

        # DeepSeek LLM
        self.llm = ChatOpenAI(
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            model="deepseek-v4-flash",
            temperature=0.1,
            max_tokens=2048,
            timeout=60,
        )

        # 主索引
        self.vectorstore: Optional[Chroma] = None
        self._bm25_retriever: Optional[LangChainBM25Retriever] = None
        self._hybrid_retriever: Optional[HybridRetriever] = None

        self._load_existing_vectorstore()

        # 临时会话索引
        self.temp_indexes: Dict[str, dict] = {}
        self._cleanup_temp_files()

    # ============== 向量库管理 ==============

    def _load_existing_vectorstore(self) -> None:
        """加载已有的向量库并重建检索器"""
        try:
            existing = self.chroma_client.list_collections()
            if any(c.name == COLLECTION_NAME for c in existing):
                self.vectorstore = Chroma(
                    client=self.chroma_client,
                    collection_name=COLLECTION_NAME,
                    embedding_function=self.embeddings,
                )
                count = self.vectorstore._collection.count()
                print(f"已加载向量库，共 {count} 条记录")
                self._rebuild_retrievers()
        except Exception as e:
            print(f"加载向量库失败: {e}")

    def _rebuild_retrievers(self) -> None:
        """重建 BM25 和向量检索器"""
        if not self.vectorstore:
            return
        try:
            all_data = self.vectorstore._collection.get(include=["documents", "metadatas"])
            if all_data and all_data.get("documents"):
                documents = [
                    Document(page_content=t, metadata=m if m else {})
                    for t, m in zip(all_data["documents"], all_data["metadatas"])
                ]
                bm25 = LangChainBM25Retriever.from_documents(documents)
                bm25.k = 6
                vec_retriever = self.vectorstore.as_retriever(
                    search_type="similarity", search_kwargs={"k": 6}
                )
                self._bm25_retriever = bm25
                self._hybrid_retriever = HybridRetriever(bm25, vec_retriever)
        except Exception as e:
            print(f"重建检索器失败: {e}")

    @staticmethod
    def _get_title_from_filename(path: str) -> str:
        """从文件名提取标题"""
        fname = Path(path).stem
        title = re.sub(r'^[〔\[（【][^〕\]）】]*[〕\]）】]\s*', '', fname)
        title = re.sub(r'^\d+[\s.、-]+', '', title)
        return title.strip()

    # ============== 文档处理 ==============

    def process_document(self, path: str) -> Tuple[str, str]:
        """处理单个文档（加载 + 添加元数据），返回 (状态描述, 路径)"""
        docs, status = DocumentLoader.load_document(path)
        if not docs:
            return status, path
        title = self._get_title_from_filename(path)
        for d in docs:
            d.metadata["file_title"] = title
            d.metadata["source_path"] = path
        return status, path

    def build_vectorstore(self, docs_dir: str = "./papers") -> dict:
        """
        构建/重建向量知识库
        返回: {"success": bool, "message": str, "details": [str]}
        """
        RAGEngine.init_progress["running"] = True
        RAGEngine.init_progress["phase"] = "scan"
        RAGEngine.init_progress["message"] = "扫描文件..."
        os.makedirs(docs_dir, exist_ok=True)

        all_files = []
        for ext in (".pdf", ".txt"):
            all_files.extend(sorted(Path(docs_dir).glob(f"*{ext}")))

        if not all_files:
            RAGEngine.init_progress["running"] = False
            return {
                "success": False,
                "message": (
                    f"papers 文件夹中没有支持的文档（支持 PDF/TXT）\n"
                    f"请将论文放入: {os.path.abspath(docs_dir)}"
                ),
                "details": [],
            }

        # 一次性加载所有文档（消除重复加载，加速初始化）
        all_docs = []
        details = []
        RAGEngine.init_progress["phase"] = "load"
        RAGEngine.init_progress["message"] = f"加载 {len(all_files)} 个文件..."
        print(f"\n共发现 {len(all_files)} 个文件，正在加载...")
        for fp in all_files:
            fname = str(fp)
            docs, status = DocumentLoader.load_document(fname)
            if docs:
                title = self._get_title_from_filename(fname)
                for d in docs:
                    d.metadata["file_title"] = title
                    d.metadata["source_path"] = fname
                all_docs.extend(docs)
            details.append(f"  {status}")

        if not all_docs:
            RAGEngine.init_progress["running"] = False
            return {"success": False, "message": "所有文档加载失败", "details": details}

        # 分块
        RAGEngine.init_progress["phase"] = "split"
        RAGEngine.init_progress["message"] = "文本分块中..."
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=800,
            chunk_overlap=200,
            separators=["\n\n", "\n", "。", ".", "；", ";", " ", ""],
        )
        chunks = text_splitter.split_documents(all_docs)
        print(f"文本分块完成，共 {len(chunks)} 个文本块")

        # 清理旧 collection
        try:
            self.chroma_client.delete_collection(COLLECTION_NAME)
            print("已清理旧向量库")
        except (ValueError, chromadb.errors.NotFoundError):
            pass

        # 构建向量库（分批嵌入，带进度反馈）
        batch_size = 64
        batch_count = (len(chunks) + batch_size - 1) // batch_size
        RAGEngine.init_progress["phase"] = "embed"
        RAGEngine.init_progress["total"] = len(chunks)
        RAGEngine.init_progress["current"] = 0
        RAGEngine.init_progress["message"] = f"向量嵌入中（共 {len(chunks)} 个文本块）..."
        print(f"正在构建向量库（共 {len(chunks)} 个文本块，分 {batch_count} 批嵌入中）...")
        self.vectorstore = None
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            batch_num = i // batch_size + 1
            if self.vectorstore is None:
                self.vectorstore = Chroma.from_documents(
                    documents=batch, embedding=self.embeddings,
                    client=self.chroma_client, collection_name=COLLECTION_NAME,
                )
            else:
                self.vectorstore.add_documents(documents=batch)
            processed = min(i + batch_size, len(chunks))
            RAGEngine.init_progress["current"] = processed
            RAGEngine.init_progress["message"] = (
                f"向量嵌入中 {processed}/{len(chunks)}"
            )
            print(f"  嵌入进度: {batch_num}/{batch_count} 批 "
                  f"（{processed}/{len(chunks)} 个文本块）")

        self._rebuild_retrievers()
        RAGEngine.init_progress["running"] = False

        success_count = sum(1 for d in details if d.startswith("  [PDF]") or d.startswith("  [TXT]") or d.startswith("  [OCR]"))
        return {
            "success": True,
            "message": f"知识库构建完成！共处理 {len(all_files)} 个文件，生成 {len(chunks)} 个文本段",
            "details": details,
        }

    # ============== 检索 ==============

    def search(self, query: str, k: int = 5) -> Tuple[List[Document], List[dict]]:
        """执行混合检索（主库 + 临时库），返回 (文档, 来源信息)"""
        # 主库检索
        if self._hybrid_retriever:
            main_docs = self._hybrid_retriever.invoke(query, k=k)
        elif self.vectorstore:
            main_docs = self.vectorstore.similarity_search(query, k=k)
        else:
            main_docs = []

        # 提取来源信息
        sources = []
        seen = set()
        for d in main_docs:
            src = d.metadata.get("source_path") or d.metadata.get("source", "")
            fname = Path(src).name if src else "(未知)"
            snippet = d.page_content[:100].replace("\n", " ")
            key = fname + snippet[:30]
            if key not in seen:
                seen.add(key)
                sources.append({"file": fname, "snippet": snippet})

        return main_docs, sources

    # ============== 问答 ==============

    def ask(self, question: str, session_id: Optional[str] = None) -> dict:
        """执行 RAG 问答，返回 {"reply": str, "sources": [dict]}"""
        if self.vectorstore is None:
            return {
                "reply": "知识库尚未初始化。请先将 PDF/TXT 放入 papers 文件夹，然后点击「初始化知识库」。",
                "sources": [],
                "context": "",
            }

        try:
            main_docs, sources = self.search(question, k=5)

            # 临时知识库检索
            if session_id and session_id in self.temp_indexes:
                temp_info = self.temp_indexes[session_id]
                temp_store = temp_info.get("store")
                if temp_store:
                    try:
                        temp_results = temp_store.similarity_search(question, k=2)
                        for d in temp_results:
                            src = d.metadata.get("source", "")
                            fname = Path(src).name if src else "uploaded.pdf"
                            snippet = d.page_content[:100].replace("\n", " ")
                            sources.append({"file": f"[上传] {fname}", "snippet": snippet})
                        main_docs = main_docs + temp_results
                    except Exception:
                        pass

            if not main_docs:
                return {
                    "reply": "未找到相关内容，请确认 papers 文件夹中有相关文档。",
                    "sources": sources,
                    "context": "",
                }

            # 构建上下文（用于 LLM）
            context = "\n\n".join(
                f"【文档 {i+1}】\n{d.page_content}" for i, d in enumerate(main_docs)
            )

            # 构建人类可读的上下文摘要（返回给前端，每段前 200 字）
            context_preview = "\n\n".join(
                f"【文档 {i+1}】{d.page_content[:200].replace(chr(10), ' ')}"
                for i, d in enumerate(main_docs)
            )

            prompt_template = """你是一个精确的学术文献助手。请严格遵循以下规则回答用户问题：

规则1：如果提供的文档片段中包含了与问题直接相关的信息，请直接引用原文的具体内容来回答，不要做任何主观判断。

规则2：如果文档片段中出现了用户问题所问的类别名称、定义、数据等具体内容，哪怕这些内容在不同文档中表述方式不同，也应直接提取并引用。不要自行判断"这是不是用户想要的答案"。

规则3：只有当所有文档片段都完全没有涉及用户问题的任何相关信息时，才能回答"现有信息不足以回答该问题"。

文档片段如下：
{context}

用户问题：{question}

请回答："""

            messages = [
                HumanMessage(content=prompt_template.format(context=context, question=question)),
            ]

            response = self.llm.invoke(messages)
            return {"reply": response.content, "sources": sources, "context": context_preview}

        except Exception as e:
            err = str(e).lower()
            if "401" in err or "unauthorized" in err:
                return {"reply": "API 认证失败，请检查 API Key", "sources": [], "context": ""}
            elif "timeout" in err:
                return {"reply": "请求超时，请检查网络连接", "sources": [], "context": ""}
            elif "connection" in err:
                return {"reply": "无法连接 DeepSeek API", "sources": [], "context": ""}
            else:
                return {"reply": f"生成回答时出错: {str(e)}", "sources": [], "context": ""}

    # ============== 临时上传 ==============

    def add_temp_upload(self, session_id: str, file_path: str) -> dict:
        """为会话添加临时上传文档"""
        docs, status = DocumentLoader.load_document(file_path)
        if not docs:
            return {"success": False, "message": status}

        title = self._get_title_from_filename(file_path)
        for d in docs:
            d.metadata["file_title"] = title
            d.metadata["source_path"] = file_path

        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=800, chunk_overlap=200,
            separators=["\n\n", "\n", "。", ".", "；", ";", " ", ""],
        )
        chunks = text_splitter.split_documents(docs)

        temp_coll = f"temp_{session_id}"
        # 使用已有的 chroma_client 而非新建，避免文件锁冲突
        try:
            self.chroma_client.delete_collection(temp_coll)
        except (ValueError, chromadb.errors.NotFoundError):
            pass

        temp_store = Chroma.from_documents(
            documents=chunks, embedding=self.embeddings,
            client=self.chroma_client, collection_name=temp_coll,
        )

        if session_id not in self.temp_indexes:
            self.temp_indexes[session_id] = {"store": temp_store, "files": []}
        else:
            self.temp_indexes[session_id]["store"] = temp_store
        self.temp_indexes[session_id]["files"].append(file_path)

        return {
            "success": True,
            "message": f"已加载上传文件，生成 {len(chunks)} 个文本段",
            "file": Path(file_path).name,
        }

    def remove_session_temp(self, session_id: str) -> None:
        """清理会话临时数据"""
        self.temp_indexes.pop(session_id, None)
        try:
            self.chroma_client.delete_collection(f"temp_{session_id}")
        except Exception:
            pass

    def _cleanup_temp_files(self) -> None:
        """清理过期临时文件"""
        if not os.path.exists(TEMP_DIR):
            os.makedirs(TEMP_DIR, exist_ok=True)
            return
        cutoff = datetime.now() - timedelta(hours=MAX_TEMP_AGE_HOURS)
        for item in os.listdir(TEMP_DIR):
            item_path = os.path.join(TEMP_DIR, item)
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(item_path))
                if mtime < cutoff:
                    (shutil.rmtree if os.path.isdir(item_path) else os.remove)(item_path)
            except Exception:
                pass
