from src.api.classify import QuestionIntent
from src.api.copilot import CopilotService
from src.api.filters import filters_from_params
from src.api.rag import compose_rag_answer, query_terms, retrieve_quotes
from src.api.research_questions import detect_research_question
from src.cluster.keyword_themes import run_keyword_themes, run_local_index
from src.config import load_settings
from src.db.memory import MemoryRepository
from src.extract.heuristic import extract_payload
from tests.test_eval_phase5 import _settings, seed_serving_corpus


def test_heuristic_extract_tags_fit_and_wishlist():
    fit = extract_payload("This kurta runs small and the size chart is useless.")
    assert "fit_uncertainty" in fit.friction_values()
    wish = extract_payload("I added it to my wishlist to save for later, not buying now.")
    assert wish.intent_mode.value == "passive_bookmark"


def test_local_index_populates_themes_and_copilot_rag(tmp_path):
    repo = MemoryRepository()
    settings = _settings(tmp_path)
    seed_serving_corpus(repo, settings)
    result = run_local_index(repo, settings)
    assert result["themes"] >= 1
    assert result["assigned"] >= 1
    assert repo.latest_cluster_run(success_only=True) is not None
    assert repo.list_theme_metrics(slice_kind="global", published_only=True)
    rows = retrieve_quotes(repo, "Why do users add items to a Myntra wishlist?", limit=5)
    assert rows
    pack = {
        "overview": {"eligible_corpus_count": 3, "counts_by_source": [], "intent_mode_counts": {}},
        "themes": {"themes": [{"name": "Fit and size uncertainty", "mention_count": 2, "share_of_voice": 0.4}]},
        "retrieval_rows": rows,
    }
    answer = compose_rag_answer(
        "Why do users add items to a Myntra wishlist?",
        pack,
        intent=QuestionIntent.qualitative,
    )
    assert "Eligible corpus count is" not in answer
    assert "mention_count" not in answer
    assert "share_of_voice" not in answer
    assert "opportunity area" not in answer.lower()
    assert "price drop" in answer.split("\n", 1)[0].lower()
    assert "wishlist" in answer.lower()
    assert len(answer) < 900
    assert query_terms("fit uncertainty")


def test_research_questions_map_and_answer_each_qid():
    prompts = {
        "Q1": "Why do users add fashion products to their wishlist?",
        "Q2": "What prevents wishlisted products from eventually being purchased?",
        "Q3": "What uncertainties remain after users have identified a product they like?",
        "Q2b": "What causes users to postpone a purchase?",
        "Q4": "How do users compare multiple shortlisted products?",
        "Q5": "What information do users seek outside Myntra/AJIO before purchasing?",
        "Q6": "What role do fit, size, styling, price, reviews, occasion and social validation play?",
        "Q7": "When do users use the wishlist as genuine purchase intent versus simply as a bookmarking mechanism?",
        "Q8": "How do these behaviors differ across user segments?",
        "Q9": "What unmet needs emerge consistently across user conversations?",
    }
    assert detect_research_question(prompts["Q1"]) == "Q1"
    assert detect_research_question(prompts["Q2"]) == "Q2"
    assert detect_research_question(prompts["Q2b"]) == "Q2"
    assert detect_research_question(prompts["Q3"]) == "Q3"
    assert detect_research_question(prompts["Q4"]) == "Q4"
    assert detect_research_question(prompts["Q5"]) == "Q5"
    assert detect_research_question(prompts["Q6"]) == "Q6"
    assert detect_research_question(prompts["Q7"]) == "Q7"
    assert detect_research_question(prompts["Q8"]) == "Q8"
    assert detect_research_question(prompts["Q9"]) == "Q9"

    pack = {
        "overview": {
            "eligible_corpus_count": 12,
            "counts_by_source": [{"source_type": "reddit", "eligible_count": 4}],
            "intent_mode_counts": {"passive_bookmark": 5, "near_term_purchase": 7},
        },
        "themes": {
            "themes": [
                {
                    "name": "Fit and size uncertainty",
                    "mention_count": 6,
                    "share_of_voice": 0.5,
                    "source_diversity": 3,
                },
                {
                    "name": "Wishlist as bookmark / save for later",
                    "mention_count": 4,
                    "share_of_voice": 0.3,
                    "source_diversity": 2,
                },
            ]
        },
        "segments": {
            "cells": [
                {"segment": "ethnic", "mention_count": 4, "small_n": False},
                {"segment": "footwear", "mention_count": 2, "small_n": False},
            ]
        },
        "retrieval_rows": [
            {
                "quote": "I added the kurta to my wishlist to wait for a better size and a sale.",
                "document_id": "11111111-1111-1111-1111-111111111111",
            },
            {
                "quote": "Not buying yet — parked it on the wishlist until I decide.",
                "document_id": "22222222-2222-2222-2222-222222222222",
            },
        ],
    }
    q1 = compose_rag_answer(prompts["Q1"], pack, intent=QuestionIntent.qualitative)
    assert "wishlist" in q1.lower()
    assert "price drop" in q1.split("\n", 1)[0].lower()
    assert "I added the kurta" in q1
    assert "parked it on the wishlist" in q1
    assert "mention_count" not in q1
    assert "share_of_voice" not in q1
    assert "intent mix" not in q1.lower()
    assert "opportunity area" not in q1.lower()
    assert "eligible corpus" not in q1.lower()
    assert len(q1) < 900
    q2 = compose_rag_answer(prompts["Q2"], pack, intent=QuestionIntent.qualitative)
    assert "fit" in q2.lower() or "wishlist" in q2.lower()
    q3 = compose_rag_answer(prompts["Q3"], pack, intent=QuestionIntent.qualitative)
    assert "fit" in q3.lower()
    q5 = compose_rag_answer(prompts["Q5"], pack, intent=QuestionIntent.qualitative)
    assert "outside" in q5.lower()
    q7 = compose_rag_answer(prompts["Q7"], pack, intent=QuestionIntent.qualitative)
    assert "bookmark" in q7.lower()
    q8 = compose_rag_answer(prompts["Q8"], pack, intent=QuestionIntent.comparative)
    assert "ethnic" in q8.lower() or "footwear" in q8.lower()
    q9 = compose_rag_answer(prompts["Q9"], pack, intent=QuestionIntent.qualitative)
    assert "unmet" in q9.lower() or "fit" in q9.lower()


def test_copilot_q1_stops_at_claim_with_two_reviews(tmp_path):
    repo = MemoryRepository()
    settings = _settings(tmp_path)
    seed_serving_corpus(repo, settings)
    service = CopilotService(repo, settings)
    turn = service.query_turn(
        "Why do users add fashion products to their wishlist?",
        filters_from_params(),
    )
    answer = turn["answer"] or ""
    assert turn["status"] == "ok"
    assert "price drop" in answer.split("\n", 1)[0].lower()
    assert "mention_count" not in answer
    assert "share_of_voice" not in answer
    assert "eligible corpus" not in answer.lower()
    assert "opportunity area" not in answer.lower()
    assert "intent mix" not in answer.lower()
    assert len(turn["citations"]) == 2
    for cite in turn["citations"]:
        blob = (cite.get("quote") or "").lower()
        assert cite["document_id"]
        assert blob
        assert any(token in blob for token in ("wishlist", "later", "sale", "coupon", "size"))
