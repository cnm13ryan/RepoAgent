from llama_index.llms.lmstudio import LMStudio

from repo_agent.chat_engine_base import BaseChatEngine


class ChatEngineLMStudio(BaseChatEngine):
    """ChatEngine using the LMStudio backend."""

    def __init__(self, project_manager):
        super().__init__(LMStudio, project_manager)



