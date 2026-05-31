import os
import numpy as np
import uuid
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchAny
from langchain_openai import ChatOpenAI
from sentence_transformers import SentenceTransformer

load_dotenv()
os.environ['HF_HOME'] = '/p/repo-2-graph/models'

class VectorStore:
    def __init__(self, files: dict, collection_name: str):
        self.files = files
        self.collection_name = collection_name
        self.chunks = []
        self._embed_model = None
        
        url = os.getenv("QDRANT_END_POINT")
        api_key = os.getenv("QDRANT_API_KEY")
        
        if not url or not api_key:
            raise ValueError("Please set QDRANT_END_POINT and QDRANT_API_KEY in your .env file!")
            
        self.client = QdrantClient(url=url, api_key=api_key, timeout=60)
        
        self.client.set_model("jinaai/jina-embeddings-v2-base-code")
        self.client.set_sparse_model("Qdrant/bm25")
        
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=self.client.get_fastembed_vector_params(),
                sparse_vectors_config=self.client.get_fastembed_sparse_vector_params()
            )
            
        # Create a payload index on the "path" field so we can filter by it
        self.client.create_payload_index(
            collection_name=self.collection_name,
            field_name="path",
            field_schema="keyword"
        )

    @property
    def embed_model(self):
        if self._embed_model is None:
            print("Loading SentenceTransformer model...")
            self._embed_model = SentenceTransformer("jinaai/jina-embeddings-v2-base-code")
        return self._embed_model

    def build(self, max_module_lines=100, overlap=5):
        self.chunks = []
        
        for file in self.files.keys():
            content = self.files[file]['content']
            lines = content.split('\n')
            classes = self.files[file]['classes']
            functions = self.files[file]['functions']
            
            for cls in classes:
                start_line = cls['line_start'] - 1
                end_line = cls['line_end']
                
                chunk = "\n".join(lines[start_line:end_line])
                metadata = {
                    "path": file.replace("\\", "/"),
                    "filename": file.split("/")[-1],
                    "function_name": cls['name'],
                    "class_name": cls['name'],
                    "chunk_type": "class",
                    "line_start": start_line + 1,
                    "line_end": end_line,
                    "methods": [f['name'] for f in functions 
                            if f.get('class_name') == cls['name']]
                }
                self.chunks.append([chunk, metadata])
            
            for func in functions:
                # Skip if it's a method (already in class chunk)
                if func.get('class_name'):
                    continue
                
                # Find decorator context
                start_line = func['line_start'] - 1
                while start_line > 0 and lines[start_line - 1].strip().startswith('@'):
                    start_line -= 1
                
                end_line = func['line_end']
                
                chunk = "\n".join(lines[start_line:end_line])
                metadata = {
                    "path": file.replace("\\", "/"),
                    "filename": file.split("/")[-1],
                    "function_name": func['name'],
                    "class_name": "module_level",
                    "chunk_type": "function",
                    "line_start": start_line + 1,
                    "line_end": end_line
                }
                self.chunks.append([chunk, metadata])

            # Collect all lines that are NOT in functions or classes
            function_ranges = [(f['line_start'] - 1, f['line_end']) for f in functions]
            class_ranges = [(c['line_start'] - 1, c['line_end']) for c in classes]
            all_ranges = function_ranges + class_ranges
            
            # Collect SEPARATE module segments (imports, constants, etc.)
            module_lines = []
            for i, line in enumerate(lines):
                in_func_or_class = any(start <= i < end for start, end in all_ranges)
                if not in_func_or_class and line.strip():  # Skip empty lines
                    module_lines.append(i)

            if module_lines:
                for chunk_start_idx in range(0, len(module_lines), 50):
                    chunk_indices = module_lines[chunk_start_idx:chunk_start_idx+50]
                    start_line = chunk_indices[0]
                    end_line = chunk_indices[-1] + 1
                    
                    chunk = "\n".join(lines[start_line:end_line])
                    metadata = {
                        "path": file.replace("\\", "/"),
                        "filename": file.split("/")[-1],
                        "function_name": f"module_segment_{chunk_start_idx//50}",
                        "class_name": "module_level",
                        "chunk_type": "module",
                        "line_start": start_line + 1,
                        "line_end": end_line
                    }
                    self.chunks.append([chunk, metadata])

    def push(self, batch_size: int = 32):
        if not self.chunks:
            print("No chunks to push. Run build() first.")
            return

        documents = [c[0] for c in self.chunks]
        metadatas = [c[1] for c in self.chunks]
        ids = [
            str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{m['path']}:{m['function_name']}:{i}"))
            for i, m in enumerate(metadatas)
        ]

        total = len(documents)
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            print(f"Pushing batch {start}-{end} / {total}...")
            
            self.client.add(
                collection_name=self.collection_name,
                documents=documents[start:end],
                metadata=metadatas[start:end],
                ids=ids[start:end]
            )
        
        print(f"Successfully pushed {total} chunks to Qdrant!")

    def search(self, query: str, query_filter=None, top_k: int = 5):
        results = self.client.query(
            collection_name=self.collection_name,
            query_text=query,
            query_filter=query_filter,
            limit=top_k
        )

        return {
            "documents": [[hit.document for hit in results]],
            "metadatas": [[hit.metadata for hit in results]],
            "ids": [[str(hit.id) for hit in results]]
        }

    def vector_search(self, query: str, filenames: list, top_k: int = 5):
        qdrant_filter = Filter(
            must=[
                FieldCondition(
                    key="path",
                    match=MatchAny(any=filenames)
                )
            ]
        )
        return self.search(
            query=query, 
            query_filter=qdrant_filter, 
            top_k=top_k
        )
    
    def rerank(self, context: dict, query: str, top_k: int):
        documents = context['documents'][0]
        metadatas = context['metadatas'][0]
        
        if not documents:
            return []
        
        query_embedding = self.embed_model.encode(query, convert_to_numpy=True)
        doc_embeddings = self.embed_model.encode(documents, convert_to_numpy=True)
        
        scores = np.dot(doc_embeddings, query_embedding)
        
        ranked = sorted(
            [(metadatas[i], scores[i], documents[i]) for i in range(len(documents))],
            key=lambda x: x[1],
            reverse=True
        )[:top_k]
        
        return ranked