"""Utilities for composing chat prompts."""

from __future__ import annotations

from repo_agent.doc_meta_info import DocItem
from repo_agent.prompt import chat_template
from repo_agent.settings import Setting


class PromptBuilder:
    """Build chat messages for documentation generation."""

    def __init__(self, settings: Setting) -> None:
        self.settings = settings

    def _referenced_prompt(self, doc_item: DocItem) -> str:
        if not doc_item.reference_who:
            return ""
        prompt_parts = [
            "As you can see, the code calls the following objects, their code and docs are as following:"
        ]
        for reference_item in doc_item.reference_who:
            doc_text = reference_item.md_content[-1] if reference_item.md_content else "None"
            code_snippet = reference_item.content.get("code_content", "")
            instance_prompt = (
                f"obj: {reference_item.get_full_name()}\n"
                f"Document: \n{doc_text}\n"
                f"Raw code:```\n{code_snippet}\n```" + "=" * 10
            )
            prompt_parts.append(instance_prompt)
        return "\n".join(prompt_parts)

    def _referencer_prompt(self, doc_item: DocItem) -> str:
        if not doc_item.who_reference_me:
            return ""
        prompt_parts = [
            "Also, the code has been called by the following objects, their code and docs are as following:"
        ]
        for referencer_item in doc_item.who_reference_me:
            doc_text = referencer_item.md_content[-1] if referencer_item.md_content else "None"
            code_snippet = referencer_item.content.get("code_content", "None")
            instance_prompt = (
                f"obj: {referencer_item.get_full_name()}\n"
                f"Document: \n{doc_text}\n"
                f"Raw code:```\n{code_snippet}\n```" + "=" * 10
            )
            prompt_parts.append(instance_prompt)
        return "\n".join(prompt_parts)

    @staticmethod
    def _relationship_description(referencer_content: str, reference_letter: str) -> str:
        if referencer_content and reference_letter:
            return "And please include the reference relationship with its callers and callees in the project from a functional perspective"
        if referencer_content:
            return "And please include the relationship with its callers in the project from a functional perspective."
        if reference_letter:
            return "And please include the relationship with its callees in the project from a functional perspective."
        return ""

    def build_messages(self, doc_item: DocItem):
        """Compose chat messages for a given ``DocItem``."""
        code_info = doc_item.content
        is_referenced = len(doc_item.who_reference_me) > 0

        code_type = code_info["type"]
        code_name = code_info["name"]
        code_content = code_info["code_content"]
        have_return = code_info.get("have_return", False)
        file_path = doc_item.get_full_name()

        referencer_content = self._referencer_prompt(doc_item)
        reference_letter = self._referenced_prompt(doc_item)
        has_relationship = self._relationship_description(referencer_content, reference_letter)

        code_type_tell = "Class" if code_type == "ClassDef" else "Function"
        parameters_or_attribute = "attributes" if code_type == "ClassDef" else "parameters"
        have_return_tell = (
            "**Output Example**: Mock up a possible appearance of the code's return value."
            if have_return
            else ""
        )
        combine_ref_situation = (
            "and combine it with its calling situation in the project," if is_referenced else ""
        )

        project_structure_prefix = ", and the related hierarchical structure of this project is as follows (The current object is marked with an *):"

        return chat_template.format_messages(
            combine_ref_situation=combine_ref_situation,
            file_path=file_path,
            project_structure_prefix=project_structure_prefix,
            code_type_tell=code_type_tell,
            code_name=code_name,
            code_content=code_content,
            have_return_tell=have_return_tell,
            has_relationship=has_relationship,
            reference_letter=reference_letter,
            referencer_content=referencer_content,
            parameters_or_attribute=parameters_or_attribute,
            language=self.settings.project.language,
        )
