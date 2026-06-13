import os
import re
import time
import logging
import traceback
from typing import Literal, Optional
from pydantic import BaseModel, Field
from neo4j import GraphDatabase
from fuzzywuzzy import process, fuzz
from qdrant_client.models import Filter, FieldCondition, MatchAny

logger = logging.getLogger("askmyrepo.query_engine")

VALID_FILE_PROPERTIES = {
    'name', 'path', 'functions', 'classes', 'imports', 'repo_id',
    'qualified_name', 'class_name', 'bases', 'module', 'alias', 'line',
    'line_start', 'line_end', 'is_entry', 'entry_kind', 'entry_confidence',
}


class CypherQuery(BaseModel):
    cypher: str = Field(..., description="Cypher query for retrieving relationships within the nodes")


class RouterDecision(BaseModel):
    decision: Literal['hybrid', 'graph_only'] = Field(
        ...,
        description="graph_only ONLY for pure structural/dependency/topology questions. hybrid for everything else."
    )
    reason: str = Field(default="Routed by QueryEngine", description="Brief explanation of the routing decision")


class ArchitectSubtype(BaseModel):
    subtype: Literal['request_flow', 'system_overview', 'dependency_map', 'class_interaction', 'entry_call_chain'] = Field(
        ...,
        description="Architecture question subtype for deep graph traversal"
    )
    reason: str = Field(default="", description="Why this subtype was chosen")


class QueryEngine:
    def __init__(self, repo_id: str, db_client, uri: str = None, user: str = None, password: str = None, llm=None):
        self.db_client = db_client
        self.repo_id = repo_id
        self.llm = llm

        uri = uri or os.getenv("NEO4j_URI") or os.getenv("NEO4J_URI")
        user = user or os.getenv("NEO4j_USER") or os.getenv("NEO4J_USER")
        password = password or os.getenv("NEO4j_PASS") or os.getenv("NEO4J_PASS")

        if not uri or not user or not password:
            raise ValueError("Neo4j credentials are not set in the environment or constructor!")

        self.graph_driver = GraphDatabase.driver(uri, auth=(user, password))
        logger.debug(f"QueryEngine initialized for repo: {repo_id}")
        self._file_index = None
        self._router_cache: dict[str, RouterDecision] = {}
        self._graph_cache: dict[str, dict | None] = {}
        self.query_templates = self._init_templates()
        self.architect_templates = self._init_architect_templates()
        self.min_result_count = 1
        self.max_result_variance = 0.5

    def _init_architect_templates(self) -> dict:
        return {
            "entry_call_chain": """
                MATCH (entry:Function {repo_id: $repo_id})
                WHERE entry.is_entry = true
                MATCH path = (entry)-[:CALLS*1..6]->(downstream:Function)
                WHERE downstream.repo_id = $repo_id
                RETURN entry.qualified_name as entry_point,
                       entry.entry_kind as kind,
                       [n in nodes(path) | n.qualified_name] as call_chain,
                       length(path) as depth
                ORDER BY depth DESC LIMIT 15
            """,
            "request_flow": """
                MATCH (entry:Function {repo_id: $repo_id})
                WHERE entry.is_entry = true
                  AND entry.entry_kind IN ['http_endpoint', 'app_runner']
                MATCH path = (entry)-[:CALLS*1..6]->(sink:Function)
                WHERE sink.repo_id = $repo_id
                  AND (NOT (sink)-[:CALLS]->() OR (sink)-[:CALLS_EXTERNAL]->())
                RETURN [n in nodes(path) | n.qualified_name] as flow,
                       length(path) as depth
                ORDER BY depth DESC LIMIT 10
            """,
            "dependency_map": """
                MATCH (r:Repo {repo_id: $repo_id})-[:CONTAINS]->(f:File)
                MATCH path = (f)-[:IMPORTS*1..4]->(dep:File)
                WHERE dep.repo_id = $repo_id
                RETURN f.path as source,
                       [n in nodes(path) | n.path] as dependency_chain
                ORDER BY length(path) DESC LIMIT 30
            """,
            "class_interaction": """
                MATCH (c1:Class {repo_id: $repo_id})-[:HAS_METHOD]->(m:Function)
                MATCH (m)-[:CALLS|INSTANTIATES]->(target)
                MATCH (c2:Class {repo_id: $repo_id})-[:HAS_METHOD]->(target)
                WHERE c1 <> c2
                RETURN c1.name as from_class, m.name as via_method,
                       c2.name as to_class
                ORDER BY from_class
            """,
            "system_overview": """
                MATCH (r:Repo {repo_id: $repo_id})-[:CONTAINS]->(f:File)
                OPTIONAL MATCH (f)-[:DEFINES_FUNCTION]->(fn:Function)
                OPTIONAL MATCH (f)-[:DEFINES_CLASS]->(c:Class)
                RETURN f.path as file,
                       collect(DISTINCT fn.name) as functions,
                       collect(DISTINCT c.name) as classes,
                       collect(DISTINCT CASE WHEN fn.is_entry THEN fn.entry_kind END) as entry_kinds
                ORDER BY f.path
            """,
        }

    def _init_templates(self) -> dict:
        return {
            "multi_hop_imports": """
                MATCH (r:Repo {{repo_id: '{repo_id}'}})
                MATCH (source:File {{name: '{source}'}})-[:IMPORTS]->(via:File {{name: '{via}'}})
                MATCH (via:File)-[:IMPORTS]->(target:File)
                RETURN DISTINCT target.path as indirect_dependency
                ORDER BY target.path
            """,
            "leaf_nodes": """
                MATCH (r:Repo {{repo_id: '{repo_id}'}})-[:CONTAINS]->(f:File)
                WHERE NOT (f)-[:IMPORTS]->()
                RETURN f.path as leaf_file
                ORDER BY f.path
            """,
            "most_dependencies": """
                MATCH (r:Repo {{repo_id: '{repo_id}'}})-[:CONTAINS]->(f:File)
                MATCH (f)-[:IMPORTS]->(deps:File)
                WITH f, count(DISTINCT deps) as dep_count
                ORDER BY dep_count DESC
                LIMIT 5
                RETURN f.path as file, dep_count as dependency_count
            """,
            "direct_imports": """
                MATCH (r:Repo {{repo_id: '{repo_id}'}})
                MATCH (f:File {{name: '{filename}'}})-[:IMPORTS]->(imported:File)
                RETURN imported.path as imported_file
                ORDER BY imported.path
            """,
            "reverse_lookup": """
                MATCH (r:Repo {{repo_id: '{repo_id}'}})
                MATCH (importer:File)-[:IMPORTS]->(target:File {{name: '{filename}'}})
                RETURN importer.path as file_that_imports
                ORDER BY importer.path
            """,
            "transitive_dependencies": """
                MATCH (r:Repo {{repo_id: '{repo_id}'}})
                MATCH path=(source:File {{name: '{source}'}})-[:IMPORTS*1..3]->(target:File)
                WHERE source <> target
                RETURN DISTINCT target.path as transitive_dep
                ORDER BY target.path
            """,
            "file_structure": """
                MATCH (r:Repo {{repo_id: '{repo_id}'}})-[:CONTAINS]->(f:File {{name: '{filename}'}})
                MATCH (f)-[:DEFINES_CLASS]->(c:Class)
                MATCH (f)-[:DEFINES_FUNCTION]->(fn:Function)
                RETURN f.path as file_path,
                       collect(DISTINCT c.name) as classes,
                       collect(DISTINCT fn.name) as functions
            """,
            "call_graph": """
                MATCH (r:Repo {{repo_id: '{repo_id}'}})
                MATCH (caller:Function {{name: '{function_name}'}})-[:CALLS]->(callee:Function)
                RETURN callee.qualified_name as called_function
                ORDER BY called_function
            """,
            "class_hierarchy": """
                MATCH (r:Repo {{repo_id: '{repo_id}'}})
                MATCH (c:Class {{name: '{class_name}'}})-[:INHERITS_FROM]->(parent:Class)
                RETURN parent.qualified_name as parent_class
                ORDER BY parent_class
            """,
            "all_files": """
                MATCH (r:Repo {{repo_id: '{repo_id}'}})-[:CONTAINS]->(f:File)
                RETURN f.path as file_path
                ORDER BY f.path
            """,
        }

    def _match_template(self, query: str) -> Optional[tuple[str, dict]]:
        match = re.search(
            r'(?:which\s+)?files?\s+(?:does?\s+)?(\w+\.py)\s+(?:indirectly\s+)?depend(?:s|ency)?\s+on\s+through\s+(\w+\.py)',
            query.lower()
        )
        if match:
            return ("multi_hop_imports", {
                "repo_id": self.repo_id, "source": match.group(1), "via": match.group(2)
            })

        if re.search(r'leaf\s+node|no\s+import|do\s+not\s+import\s+any', query.lower()):
            return ("leaf_nodes", {"repo_id": self.repo_id})

        if re.search(r'most\s+dependen|highest\s+dependen|most\s+import', query.lower()):
            return ("most_dependencies", {"repo_id": self.repo_id})

        match = re.search(r'(?:what\s+(?:does|do)\s+)?(\w+\.py)\s+import', query.lower())
        if match:
            return ("direct_imports", {"repo_id": self.repo_id, "filename": match.group(1)})

        match = re.search(r'(?:which|what)\s+files?\s+import\s+(\w+\.py)', query.lower())
        if match:
            return ("reverse_lookup", {"repo_id": self.repo_id, "filename": match.group(1)})

        match = re.search(r'transitive\s+dependenc(?:y|ies).*?(\w+\.py)', query.lower())
        if match:
            return ("transitive_dependencies", {"repo_id": self.repo_id, "source": match.group(1)})

        match = re.search(r'(?:structure|content|what\s+is\s+in)\s+(\w+\.py)', query.lower())
        if match:
            return ("file_structure", {"repo_id": self.repo_id, "filename": match.group(1)})

        match = re.search(r'(?:what\s+(?:does|do)\s+)?(?:function\s+)?(\w+)\s+call', query.lower())
        if match:
            return ("call_graph", {"repo_id": self.repo_id, "function_name": match.group(1)})

        match = re.search(r'(?:what\s+(?:does|do)\s+)?(?:class\s+)?(\w+)\s+inherit', query.lower())
        if match:
            return ("class_hierarchy", {"repo_id": self.repo_id, "class_name": match.group(1)})

        return None

    def _classify_architect_subtype(self, query: str) -> str:
        q = query.lower()
        if any(kw in q for kw in ("request flow", "end-to-end", "api to", "trace the call", "call chain", "from api")):
            return "request_flow"
        if any(kw in q for kw in ("dependency map", "module depend", "how do modules", "import chain")):
            return "dependency_map"
        if any(kw in q for kw in ("class interact", "between classes", "cross-class")):
            return "class_interaction"
        if any(kw in q for kw in ("entry point", "main entry", "call chain from entry", "bootstrapping")):
            return "entry_call_chain"
        if any(kw in q for kw in ("overview", "architecture", "system", "components connected", "how are components")):
            return "system_overview"

        if self.llm:
            try:
                classifier = self.llm.with_structured_output(ArchitectSubtype)
                result: ArchitectSubtype = classifier.invoke([
                    ("system", "Classify the architecture question subtype."),
                    ("user", query),
                ])
                return result.subtype
            except Exception as e:
                logger.warning(f"Architect subtype LLM classification failed: {e}")
        return "system_overview"

    def _execute_architect_template(self, template_name: str) -> dict | None:
        try:
            cypher = self.architect_templates[template_name]
            with self.graph_driver.session() as session:
                result = session.run(cypher, repo_id=self.repo_id)
                data = [record.data() for record in result]
            logger.debug(f"[architect] Template '{template_name}': {len(data)} results")
            return {
                "is_fallback": False, "data": data, "response": cypher,
                "method": "architect", "template_name": template_name,
                "timestamp": time.time(),
            }
        except Exception as e:
            logger.error(f"[Architect Template Error] {template_name}: {e}")
            return None

    def architect_search(self, query: str) -> dict | None:
        subtype = self._classify_architect_subtype(query)
        logger.info(f"[architect] Subtype: {subtype}")

        template_map = {
            "request_flow": ["request_flow", "entry_call_chain"],
            "system_overview": ["system_overview", "entry_call_chain"],
            "dependency_map": ["dependency_map"],
            "class_interaction": ["class_interaction"],
            "entry_call_chain": ["entry_call_chain", "request_flow"],
        }
        templates = template_map.get(subtype, ["system_overview"])

        combined_data = []
        used_templates = []
        for tpl in templates[:2]:
            result = self._execute_architect_template(tpl)
            if result and result.get("data"):
                combined_data.extend(result["data"])
                used_templates.append(tpl)

        if not combined_data:
            logger.info("[architect] No data returned from any template")
            return None

        return {
            "is_fallback": False, "data": combined_data, "method": "architect",
            "subtype": subtype, "templates": used_templates,
            "timestamp": time.time(),
        }

    def extract_critical_path_files(self, architect_result: dict, limit: int = 4) -> list[str]:
        paths: list[str] = []
        for record in architect_result.get("data", []):
            for key, val in record.items():
                if isinstance(val, str) and (val.endswith(".py") or "/" in val):
                    paths.append(val)
                elif isinstance(val, list):
                    for item in val:
                        if isinstance(item, str):
                            if item.endswith(".py") or "::" in item:
                                fn_path = item.split("::")[0] if "::" in item else item
                                if fn_path.endswith(".py"):
                                    paths.append(fn_path)
        seen = set()
        unique = []
        for p in paths:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        return unique[:limit]

    def _execute_template(self, template_name: str, params: dict) -> dict | None:
        try:
            cypher = self.query_templates[template_name].format(**params)
            with self.graph_driver.session() as session:
                result = session.run(cypher)
                data = [record.data() for record in result]
            return {
                "is_fallback": False, "data": data, "response": cypher,
                "method": "template", "template_name": template_name,
                "timestamp": time.time(),
            }
        except Exception as e:
            logger.error(f"[Template Error] {template_name}: {e}")
            return None

    def graph_search(self, query: str) -> dict | None:
        if query in self._graph_cache:
            logger.debug("[graph] Cache hit")
            return self._graph_cache[query]

        template_match = self._match_template(query)
        if template_match:
            template_name, params = template_match
            logger.debug(f"[graph] Template match: {template_name}")
            result = self._execute_template(template_name, params)
            if result:
                self._graph_cache[query] = result
                return result

        logger.debug("[graph] No template match — using LLM-generated Cypher")
        structured_llm = self.llm.with_structured_output(CypherQuery)

        index = self._load_file_index()
        sample = index[:15]
        file_list = "\n".join(f"- {f['name']} ({f['path']})" for f in sample)

        system_content = f"""You are a Cypher expert for Neo4j. Generate ONLY valid, working queries.
            CRITICAL RULES:
            1. ALWAYS start with: MATCH (r:Repo {{repo_id: '{self.repo_id}'}})
            2. NEVER use properties not in VALID_PROPERTIES list
            3. For multi-hop queries (A -> B -> C), use proper MATCH chains with intermediate variables
            4. ALWAYS use DISTINCT if returning multiple instances
            5. ALWAYS ORDER BY results for consistency
            6. For "files that depend on X", use reverse relationship direction
            7. For "transitive" queries, use variable-length patterns: -[:IMPORTS*1..3]->
            8. Return DISTINCT paths to avoid duplicates
            9. Always filter by repo_id to scope queries
            10. If unsure about relationship direction, return sentinel query

            SCHEMA:
            Nodes:
            - Repo(repo_id)
            - File(name, path, functions, classes, imports, repo_id)
            - Class(name, qualified_name, path, bases, line_start, line_end, repo_id)
            - Function(name, qualified_name, path, class_name, line_start, line_end, repo_id)
            - Import(name, module, alias, path, line, repo_id)
            - ExternalSymbol(name, repo_id)

            Edges (all have repo_id):
            - (Repo)-[:CONTAINS]->(File)
            - (File)-[:IMPORTS]->(File)
            - (File)-[:IMPORTS_SYMBOL]->(Import)
            - (File)-[:DEFINES_CLASS]->(Class)
            - (File)-[:DEFINES_FUNCTION]->(Function)
            - (Class)-[:HAS_METHOD]->(Function)
            - (Class)-[:INHERITS_FROM]->(Class)
            - (Class)-[:INHERITS_EXTERNAL]->(ExternalSymbol)
            - (Function)-[:CALLS]->(Function)
            - (Function)-[:INSTANTIATES]->(Class)
            - (Function)-[:CALLS_EXTERNAL]->(ExternalSymbol)

            VALID PROPERTIES: {', '.join(sorted(VALID_FILE_PROPERTIES))}

            SAMPLE FILES IN REPO:
            {file_list}

            EXAMPLES OF CORRECT QUERIES:
            Example 1: Multi-hop (A -> B -> C)
            Query: "What does X import transitively?"
            Example 2: Leaf nodes (no outgoing edges)
            Query: "Which files have no imports?"
            Example 3: Reverse lookup (who imports X)
            Query: "Which files import X?"
            Example 4: Variable-length (transitive)
            Query: "Transitive dependencies of X?"

            IMPORTANT:
            - For behavioral questions (what does code do), return sentinel immediately
            - Never hardcode file names except when explicitly mentioned in question
            - Use DISTINCT to avoid duplicates
            - Filter by repo_id in first MATCH
            - Return file paths, not File nodes themselves
            """

        response = structured_llm.invoke([
            ("system", system_content),
            ("user", f"Generate Cypher for: {query}")
        ])

        try:
            safe_cypher = self.sanitize_cypher(response.cypher)
            if not safe_cypher:
                logger.warning(f"[Warning] Sanitization failed for query: {query}")
                logger.warning(f"  Generated Cypher: {response.cypher}")
                return None

            safe_cypher = self.resolve_names_in_cypher(safe_cypher)

            with self.graph_driver.session() as session:
                result = session.run(safe_cypher)
                data = [record.data() for record in result]

            is_fallback = (
                len(data) == 1 and
                len(data[0]) == 1 and
                "f.path" in data[0]
            )

            output = {
                "is_fallback": is_fallback, "data": data,
                "response": response.cypher, "method": "llm",
                "timestamp": time.time(),
            }

            self._graph_cache[query] = output
            logger.debug(f"[graph] LLM Cypher returned {len(data)} rows")
            return output

        except Exception as e:
            logger.error(f"[Error] Graph query failed:")
            logger.error(f"  Query: {query}")
            logger.error(f"  Generated Cypher: {response.cypher}")
            logger.error(f"  Exception: {type(e).__name__}: {e}")
            for line in traceback.format_exc().splitlines():
                logger.error(f"  {line}")
            return None

    def _validate_graph_result(self, result: dict | None, query: str) -> dict | None:
        if result is None:
            return None

        data = result.get("data", [])

        if not data:
            logger.debug(f"[Validation] Empty result for query: {query}")
            return result

        if len(data) == 1 and any(
            kw in query.lower()
            for kw in ["indirectly", "transitive", "through"]
        ):
            logger.warning(f"[Validation] Single result for multi-hop query: {query}")
            result["confidence"] = 0.3
            return result

        if len(data) > 1:
            result["confidence"] = min(1.0, len(data) / 5.0)
        else:
            result["confidence"] = 0.5

        file_count = 0
        for record in data:
            for k, v in record.items():
                if isinstance(v, str) and v.endswith(".py"):
                    file_count += 1
                elif isinstance(v, list):
                    file_count += len([x for x in v if isinstance(x, str) and x.endswith(".py")])

        result["extracted_file_count"] = file_count

        if file_count < self.min_result_count:
            logger.warning(f"[Validation] Low file count: {file_count}")
            result["is_incomplete"] = True
        else:
            result["is_incomplete"] = False

        return result

    def _extract_filenames_safe(self, graph_result: dict | None, query: str) -> list[str]:
        if not graph_result:
            return self.extract_entities_from_query(query)

        graph_result = self._validate_graph_result(graph_result, query)

        if not self._is_meaningful(graph_result):
            logger.debug(f"[Fallback] Graph result not meaningful, using entity extraction")
            return self.extract_entities_from_query(query)

        filenames = self._extract_filenames(graph_result["data"])

        if graph_result.get("is_incomplete"):
            logger.debug(f"[Augmentation] Graph result incomplete, augmenting with entity extraction")
            entity_files = self.extract_entities_from_query(query)
            filenames = list(set(filenames) | set(entity_files))

        if not filenames:
            logger.debug(f"[Fallback] No filenames extracted, using entity extraction")
            return self.extract_entities_from_query(query)

        logger.debug(f"[Validation] Extracted {len(filenames)} files from graph: {filenames[:3]}...")
        return filenames

    def _load_file_index(self) -> list[dict]:
        if self._file_index is not None:
            return self._file_index

        with self.graph_driver.session() as session:
            result = session.run(f"""
                MATCH (r:Repo {{repo_id: '{self.repo_id}'}})-[:CONTAINS]->(f:File)
                OPTIONAL MATCH (f)-[:DEFINES_CLASS]->(c:Class)
                OPTIONAL MATCH (f)-[:DEFINES_FUNCTION]->(fn:Function)
                OPTIONAL MATCH (c)-[:HAS_METHOD]->(m:Function)
                RETURN f.name as name, f.path as path,
                       collect(DISTINCT c.name) as classes,
                       collect(DISTINCT fn.name) + collect(DISTINCT m.name) as functions
            """)
            self._file_index = [dict(r) for r in result]

        logger.debug(f"[index] Loaded {len(self._file_index)} files into index")
        return self._file_index

    def resolve_filename(self, raw_name: str) -> str | None:
        index = self._load_file_index()
        names = [f["name"] for f in index]

        if raw_name in names:
            return raw_name

        match = process.extractOne(raw_name, names, scorer=fuzz.token_sort_ratio)
        if match and match[1] >= 70:
            return match[0]

        return None

    def extract_entities_from_query(self, query: str) -> list[str]:
        index = self._load_file_index()
        entity_map: dict[str, str] = {}

        for f in index:
            entity_map[f["name"]] = f["path"]
            for cls in (f.get("classes") or []):
                entity_map[cls] = f["path"]
            for fn in (f.get("functions") or []):
                entity_map[fn] = f["path"]

        if not entity_map:
            return []

        words = query.replace("'s", "").split()
        bigrams = [f"{words[i]} {words[i + 1]}" for i in range(len(words) - 1)]
        candidates = words + bigrams

        matched_paths = []
        for candidate in candidates:
            match = process.extractOne(
                candidate, list(entity_map.keys()), scorer=fuzz.token_sort_ratio
            )
            if match and match[1] >= 75:
                matched_paths.append(entity_map[match[0]])

        return list(set(matched_paths))

    def sanitize_cypher(self, cypher: str) -> str:
        cypher_clean = re.sub(r"'[^']*'|\"[^\"]*\"", "''", cypher)
        used_props = set(re.findall(r'\b\w+\.(\w+)\b', cypher_clean))
        cypher_keywords = {'path', 'type', 'keys', 'labels', 'id', 'properties'}
        invalid = used_props - VALID_FILE_PROPERTIES - cypher_keywords
        if invalid:
            logger.warning(f"[Sanitize] Blocked invalid properties: {invalid}")
            return None
        return cypher

    def resolve_names_in_cypher(self, cypher: str) -> str:
        def replacer(match):
            raw = match.group(1)
            resolved = self.resolve_filename(raw)
            if resolved and resolved != raw:
                logger.debug(f"[resolve] '{raw}' -> '{resolved}'")
                return match.group(0).replace(raw, resolved)
            return match.group(0)

        pattern = r"\{(?:name|source|via):\s*['\"]([^'\"]+)['\"]\}"
        return re.sub(pattern, replacer, cypher)

    def _is_meaningful(self, graph_result: dict) -> bool:
        if graph_result is None:
            return False
        return not graph_result.get("is_fallback", False) and bool(graph_result.get("data"))

    def vector_search(self, query: str, filenames: list, top_k: int = 5):
        return self.db_client.vector_search(query, filenames=filenames, top_k=top_k)

    def rerank(self, context: dict, query: str, top_k: int):
        return self.db_client.rerank(context, query, top_k)

    def _extract_filenames(self, graph_data: list[dict]) -> list[str]:
        paths = []
        for record in graph_data:
            for v in record.values():
                if isinstance(v, str) and v.endswith(".py"):
                    paths.append(v)
                elif isinstance(v, list):
                    paths.extend(x for x in v if isinstance(x, str) and x.endswith(".py"))
        return list(set(paths))

    def _resolve_filenames(self, graph_result: dict | None, query: str) -> list[str]:
        if graph_result and self._is_meaningful(graph_result):
            filenames = self._extract_filenames(graph_result["data"])
            if filenames:
                return filenames

        logger.debug("[fallback] Graph returned no meaningful data, using entity extraction")
        return self.extract_entities_from_query(query)

    def router(self, query: str) -> RouterDecision:
        if query in self._router_cache:
            return self._router_cache[query]

        router_llm = self.llm.with_structured_output(RouterDecision)
        router_prompt = [
            ("system", """You are a query router for code repository Q&A.
                The knowledge graph stores: files, imports, classes, functions, methods, inheritance, call edges.
                It has NO knowledge of variable values, runtime state, or full code behavior.

                Classify into:

                graph_only — answerable purely from code structure:
                - which files import X
                - what does <file> depend on
                - where is a class/function/method defined
                - which methods belong to a class
                - which functions/methods call or instantiate another symbol
                - which files have no imports
                - which file has the most dependencies
                - transitive dependencies of <file>
                - what files depend on <file> (reverse lookup)

                hybrid — everything else:
                - what a function/method does internally or returns
                - what happens when a condition be met
                - initial / default value of anything
                - how a feature is implemented
                - what database / framework / library is used
                - any question about runtime behavior, state, or logic

                RULE: If mentioning specific variables/fields or asking about behavior → hybrid.
                When in doubt, choose hybrid.
            """),
            ("user", f"query: {query}")
        ]

        from langchain_core.prompts import ChatPromptTemplate
        prompt_template = ChatPromptTemplate.from_messages(router_prompt)
        chain = prompt_template | router_llm
        result = chain.invoke({"query": query})
        self._router_cache[query] = result
        return result

    def get_result(self, query: str, top_k: int = 5):
        route = self.router(query).decision
        retrieve_k = top_k

        def _pure_vector():
            vector_data = self.db_client.search(query=query, top_k=retrieve_k)
            return {
                "type": "hybrid",
                "graph": [],
                "vector": self.rerank(vector_data, query, top_k),
            }

        if route == "graph_only":
            graph_result = self.graph_search(query)

            if graph_result is None:
                return _pure_vector()

            if self._is_meaningful(graph_result):
                return {"type": "graph", "graph": graph_result["data"]}

            filenames = self._extract_filenames_safe(graph_result, query)
            if filenames:
                vector_data = self.vector_search(query, filenames=filenames, top_k=retrieve_k)
            else:
                vector_data = self.db_client.search(query=query, top_k=retrieve_k)

            return {
                "type": "hybrid",
                "graph": [],
                "vector": self.rerank(vector_data, query, top_k),
            }

        else:
            graph_result = self.graph_search(query)

            if graph_result is None:
                return _pure_vector()

            filenames = self._extract_filenames_safe(graph_result, query)
            if filenames:
                vector_data = self.vector_search(query, filenames=filenames, top_k=retrieve_k)
            else:
                vector_data = self.db_client.search(query=query, top_k=retrieve_k)

            return {
                "type": "hybrid",
                "graph": graph_result["data"] if graph_result else [],
                "vector": self.rerank(vector_data, query, top_k),
            }

    def close(self):
        self.graph_driver.close()
        logger.info("Neo4j Driver closed.")
