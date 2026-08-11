"""
LLM layer for the natural-language projection explanation feature.

A simple bounded tool-calling loop, not LangGraph -- there's no human-in-
-the-loop/interrupt need here, unlike the sibling cmir_agent project.
Azure OpenAI via LangChain (`langchain-openai`, `langchain-core` only --
not the full `langchain` meta-package), matching cmir_agent's house
style for the provider choice, but NOT its lack of retry/timeout wrapping
or its practice of splicing raw content into an undelimited prompt
string -- see providers/azure_openai.py and
.claude/skills/llm-agent-patterns/SKILL.md.

Layout:
  base.py                          -- StructuredChatClient Protocol, provider-agnostic
  providers/azure_openai.py        -- AzureChatOpenAI wrapped with retry/backoff/timeout
  prompts/explain_projection/v1.py -- PROMPT_VERSION + SYSTEM_PROMPT
  tools/explanation_tools.py       -- optional-tool JSON schemas + arg validation
  explanation_schema.py            -- ProjectionExplanationOutput (structured-output target)
"""
