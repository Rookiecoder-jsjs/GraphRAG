"""Unit tests for the BM25 service: tokenization and index lifecycle.

Covers the two fixes:
- Chinese text is segmented into real words via jieba (not lumped into a
  single contiguous-CJK token, which gutted keyword recall for Chinese).
- remove_from_index() is wired so a deleted document's chunks stop being
  returned by BM25 (previously never called -> deleted docs stayed
  searchable until restart).
"""
from app.services.bm25 import BM25Service


class TestChineseTokenization:
    def test_cjk_is_split_into_words_not_one_run(self):
        svc = BM25Service()
        tokens = svc._tokenize("知识图谱系统支持RAG问答")
        # jieba splits the Chinese into real words and keeps the latin word.
        assert "知识" in tokens
        assert "图谱" in tokens
        assert "rag" in tokens
        # The old regex behavior (whole run as one token) must not happen.
        assert "知识图谱系统支持rag问答" not in tokens

    def test_pure_punctuation_is_dropped(self):
        svc = BM25Service()
        assert svc._tokenize("，。！？") == []

    def test_single_char_tokens_dropped(self):
        svc = BM25Service()
        assert "的" not in svc._tokenize("我的文档")


class TestIndexLifecycle:
    # NOTE: rank_bm25's BM25Okapi gives a term appearing in exactly half the
    # corpus idf == 0 (log(1.5)-log(1.5)), so these tests use 3+ documents to
    # keep queries for a unique term on the positive-idf side of the score
    # filter (> 0).

    def test_search_finds_matching_content(self):
        svc = BM25Service()
        svc.build_user_index(
            1,
            ["知识图谱 系统", "另一个 文档", "机器学习 研究"],
            ["c1", "c2", "c3"],
        )
        ids = {h["id"] for h in svc.search("知识", 1, top_k=5)}
        assert "c1" in ids

    def test_remove_from_index_purges_deleted_docs(self):
        svc = BM25Service()
        svc.build_user_index(
            1,
            ["知识图谱 系统", "另一个 文档", "机器学习 研究", "量子 计算"],
            ["c1", "c2", "c3", "c4"],
        )
        assert "c1" in {h["id"] for h in svc.search("知识", 1, top_k=5)}

        svc.remove_from_index(1, {"c1"})
        # c1's content is gone from the index (no doc contains "知识" now).
        assert all(h["id"] != "c1" for h in svc.search("知识", 1, top_k=5))
        # The remaining docs are still findable (3 left, "文档" in 1/3).
        assert "c2" in {h["id"] for h in svc.search("文档", 1, top_k=5)}

    def test_add_to_index_appends_to_existing(self):
        svc = BM25Service()
        svc.build_user_index(1, ["知识图谱 系统", "机器学习 研究"], ["c1", "c3"])
        svc.add_to_index(1, ["另一个 文档", "量子 计算"], ["c2", "c4"])
        assert "c2" in {h["id"] for h in svc.search("文档", 1, top_k=5)}

    def test_per_user_isolation(self):
        svc = BM25Service()
        svc.build_user_index(
            1,
            ["知识图谱 系统", "数据科学 入门", "机器学习 研究"],
            ["u1c1", "u1c2", "u1c3"],
        )
        svc.build_user_index(
            2,
            ["另一个 文档", "旅行 攻略", "美食 推荐"],
            ["u2c1", "u2c2", "u2c3"],
        )
        assert {h["id"] for h in svc.search("知识", 1, top_k=5)} == {"u1c1"}
        # User 2's index has no "知识".
        assert svc.search("知识", 2, top_k=5) == []
