"""Small extraction subprocess. It does not import application clients or model runtimes."""
import json
from contextlib import redirect_stdout
import sys
from pathlib import Path


def extract_document(path: str, max_pages: int) -> dict:
    source = Path(path)
    if source.suffix.lower() == ".md":
        content = source.read_bytes().decode("utf-8")
        if not content.strip():
            raise ValueError("document contains no extractable text")
        if len(content) > 2_000_000:
            raise ValueError("extracted text limit exceeded")
        from app.rag.markdown import markdown_sections
        sections = markdown_sections(content)
    elif source.suffix.lower() == ".pdf":
        from app.rag.pdf import extract_pdf
        return extract_pdf(source, max_pages)
    else:
        raise ValueError("unsupported knowledge file")
    return {"content": content, "sections": sections}


def extract_bounded(path: str, max_pages: int) -> str:
    return extract_document(path, max_pages)["content"]


if __name__ == "__main__":
    try:
        if sys.platform != "win32":
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
        # Native parser libraries may print diagnostics. Keep stdout as a single
        # JSON protocol frame and send those diagnostics to the bounded stderr.
        with redirect_stdout(sys.stderr):
            result = extract_document(sys.argv[1], int(sys.argv[2]))
        print(json.dumps(result, ensure_ascii=True))
    except Exception as error:
        print(json.dumps({"error": str(error)[:1000]}))
        sys.exit(1)
