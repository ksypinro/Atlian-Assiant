import pytest

from atlian_assiant.core.cache import MetadataCache
from atlian_assiant.core.errors import AmbiguousReferenceError, NotFoundError
from atlian_assiant.core.resolvers import FieldResolver, SpaceResolver, UserResolver
from atlian_assiant.models.jira import Field
from atlian_assiant.testing import FakeBackend


@pytest.mark.anyio
async def test_field_resolver_maps_names_ids_and_system_fields():
    b = FakeBackend()
    r = FieldResolver(b.jira)
    assert await r.resolve("Story Points") == "customfield_10016" and await r.resolve("story points") == "customfield_10016"
    assert await r.resolve("customfield_10016") == "customfield_10016" and await r.resolve("summary") == "summary"
    assert await r.resolve("labels") == "labels"            # system id not in the list is still passed through
    assert await r.resolve_mapping({"Story Points": 5}) == {"customfield_10016": 5}
    assert (await r.names_by_id())["summary"] == "Summary"
    with pytest.raises(NotFoundError):
        await r.resolve("No Such Field")


@pytest.mark.anyio
async def test_field_resolver_ambiguity_lists_candidates_and_cache_is_used():
    b = FakeBackend()
    b.store.fields.append(Field(id="customfield_999", name="Story Points", custom=True))
    r = FieldResolver(b.jira)
    with pytest.raises(AmbiguousReferenceError) as exc:
        await r.resolve("Story Points")
    assert {c["id"] for c in exc.value.details["candidates"]} == {"customfield_10016", "customfield_999"}
    calls = {"n": 0}
    orig = b.jira.list_fields

    async def counting():
        calls["n"] += 1
        return await orig()

    b.jira.list_fields = counting          # type: ignore[method-assign]
    r2 = FieldResolver(b.jira)
    await r2.resolve("summary"); await r2.resolve("summary")
    assert calls["n"] == 1


@pytest.mark.anyio
async def test_user_resolver_email_name_account_id_ambiguity():
    r = UserResolver(FakeBackend().jira)
    assert await r.resolve("ann@example.com") == "acc-ann" and await r.resolve("Bob Ray") == "acc-bob"
    assert await r.resolve("5b10a2844c20165700ede21f") == "5b10a2844c20165700ede21f"
    assert await r.resolve("ann@example.com") == "acc-ann"          # second call is cached
    with pytest.raises(AmbiguousReferenceError):
        await r.resolve("example.com")
    with pytest.raises(NotFoundError):
        await r.resolve("zzz")


@pytest.mark.anyio
async def test_space_resolver_key_and_id_both_ways():
    r = SpaceResolver(FakeBackend().confluence)
    assert await r.to_id("ENG") == "9001" and await r.to_id("9001") == "9001"
    assert await r.to_key("9001") == "ENG" and await r.to_key("ENG") == "ENG"


@pytest.mark.anyio
async def test_cache_ttl_and_invalidate():
    now = {"t": 0.0}
    c = MetadataCache(ttl=10, clock=lambda: now["t"])
    c.set(1, "cid", "jira", "fields")
    assert c.get("cid", "jira", "fields") == 1
    now["t"] = 11
    assert c.get("cid", "jira", "fields") is None
    c.set(1, "a", "x"); c.set(2, "a", "y"); c.set(3, "b", "x")
    assert c.invalidate("a") == 2 and c.get("b", "x") == 3
