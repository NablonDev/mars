from __future__ import annotations

from langchain_openai import AzureChatOpenAI

from cmir_agent.config import LLMConfig
from cmir_agent.domain.models import CMIR
from cmir_agent.interfaces.extractor import CMIRExtractor

_PROMPT_TEMPLATE = """
Read this CMIR email and extract the following fields:

- sender_type
- customer_identity
- material_identity
- intent_phrase
- existing_cmir_ref
- brand
- site
- target_grd_code
- target_customer_material_ref
- effective_date
- reason

Return only structured output. Do not infer a status or missing_fields list;
completeness is decided downstream. Leave any field you cannot find as an
empty string.

Email:

{body}
"""


class AzureOpenAICMIRExtractor(CMIRExtractor):
    def __init__(self, config: LLMConfig) -> None:
        self._llm = AzureChatOpenAI(
            azure_endpoint=config.endpoint,
            api_key=config.api_key,
            api_version=config.api_version,
            azure_deployment=config.deployment,
            temperature=config.temperature,
        ).with_structured_output(CMIR)

    def extract(self, body: str) -> CMIR:
        result = self._llm.invoke(_PROMPT_TEMPLATE.format(body=body))
        return result if isinstance(result, CMIR) else CMIR(**result)
