"""`search_faq`: the tool that brings the owner's FAQs to the model.

Tool-only retrieval: FAQs reach the model only through this tool, never
pre-loaded into the prompt. The executor asks `KnowledgeBase.relevant` (only
active FAQs, only this tenant's, ranked) and hands back what it really found:
the snippets go to the prompt's FAQ RESULTS block and become the only ids
the grounding guard accepts as `sources` this turn. An empty result is said
out loud ("no matching FAQs") so the model has nothing to fill in.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from services.knowledge import MAX_QUERY_CHARS, KnowledgeBase
from services.tools import Tool, ToolContext, ToolResult

SEARCH_FAQ = "search_faq"


class SearchFaqArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUERY_CHARS),
        Field(description="The customer's question, in a few words."),
    ]


def search_faq_tool(knowledge: KnowledgeBase) -> Tool[SearchFaqArgs]:
    async def run(ctx: ToolContext, args: SearchFaqArgs) -> ToolResult:
        found = await knowledge.relevant(ctx.tenant, args.query)
        ids = [str(faq.id) for faq in found]
        for_model = (
            {"matches": len(found), "faq_ids": ids}
            if found
            else {"matches": 0, "result": "no matching FAQs"}
        )
        return ToolResult(
            for_model=for_model,
            for_log={"faq_ids": ids, "count": len(found)},
            faqs=tuple(found),
        )

    return Tool(
        name=SEARCH_FAQ,
        description=(
            "Search this business's FAQs for answers to the customer's question. "
            "Returns the matching FAQs with their ids."
        ),
        args=SearchFaqArgs,
        run=run,
    )
