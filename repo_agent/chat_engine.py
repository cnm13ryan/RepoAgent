from llama_index.llms.ollama import Ollama

from repo_agent.chat_engine_base import BaseChatEngine


class ChatEngine(BaseChatEngine):
    """ChatEngine using the Ollama backend."""

    def __init__(self, project_manager):
        super().__init__(Ollama, project_manager)
