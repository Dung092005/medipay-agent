from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.config import get_settings


def split_document(content: str) -> list[str]:
    settings = get_settings()
    settings.validate_chunk_settings()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    return splitter.split_text(content)
