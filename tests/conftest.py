"""Keep normal unit tests independent of a local Ollama server."""


def pytest_addoption(parser):
    parser.addoption(
        "--run-ollama", action="store_true", default=False,
        help="Enable live model checks against local Ollama",
    )
