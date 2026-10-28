import anyio
import pytest

from atlian_assiant.core.ratelimit import ConcurrencyLimiter, IssueWriteSerializer, RateLimitObserver
from atlian_assiant.core.retry import RetryPolicy


def test_retry_policy_rules():
    p = RetryPolicy(max_attempts=3)
    assert p.should_retry(429, idempotent=True, attempt=0) and p.should_retry(503, idempotent=True, attempt=1)
    assert not p.should_retry(503, idempotent=False, attempt=0) and p.should_retry(429, idempotent=False, attempt=0)
    assert not p.should_retry(404, idempotent=True, attempt=0) and not p.should_retry(429, idempotent=True, attempt=2)


def test_delay_prefers_retry_after_and_caps_backoff():
    p = RetryPolicy(base_delay=1, max_delay=10)
    assert 5 <= p.delay(0, 5) <= 6
    assert all(0.5 <= p.delay(0, None) <= 1.0 for _ in range(20))
    assert all(p.delay(20, None) <= 10 for _ in range(20))


def test_observer_parses_header_variants_case_insensitively():
    o = RateLimitObserver()
    st = o.observe("jira_platform", {"X-RateLimit-Remaining": "12", "X-RateLimit-Limit": "100", "RateLimit-Reason": "jira-quota-global-based",
                                      "Beta-Retry-After": "3"})
    assert (st.remaining, st.limit, st.reason, st.retry_after) == (12, 100, "jira-quota-global-based", 3.0)
    assert o.retry_after({"retry-after": "9"}) == 9.0 and o.retry_after({}) is None and o.retry_after({"Retry-After": "soon"}) is None


@pytest.mark.anyio
async def test_limiter_bounds_concurrency_per_family():
    lim = ConcurrencyLimiter({"jira": 2})
    active = peak = 0

    async def work():
        nonlocal active, peak
        async with lim.limit("jira"):
            active += 1
            peak = max(peak, active)
            await anyio.sleep(0.01)
            active -= 1

    async with anyio.create_task_group() as tg:
        for _ in range(8):
            tg.start_soon(work)
    assert peak == 2


@pytest.mark.anyio
async def test_serializer_orders_writes_to_the_same_issue_but_not_others():
    ser = IssueWriteSerializer()
    log: list[str] = []

    async def write(key: str, tag: str):
        async with ser.serialize(key):
            log.append(f"{tag}-start")
            await anyio.sleep(0.01)
            log.append(f"{tag}-end")

    async with anyio.create_task_group() as tg:
        tg.start_soon(write, "A-1", "a")
        tg.start_soon(write, "A-1", "b")
    assert log == ["a-start", "a-end", "b-start", "b-end"]
    log.clear()
    async with anyio.create_task_group() as tg:
        tg.start_soon(write, "A-1", "a")
        tg.start_soon(write, "B-2", "b")
    assert log[:2] == ["a-start", "b-start"]
    async with ser.serialize(None):
        pass
