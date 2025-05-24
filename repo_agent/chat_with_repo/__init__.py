"""Entry points for the ``chat_with_repo`` package."""


def main() -> None:
    """Run the interactive chat interface."""
    from .main import main as _main

    return _main()

__all__ = ["main"]
