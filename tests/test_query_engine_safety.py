"""Cypher safety.

These lock in the two independent defences added after the review:
parameterised templates, and a read-only guard on generated Cypher.
"""
import re

import pytest


class TestSanitizeCypher:
    @pytest.mark.parametrize("cypher", [
        "MATCH (n) DETACH DELETE n",
        "MATCH (n) DELETE n",
        "MATCH (n) SET n.x = 1",
        "MATCH (r:Repo {repo_id: 'x'}) CREATE (n:Pwn)",
        "MATCH (n) REMOVE n:x",
        "MATCH (n) MERGE (m:Pwn)",
        "CALL db.labels()",
        "CALL apoc.cypher.run('x')",
        "DROP INDEX foo",
        "LOAD CSV FROM 'file:///etc/passwd' AS line RETURN line",
        "GRANT MATCH ON GRAPH * TO nobody",
    ])
    def test_blocks_writes_and_procedures(self, query_engine, cypher):
        # The original property allow-list passed every one of these: none of
        # them contain an `n.prop` access for it to complain about.
        assert query_engine.sanitize_cypher(cypher) is None, cypher

    @pytest.mark.parametrize("cypher", [
        "MATCH (r:Repo {repo_id: 'a'}) RETURN r.repo_id",
        "MATCH (f:File {name: 'a.py'})-[:IMPORTS]->(g) RETURN g.path",
        "MATCH (n {name: 'delete everything'}) RETURN n",
        "MATCH (c:Class)-[:INHERITS_FROM]->(p) RETURN p.qualified_name",
    ])
    def test_allows_reads(self, query_engine, cypher):
        assert query_engine.sanitize_cypher(cypher) == cypher

    def test_literal_containing_a_write_keyword_is_not_a_false_positive(self, query_engine):
        """`'delete me'` is data, not a clause.

        The write-clause scan runs on the query with string literals blanked
        out, so a file legitimately named that is still queryable.
        """
        q = "MATCH (n {name: 'delete everything'}) RETURN n"
        assert query_engine.sanitize_cypher(q) is not None

    def test_blocks_unknown_properties(self, query_engine):
        assert query_engine.sanitize_cypher("MATCH (n) RETURN n.aws_secret") is None

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_blocks_empty(self, query_engine, bad):
        assert query_engine.sanitize_cypher(bad) is None


class TestParameterizeLiterals:
    def test_lifts_every_property_map_literal(self, query_engine):
        cypher, params = query_engine.parameterize_literals(
            "MATCH (r:Repo {repo_id: 'evil'}) MATCH (f:File {name: 'a.py'}) RETURN f.path"
        )
        assert "'evil'" not in cypher
        assert "'a.py'" not in cypher
        assert cypher.count("$") == 2
        assert sorted(params.values()) == ["a.py", "evil"]

    def test_multiword_values_become_parameters(self, query_engine):
        cypher, params = query_engine.parameterize_literals(
            "MATCH (f:File {name: 'src/my module.py'}) RETURN f"
        )
        assert "my module.py" not in cypher
        assert params == {"_lit_0": "src/my module.py"}

    def test_embedded_quote_never_reaches_execution(self, query_engine):
        """The defence is layered, and the order matters:

        1. sanitize_cypher() scans the *raw* text and refuses any write
           keyword, so a literal cannot smuggle one in.
        2. parameterize_literals() then lifts what remains.

        The regex cannot itself handle a value containing a quote — it is not
        meant to. Step 1 is what makes that irrelevant.
        """
        hostile = "x') DETACH DELETE n //"
        raw = f"MATCH (r:Repo {{repo_id: '{hostile}'}}) RETURN r"

        # Step 1: rejected outright, so step 2 is never reached.
        assert query_engine.sanitize_cypher(raw) is None

    def test_double_quoted_literals_are_left_alone_but_not_interpolated(self, query_engine):
        cypher, params = query_engine.parameterize_literals(
            'MATCH (f:File {name: "a.py"}) RETURN f'
        )
        assert '"a.py"' in cypher
        assert params == {}


class TestTemplatesAreParameterised:
    """A regression guard on the injection vector itself.

    `repo_id` is derived from a user-supplied URL. Before this change the
    templates interpolated it with str.format(), so:

        get_filename("https://github.com/a')-DETACH DELETE n//b")
          -> "a')-DETACH DELETE n-"
    """

    def test_no_format_placeholders_remain(self, query_engine):
        templates = query_engine._init_templates()
        assert templates, "expected templates"
        for name, cypher in templates.items():
            assert "{repo_id}" not in cypher, f"{name} interpolates repo_id"
            assert "{source}" not in cypher, f"{name} interpolates source"
            assert "{via}" not in cypher, f"{name} interpolates via"
            assert "{filename}" not in cypher, f"{name} interpolates filename"
            assert "{function_name}" not in cypher, f"{name} interpolates function_name"
            assert "{class_name}" not in cypher, f"{name} interpolates class_name"

    def test_architect_templates_are_parameterised(self, query_engine):
        for name, cypher in query_engine._init_architect_templates().items():
            assert "{repo_id}" not in cypher, f"{name} interpolates repo_id"
            assert "$repo_id" in cypher, f"{name} does not use the parameter"

    def test_every_template_scopes_to_a_repo(self, query_engine):
        for name, cypher in query_engine._init_templates().items():
            assert "$repo_id" in cypher, f"{name} is not scoped to a repo"


class TestMatcherExtraction:
    """`_match_template` regexes feed straight into query parameters.

    They must never be able to emit a Cypher fragment, and they must not be so
    loose that they hijack an unrelated question.
    """

    def test_extracted_names_are_always_python_filenames(self, query_engine):
        cases = [
            "which files does a.py depend on through b.py",
            "what does c.py import",
            "which files import d.py",
            "transitive dependencies of e.py",
            "what is in f.py",
        ]
        for q in cases:
            match = query_engine._match_template(q)
            assert match is not None, q
            _, params = match
            for key, value in params.items():
                if key == "repo_id":
                    continue
                assert '"' not in value and "'" not in value, (q, key, value)
                assert "\\" not in value, (q, key, value)

    def test_symbol_names_cannot_carry_cypher(self, query_engine):
        """`call`/`inherit` capture a bare word, not a filename — these values
        still become query parameters, so they must be inert too."""
        for q in ["what does g call", "what does MyClass inherit"]:
            match = query_engine._match_template(q)
            assert match is not None, q
            _, params = match
            for key, value in params.items():
                if key == "repo_id":
                    continue
                assert re.fullmatch(r"\w+", value), (q, key, value)

    def test_injection_text_does_not_become_a_template_match(self, query_engine):
        hostile = "what does a.py') DETACH DELETE n // import"
        match = query_engine._match_template(hostile)
        if match is not None:
            _, params = match
            for key, value in params.items():
                if key != "repo_id":
                    assert "DELETE" not in value.upper()
