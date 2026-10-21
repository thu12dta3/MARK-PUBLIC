import mark_core


def test_rust_ping():
    response = mark_core.rust_ping()
    assert response == "MARK Rust Core Online"
