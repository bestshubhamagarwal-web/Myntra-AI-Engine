from src.cli import main


def test_ingest_rejects_unknown_source():
    assert main(["ingest", "tiktok"]) == 2
