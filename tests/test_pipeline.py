from __future__ import annotations

from groundwork.cache import MemoryCache
from groundwork.sources.base import Document
from groundwork.sources.local import LocalSource

from .conftest import CORPUS, collect, docx_bytes


class StubSource:
    """In-memory source with a fixed result list and a download counter."""

    def __init__(self, docs: dict[str, tuple[Document, bytes]], visible: list[str]):
        self.docs, self.visible, self.fetches = docs, visible, []

    async def search(self, query, limit):
        return [self.docs[k][0] for k in self.visible][:limit]

    async def fetch(self, doc):
        self.fetches.append(doc.source_id)
        return next(data for d, data in self.docs.values() if d.source_id == doc.source_id)


EXPENSES = docx_bytes({"Travel claims": "Claims must be submitted within 30 days of the trip.\n\nClaims over 500 dollars need manager approval."})
SALARIES = docx_bytes({"Executive pay": "The executive bonus pool is 12 percent at target."})


def stub(visible, version="v1"):
    return StubSource({
        "exp": (Document("drive/exp", version, "Expenses.docx", "https://contoso.sharepoint.com/Expenses.docx", 0), EXPENSES),
        "sal": (Document("drive/sal", "v1", "Exec Pay.docx", "https://contoso.sharepoint.com/Exec%20Pay.docx", 1), SALARIES),
    }, visible)


async def test_streams_deltas_then_validated_citations(make_assistant):
    assistant, _ = make_assistant("Claims are due within 30 days [1]. Late claims need approval [1][7].")
    events = await collect(assistant.ask(LocalSource(CORPUS), "How long do I have to submit a travel claim?"))
    names = [n for n, _ in events]
    assert names[0] == "status" and names[-1] == "done"
    assert names.count("delta") > 5, "answer must stream, not arrive as one block"
    citations = dict(events)["citations"]
    assert [c["id"] for c in citations] == [1], "[7] was never sent to the model and must be dropped"
    assert citations[0]["title"] == "travel-and-expenses.md"
    assert citations[0]["section"]
    assert dict(events)["done"]["grounded"] is True


async def test_no_match_refuses_without_calling_model(make_assistant, settings):
    assistant, provider = make_assistant()
    events = await collect(assistant.ask(LocalSource(CORPUS), "zebra quokka narwhal"))
    assert provider.prompts == []
    assert ("delta", {"text": settings.refusal_text}) in events
    assert dict(events)["done"]["refused"] is True


async def test_uncited_answer_is_flagged_not_grounded(make_assistant):
    assistant, _ = make_assistant("Claims are due within 30 days.")
    events = await collect(assistant.ask(LocalSource(CORPUS), "travel claim deadline"))
    assert dict(events)["done"]["grounded"] is False


async def test_restricted_document_never_reaches_prompt(make_assistant):
    assistant, provider = make_assistant("x [1]")
    await collect(assistant.ask(LocalSource(CORPUS, user="someone"), "executive short-term incentive pool"))
    assert "12 percent" not in provider.last_prompt

    allowed, provider2 = make_assistant("x [1]")
    await collect(allowed.ask(LocalSource(CORPUS, user="hr-admin"), "executive short-term incentive pool"))
    assert "12 percent" in provider2.last_prompt


async def test_warm_cache_does_not_leak_between_users(make_assistant):
    cache = MemoryCache()
    a, _ = make_assistant("x [1]", cache=cache)
    await collect(a.ask(stub(["exp", "sal"]), "executive bonus pool"))
    assert cache.get("drive/sal@v1") is not None

    b, provider_b = make_assistant("x", cache=cache)
    await collect(b.ask(stub(["exp"]), "executive bonus pool"))
    assert "bonus pool" not in provider_b.last_prompt


async def test_edited_document_is_reread(make_assistant):
    cache = MemoryCache()
    a, _ = make_assistant(cache=cache)
    s1 = stub(["exp"]); await collect(a.ask(s1, "travel claims"))
    s2 = stub(["exp"]); await collect(a.ask(s2, "travel claims"))
    s3 = stub(["exp"], version="v2"); await collect(a.ask(s3, "travel claims"))
    assert s1.fetches == ["drive/exp"] and s2.fetches == [] and s3.fetches == ["drive/exp"]


async def test_follow_up_keeps_context_for_retrieval(make_assistant):
    assistant, provider = make_assistant("No, contractors are covered by their contract [1].")
    history = [
        {"role": "user", "content": "How many days of annual leave do I get?"},
        {"role": "assistant", "content": "20 days per year [1]."},
    ]
    await collect(assistant.ask(LocalSource(CORPUS), "What about contractors?", history))
    prompt = provider.last_prompt
    assert "leave-policy.md" in prompt
    assert provider.prompts[-1][0]["content"].startswith("How many days"), "history is passed to the model"


async def test_failed_download_skips_document_instead_of_failing(make_assistant):
    class Flaky(StubSource):
        async def fetch(self, doc):
            if doc.source_id == "drive/sal":
                raise RuntimeError("423 Locked")
            return await super().fetch(doc)

    src = stub(["exp", "sal"]); flaky = Flaky(src.docs, src.visible)
    assistant, _ = make_assistant()
    events = await collect(assistant.ask(flaky, "travel claims"))
    assert dict(events)["citations"][0]["title"] == "Expenses.docx"


async def test_one_document_cannot_crowd_out_the_others(make_assistant, settings):
    assistant, _ = make_assistant()
    found = await assistant.retrieve(LocalSource(CORPUS), "claims approval flights hotels meals mileage leave")
    per_doc = {}
    for c in found.chunks:
        per_doc[c.doc_key] = per_doc.get(c.doc_key, 0) + 1
    assert max(per_doc.values()) <= 3
