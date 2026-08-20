from __future__ import annotations

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.core.config import LLMConfig
from app.schemas.cmir import CMIR

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

Example:
A line reading "Customer Material : ACME-CHOC-BAR" means
target_customer_material_ref = "ACME-CHOC-BAR" (the customer's own reference for the
material), not material_identity.

Email:

{body}
"""


class AzureOpenAICMIRExtractor:
    def __init__(self, config: LLMConfig) -> None:
        self._llm = ChatOpenAI(
            base_url=config.endpoint,
            api_key=SecretStr(config.api_key),
            model=config.deployment,
            temperature=config.temperature,
            timeout=config.timeout_seconds,
            max_retries=config.max_retries,
        ).with_structured_output(CMIR)

    def extract(self, body: str) -> CMIR:
        result = self._llm.invoke(_PROMPT_TEMPLATE.format(body=body))
        return result if isinstance(result, CMIR) else CMIR(**result)
