import docx
import sys
import os

def extract_text(docx_path):
    doc = docx.Document(docx_path)
    full_text = []
    for para in doc.paragraphs:
        full_text.append(para.text)
    return '\n'.join(full_text)

if __name__ == '__main__':
    docx_path = sys.argv[1]
    if not os.path.exists(docx_path):
        print(f"文件不存在: {docx_path}")
        sys.exit(1)
    text = extract_text(docx_path)
    print(text)