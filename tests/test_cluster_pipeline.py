# -*- coding: utf-8 -*-
"""cluster_pipeline 分片单测。"""
from __future__ import annotations
from services.cluster_pipeline import plan_batches


def test_plan_batches_chunks_when_no_clusters():
    ids = ["a%d" % i for i in range(10)]
    batches = plan_batches(ids, None, batch_size=3)
    assert all(len(b) <= 3 for b in batches)
    assert sorted(x for b in batches for x in b) == sorted(ids)


def test_plan_batches_respects_clusters_and_batch_size():
    ids = ["a", "b", "c", "x", "y", "z"]
    clusters = [["a", "b", "c", "x", "y", "z"]]  # 大簇
    batches = plan_batches(ids, clusters, batch_size=2)
    assert all(len(b) <= 2 for b in batches)
    assert sorted(x for b in batches for x in b) == sorted(ids)
    assert len(set(x for b in batches for x in b)) == len(ids)  # 无重复


def test_plan_batches_keeps_cluster_members_adjacent():
    ids = ["a", "b", "c", "d"]
    clusters = [["a", "b"], ["c", "d"]]
    batches = plan_batches(ids, clusters, batch_size=10)
    # 每个批应是某一个簇的完整成员
    assert set(batches[0]) == {"a", "b"}
    assert set(batches[1]) == {"c", "d"}


def test_plan_batches_only_includes_input_ids():
    ids = ["a", "b"]
    clusters = [["a", "b", "ghost"]]  # ghost 不在输入
    batches = plan_batches(ids, clusters, batch_size=10)
    assert sorted(x for b in batches for x in b) == ["a", "b"]


def test_plan_batches_empty():
    assert plan_batches([], None) == []
