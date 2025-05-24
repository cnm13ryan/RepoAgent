from llama_index.llms.lmstudio import LMStudio

from repo_agent.doc_meta_info import DocItem
from repo_agent.log import logger
from repo_agent.settings import SettingsManager
from repo_agent.prompt_builder import PromptBuilder


class ChatEngine:
    """
    ChatEngine is used to generate the doc of functions or classes.
    """

    def __init__(self, project_manager):
        self.settings = SettingsManager.get_setting()

        self.llm = LMStudio(
            api_key=self.settings.chat_completion.openai_api_key.get_secret_value(),
            api_base=self.settings.chat_completion.openai_base_url,
            request_timeout=self.settings.chat_completion.request_timeout,
            model=self.settings.chat_completion.model,
            temperature=self.settings.chat_completion.temperature,
            max_retries=1,
            is_chat_model=True,
        )
        self.prompt_builder = PromptBuilder(self.settings)

    def build_prompt(self, doc_item: DocItem):
        """Return chat messages for ``doc_item``."""
        return self.prompt_builder.build_messages(doc_item)

    def generate_doc(self, doc_item: DocItem):
        """Generates documentation for a given DocItem."""
        messages = self.build_prompt(doc_item)

        # Begin LLM call
        try:
            logger.debug(f"Sending messages to LLM: {messages}")
            response = self.llm.chat(messages)

            # Check usage in response
            if hasattr(response.raw, 'usage') and isinstance(response.raw.usage, dict):
                logger.debug(f"LLM Prompt Tokens: {response.raw.usage.get('prompt_tokens')}")
                logger.debug(f"LLM Completion Tokens: {response.raw.usage.get('completion_tokens')}")
                logger.debug(f"Total LLM Token Count: {response.raw.usage.get('total_tokens')}")
            else:
                logger.debug("No usage information available in response.")

            # Log final content
            logger.debug(f"LLM Response: {response.message.content}")
            return response.message.content

        except Exception as e:
            logger.error(f"Error in llamaindex chat call: {e}")
            raise
