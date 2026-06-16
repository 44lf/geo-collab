import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--mysql", action="store_true", default=False, help="deprecated; tests use MySQL only"
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "mysql: mark test as requiring the disposable MySQL test database"
    )
    config.addinivalue_line(
        "markers", "live: hits a real external site; skipped unless explicitly enabled"
    )
    config.addinivalue_line(
        "markers", "load: heavy load/measurement harness; opt-in via GEO_RUN_LOAD_TESTS=1"
    )


def pytest_collection_modifyitems(config, items):
    import os

    has_db = bool(os.environ.get("GEO_TEST_DATABASE_URL"))
    run_load = os.environ.get("GEO_RUN_LOAD_TESTS") == "1"
    skip_mysql = pytest.mark.skip(reason="Set GEO_TEST_DATABASE_URL to run MySQL database tests")
    skip_load = pytest.mark.skip(reason="Set GEO_RUN_LOAD_TESTS=1 to run load/measurement tests")
    for item in items:
        if "mysql" in item.keywords and not has_db:
            item.add_marker(skip_mysql)
        if "load" in item.keywords and not run_load:
            item.add_marker(skip_load)


@pytest.fixture(scope="session")
def mysql_db(request):
    import os

    if not os.environ.get("GEO_TEST_DATABASE_URL"):
        pytest.skip("Set GEO_TEST_DATABASE_URL to run MySQL database tests")
    return None
