from src.normalize.category import infer_product_category
from src.normalize.language import detect_language
from src.normalize.pii import hash_author, scrub_pii
from src.normalize.relevance import classify_relevance
from src.normalize.spotcheck import spotcheck_text_failures


def test_play_store_weak_signal_kept():
    rel, reason = classify_relevance(
        "I have been using this for months and sometimes it hangs.",
        source_type="play_store",
    )
    assert rel == "inferred"
    assert reason is None


def test_app_store_weak_signal_kept():
    rel, reason = classify_relevance(
        "I have been using this for months and sometimes it hangs.",
        source_type="app_store",
    )
    assert rel == "inferred"
    assert reason is None


def test_reddit_weak_signal_rejected():
    rel, reason = classify_relevance(
        "I have been using this for months and sometimes it hangs.",
        source_type="reddit",
    )
    assert rel == "reject"


def test_youtube_competitor_video_rejected():
    rel, reason = classify_relevance(
        "Size is small, love this haul.",
        source_type="youtube",
        parent_context={"video_title": "Shein winter haul try-on"},
    )
    assert rel == "reject"
    assert reason == "off_topic"


def test_reddit_deleted_is_removed():
    rel, reason = classify_relevance("[deleted]", source_type="reddit")
    assert rel == "reject"
    assert reason == "removed"


def test_hash_author_stable():
    a = hash_author("Alice", "secret")
    b = hash_author("Alice", "secret")
    c = hash_author("Bob", "secret")
    assert a == b
    assert a != c
    assert a is not None
    assert len(a) == 64


def test_handle_scrub():
    out = scrub_pii("see u/someone and @myntrafan about sizing")
    assert "u/someone" not in out
    assert "@myntrafan" not in out
    assert "[HANDLE]" in out
    assert not spotcheck_text_failures(out)


def test_language_mixed_script():
    assert detect_language("size छोटा hai") == "hinglish"


def test_category_ethnic():
    assert infer_product_category("this lehenga was gorgeous") == "ethnic"
