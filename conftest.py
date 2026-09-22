"""Keep normal unit tests independent of a local Ollama server."""


def pytest_addoption(parser):
    parser.addoption(
        "--run-ollama", action="store_true", default=False,
        help="Run the six Day 4 live persona checks against local Ollama",
    )
