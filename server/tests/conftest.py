import pytest


def pytest_addoption(parser):
    parser.addoption("--mysql", action="store_true", default=False, help="run MySQL integration tests")


def pytest_configure(config):
    config.addinivalue_line("markers", "mysql: mark test as requiring MySQL database")


@pytest.fixture(scope="session")
def mysql_db(request):
    if not request.config.getoption("--mysql"):
        pytest.skip("use --mysql to run MySQL tests")
    return None
