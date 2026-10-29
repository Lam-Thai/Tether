"""Slice 14 end to end on real Postgres: the owner associates a number (`/phone-numbers`,
fake Twilio numbers API), then signed Twilio webhooks (`/voice`, `/input`, `/input/wait`,
`/status`) drive a call through `CallSessionService` -> the shared `ConversationEngine`
(fake model) -> booking on the fake Google Calendar -> TwiML; the call lands in the inbox
with its transcript, booking and summary. Plus replays, bad signatures, unknown numbers,
quota exhaustion, the duration cap, silence, and slow turns (hold, resume, takeover).
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlparse
from xml.etree import ElementTree

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select, update

from core.booking_rules import default_booking_rules
from core.calendar import get_clock
from core.config import settings
from core.db import async_session_factory
from core.llm import get_llm_client
from core.voice import (
    INPUT_PATH,
    STATUS_PATH,
    VOICE_PATH,
    WAIT_PATH,
    get_numbers_api,
    get_voice_settings,
)
from integrations.llm.fake import FakeLLMClient, agent_turn, summary_reply
from integrations.llm.port import ModelReply, ModelRequest
from integrations.twilio.fake import FakeNumbersApi
from integrations.twilio.signature import compute_signature
from models.agent import Agent
from models.booking import Booking
from models.interaction import Interaction, InteractionMessage
from models.usage import UsageCounter
from models.voice_turn import VoiceTurn
from services import call_session
from services.booking_rules import BookingRules
from services.call_session import (
    BUSY_LINE,
    NO_INPUT_LINE,
    REPROMPT_LINE,
    UNKNOWN_NUMBER_LINE,
    VoiceSettings,
)
from services.usage import UsageKind
from tests.api.test_bookings import BOOKED, NOW, _book_call, _connect, _salon

TOKEN = "voice-test-token"
PUBLIC = "https://api.example.test"
CALLER = "+15557654321"
GREETING = "Thanks for calling Demo Salon, this is Ava. How can I help?"
HOURS = "We're open Monday to Friday, 9 AM to 5 PM."
SUMMARY = (
    "The caller asked about opening hours and booked a haircut. "
    "The haircut is booked for Friday at 3:30 PM."
)


def _auth(tenant) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.token}"}


def _call_sid() -> str:
    return f"CA{uuid.uuid4().hex}"


@pytest.fixture
def twilio_configured(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "AC" + "c" * 32)
    monkeypatch.setattr(settings, "twilio_auth_token", SecretStr(TOKEN))
    monkeypatch.setattr(settings, "twilio_from_number", "+15005550006")
    monkeypatch.setattr(settings, "api_public_base_url", PUBLIC)


@pytest.fixture
def numbers(app) -> FakeNumbersApi:
    fake = FakeNumbersApi(trial=True)
    app.dependency_overrides[get_numbers_api] = lambda: fake
    return fake


@pytest.fixture
def voice(app) -> dict[str, VoiceSettings]:
    """Mutable holder: tests swap in tighter timings or a shorter duration cap."""
    holder = {"settings": VoiceSettings(max_call_seconds=600, hold_after_seconds=5.0)}
    app.dependency_overrides[get_voice_settings] = lambda: holder["settings"]
    return holder


@pytest.fixture
async def client(app, twilio_configured, numbers, voice):
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    app.dependency_overrides[default_booking_rules] = lambda: BookingRules(15, 120, 60, 0)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _signed(client, path: str, form: dict[str, str], query: dict | None = None, token=TOKEN):
    suffix = f"?{urlencode(query)}" if query else ""
    signature = compute_signature(token, f"{PUBLIC}{path}{suffix}", form)
    return await client.post(
        f"{path}{suffix}",
        content=urlencode(form),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": signature,
        },
    )


class Twiml:
    def __init__(self, response) -> None:
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/xml")
        self.root = ElementTree.fromstring(response.content)

    @property
    def said(self) -> str:
        return " ".join(say.text or "" for say in self.root.iter("Say"))

    @property
    def hangs_up(self) -> bool:
        return self.root.find("Hangup") is not None

    @property
    def gather_query(self) -> dict[str, str] | None:
        gather = self.root.find("Gather")
        if gather is None:
            return None
        parsed = urlparse(gather.attrib["action"])
        assert f"{PUBLIC}{parsed.path}" == f"{PUBLIC}{INPUT_PATH}"
        return {k: v[0] for k, v in parse_qs(parsed.query).items()}

    @property
    def redirect_query(self) -> dict[str, str] | None:
        redirect = self.root.find("Redirect")
        if redirect is None or not redirect.text:
            return None
        parsed = urlparse(redirect.text)
        assert parsed.path == WAIT_PATH
        return {k: v[0] for k, v in parse_qs(parsed.query).items()}


async def _associate(
    client, tenant, numbers: FakeNumbersApi, *, activate: bool = True
) -> tuple[str, str]:
    """Puts a number on the fake account and associates it; returns (number id, E.164).
    Also activates the agent unless told not to (new agents start inactive)."""
    on_account = numbers.add_number()
    listed = await client.get("/api/v1/phone-numbers", headers=_auth(tenant))
    assert listed.status_code == 200, listed.text
    assert on_account.sid in [n["provider_number_sid"] for n in listed.json()["available"]]
    created = await client.post(
        "/api/v1/phone-numbers",
        json={"provider_number_sid": on_account.sid},
        headers=_auth(tenant),
    )
    assert created.status_code == 201, created.text
    if activate:
        await _activate_agent(client, tenant)
    return created.json()["id"], on_account.phone_number


async def _activate_agent(client, tenant) -> None:
    """New agents start inactive and callers hear "not available" until the owner
    activates (`POST /agent/activate`, covered in `test_readiness_api.py`). These call
    tests are about the call itself, so they flip the flag directly; the inactive-agent
    check is exercised on purpose in `test_limits_end_calls_gracefully`."""
    assert (await client.get("/api/v1/agent", headers=_auth(tenant))).status_code == 200
    async with async_session_factory() as session:
        await session.execute(
            update(Agent).where(Agent.business_id == tenant.business_id).values(active=True)
        )
        await session.commit()


async def _greeting(client, tenant) -> None:
    patched = await client.patch(
        "/api/v1/agent", json={"greeting": GREETING}, headers=_auth(tenant)
    )
    assert patched.status_code == 200, patched.text


async def _start(client, to: str, sid: str) -> Twiml:
    return Twiml(await _signed(client, VOICE_PATH, {"CallSid": sid, "To": to, "From": CALLER}))


async def _say(client, sid: str, turn: int, speech: str | None, silences: int = 0) -> Twiml:
    form = {"CallSid": sid}
    if speech is not None:
        form["SpeechResult"] = speech
    query = {"turn": turn, **({"silences": silences} if silences else {})}
    return Twiml(await _signed(client, INPUT_PATH, form, query))


async def _interaction(sid: str) -> Interaction:
    async with async_session_factory() as session:
        row = (
            await session.execute(select(Interaction).where(Interaction.external_call_id == sid))
        ).scalar_one()
        return row


async def _message_count(interaction_id) -> int:
    async with async_session_factory() as session:
        return int(
            await session.scalar(
                select(func.count()).where(InteractionMessage.interaction_id == interaction_id)
            )
            or 0
        )


async def _counter(business_id, kind: UsageKind, day) -> int:
    async with async_session_factory() as session:
        value = await session.scalar(
            select(UsageCounter.count).where(
                UsageCounter.business_id == business_id,
                UsageCounter.kind == kind.value,
                UsageCounter.window_date == day,
            )
        )
        return int(value or 0)


async def test_the_demo_call_books_a_haircut_and_lands_in_the_inbox(
    client, entitled_tenants, calendar, llm, numbers, usage
):
    tenant, other = entitled_tenants
    number_id, to = await _associate(client, tenant, numbers)
    # Tether pointed the number's webhooks at itself and says so.
    configured = numbers.numbers[next(iter(numbers.numbers))]
    assert configured.voice_url == f"{PUBLIC}{VOICE_PATH}"
    assert configured.status_callback == f"{PUBLIC}{STATUS_PATH}"
    page = (await client.get("/api/v1/phone-numbers", headers=_auth(tenant))).json()
    assert page["configured"] is True and page["trial"] is True
    [mine] = page["numbers"]
    assert (mine["id"], mine["status"], mine["last_error"]) == (number_id, "connected", None)
    assert mine["verified_at"] is not None
    # The other business never sees it, neither as its own nor as available.
    theirs = (await client.get("/api/v1/phone-numbers", headers=_auth(other))).json()
    assert theirs["numbers"] == [] and theirs["available"] == []

    service_id = await _salon(client, tenant)
    refresh = await _connect(client, tenant, calendar)
    await _greeting(client, tenant)
    sid = _call_sid()

    greeting = await _start(client, to, sid)
    assert greeting.said == GREETING and greeting.gather_query == {"turn": "1"}

    llm.queue(agent_turn(HOURS, intent="BUSINESS_HOURS", sources=["business_hours"]))
    hours = await _say(client, sid, 1, "What are your hours?")
    assert hours.said == HOURS and hours.gather_query == {"turn": "2"} and not hours.hangs_up

    llm.queue(_book_call(), agent_turn(BOOKED, intent="BOOK_APPOINTMENT", sources=[service_id]))
    booked = await _say(client, sid, 2, "A haircut Friday at 3:30 please, I'm Alex")
    assert booked.said == BOOKED and booked.gather_query == {"turn": "3"}

    # Twilio retries the same webhook: same answer, no model call, nothing duplicated.
    interaction = await _interaction(sid)
    calls_before, messages_before = len(llm.requests), await _message_count(interaction.id)
    again = await _say(client, sid, 2, "A haircut Friday at 3:30 please, I'm Alex")
    assert again.said == BOOKED and again.gather_query == {"turn": "3"}
    assert len(llm.requests) == calls_before
    assert await _message_count(interaction.id) == messages_before
    async with async_session_factory() as session:
        [booking] = (
            await session.execute(select(Booking).where(Booking.business_id == tenant.business_id))
        ).scalars()
    assert booking.status == "confirmed"
    assert booking.customer_phone == CALLER  # caller ID is the booking phone
    assert len(calendar.api.events(refresh)) == 1

    llm.queue(agent_turn("You're welcome, have a great day!"))
    bye = await _say(client, sid, 3, "No, that's all, thanks. Bye!")
    assert bye.hangs_up and bye.gather_query is None

    llm.queue(summary_reply(SUMMARY))
    form = {"CallSid": sid, "CallStatus": "completed", "CallDuration": "95"}
    assert (await _signed(client, STATUS_PATH, form)).status_code == 204
    # A repeated callback changes nothing (the seconds are recorded once).
    assert (await _signed(client, STATUS_PATH, form)).status_code == 204

    detail = await client.get(f"/api/v1/interactions/{interaction.id}", headers=_auth(tenant))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert (body["channel"], body["status"], body["outcome"]) == ("voice", "completed", "booked")
    assert body["summary"] == SUMMARY and body["caller"] == CALLER
    messages = (
        await client.get(f"/api/v1/interactions/{interaction.id}/messages", headers=_auth(tenant))
    ).json()["items"]
    assert [m["text"] for m in messages][:3] == [GREETING, "What are your hours?", HOURS]
    assert len(messages) == 7  # greeting + 3 caller turns x 2
    listed = (await client.get("/api/v1/interactions", headers=_auth(tenant))).json()["items"]
    [row] = [item for item in listed if item["id"] == str(interaction.id)]
    assert row["caller"] == "•••" + CALLER[-4:]  # masked in the list
    assert (await client.get("/api/v1/interactions", headers=_auth(other))).json()["items"] == []

    day = usage.day.date()
    assert await _counter(tenant.business_id, UsageKind.CALL, day) == 1
    assert await _counter(tenant.business_id, UsageKind.CALL_SECONDS, day) == 95
    assert await _counter(tenant.business_id, UsageKind.TELEPHONY_API, day) >= 3

    # After the call ended, a late input webhook just says goodbye.
    late = await _say(client, sid, 4, "hello?")
    assert late.hangs_up


async def test_signatures_unknown_numbers_and_other_businesses(
    client, entitled_tenants, numbers, llm
):
    tenant, other = entitled_tenants
    _, to = await _associate(client, tenant, numbers)
    sid = _call_sid()
    form = {"CallSid": sid, "To": to, "From": CALLER}

    # A bad signature is refused before anything runs.
    for path, query in ((VOICE_PATH, None), (INPUT_PATH, {"turn": 1}), (STATUS_PATH, None)):
        bad = await _signed(client, path, form, query, token="not-the-token")
        assert bad.status_code == 403
        assert bad.json()["error"]["code"] == "invalid_signature"
    unsigned = await client.post(
        VOICE_PATH,
        content=urlencode(form),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert unsigned.status_code == 403

    # A number nobody associated: a polite line, a hang-up, and no interaction anywhere.
    unknown = await _start(client, "+15550000000", sid)
    assert unknown.said == UNKNOWN_NUMBER_LINE and unknown.hangs_up
    async with async_session_factory() as session:
        assert (
            await session.scalar(select(func.count()).where(Interaction.external_call_id == sid))
        ) == 0

    # The other business can't take or even see the number.
    [held] = numbers.numbers.values()
    taken = await client.post(
        "/api/v1/phone-numbers",
        json={"provider_number_sid": held.sid},
        headers=_auth(other),
    )
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "phone_number_in_use"
    mine = (await client.get("/api/v1/phone-numbers", headers=_auth(tenant))).json()["numbers"]
    number_id = mine[0]["id"]
    for method, path in (
        ("post", f"/api/v1/phone-numbers/{number_id}/test"),
        ("delete", f"/api/v1/phone-numbers/{number_id}"),
    ):
        response = await getattr(client, method)(path, headers=_auth(other))
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "phone_number_not_found"

    # A call to the number belongs to its business only.
    llm.queue(agent_turn("Happy to help."))
    await _start(client, to, sid)
    await _say(client, sid, 1, "Hi there")
    assert (await _interaction(sid)).business_id == tenant.business_id
    assert (await client.get("/api/v1/interactions", headers=_auth(other))).json()["items"] == []

    # Disconnect clears the webhooks; later calls to the number get the polite line.
    gone = await client.delete(f"/api/v1/phone-numbers/{number_id}", headers=_auth(tenant))
    assert gone.status_code == 200 and gone.json() == {"webhooks_cleared": True}
    assert held.sid in numbers.numbers and numbers.numbers[held.sid].voice_url is None
    after = await _start(client, to, _call_sid())
    assert after.said == UNKNOWN_NUMBER_LINE and after.hangs_up
    # The finished call keeps its transcript; only the link to the number is dropped.
    assert (await _interaction(sid)).phone_number_id is None


async def test_limits_end_calls_gracefully(client, entitled_tenants, numbers, llm, usage, voice):
    tenant, _ = entitled_tenants
    _, to = await _associate(client, tenant, numbers)

    # Gemini quota runs out mid-call: the fixed fallback with the business's contacts,
    # then a clean hang-up. One model call is allowed today.
    usage.set_daily(UsageKind.GEMINI_REQUEST, per_day=1_000_000, per_business_per_day=1)
    sid = _call_sid()
    await _start(client, to, sid)
    llm.queue(agent_turn("Happy to help."))
    assert not (await _say(client, sid, 1, "Hi")).hangs_up
    out = await _say(client, sid, 2, "Can I book a haircut?")
    assert out.hangs_up and "can't help with that right now" in out.said
    assert out.said.endswith("Goodbye.")

    # A call longer than the cap ends with the time-limit line.
    long_call = _call_sid()
    await _start(client, to, long_call)
    async with async_session_factory() as session:
        await session.execute(
            update(Interaction)
            .where(Interaction.external_call_id == long_call)
            .values(started_at=datetime.now(UTC) - timedelta(seconds=700))
        )
        await session.commit()
    capped = await _say(client, long_call, 1, "Still there?")
    assert capped.hangs_up and "time limit" in capped.said

    # Silence: a reprompt, then a goodbye.
    quiet = _call_sid()
    await _start(client, to, quiet)
    first = await _say(client, quiet, 1, None)
    assert first.said == REPROMPT_LINE and first.gather_query == {"turn": "1", "silences": "1"}
    second = await _say(client, quiet, 1, "", silences=1)
    assert second.said == NO_INPUT_LINE and second.hangs_up

    # The daily call cap: a new call is refused politely; replaying the voice webhook of
    # a call already started is not a second call.
    usage.set_daily(UsageKind.CALL, per_day=1_000_000, per_business_per_day=3)
    refused = await _start(client, to, _call_sid())
    assert refused.hangs_up and "can't help with that right now" in refused.said
    replayed = await _start(client, to, quiet)
    assert not replayed.hangs_up and replayed.gather_query == {"turn": "1"}
    assert await _counter(tenant.business_id, UsageKind.CALL, usage.day.date()) == 3

    # Too many calls at once.
    usage.set_daily(UsageKind.CALL, per_day=1_000_000, per_business_per_day=1_000_000)
    usage.set_concurrency(total=1_000_000, per_business=0)
    busy = await _start(client, to, _call_sid())
    assert busy.said == BUSY_LINE and busy.hangs_up

    # The agent is inactive (what a new agent is until activated): a polite line, no
    # interaction.
    usage.set_concurrency(total=1_000_000, per_business=1_000_000)
    async with async_session_factory() as session:
        await session.execute(
            update(Agent).where(Agent.business_id == tenant.business_id).values(active=False)
        )
        await session.commit()
    inactive_sid = _call_sid()
    inactive = await _start(client, to, inactive_sid)
    assert inactive.hangs_up and "can't take calls on this line" in inactive.said
    async with async_session_factory() as session:
        assert not await session.scalar(
            select(func.count()).where(Interaction.external_call_id == inactive_sid)
        )


class SlowLLM(FakeLLMClient):
    """The fake model, but every call takes `delay` seconds (a slow Gemini)."""

    delay: float = 0.0

    async def generate(self, request: ModelRequest) -> ModelReply:
        await asyncio.sleep(self.delay)
        return await super().generate(request)


async def test_a_slow_turn_holds_then_resumes_and_a_dead_worker_is_taken_over(
    app, client, entitled_tenants, numbers, voice
):
    tenant, _ = entitled_tenants
    slow = SlowLLM()
    slow.delay = 0.8
    app.dependency_overrides[get_llm_client] = lambda: slow
    voice["settings"] = VoiceSettings(
        max_call_seconds=600,
        hold_after_seconds=0.2,
        wait_seconds=3.0,
        heartbeat_seconds=0.1,
        stale_after_seconds=0.5,
        poll_seconds=0.05,
    )
    _, to = await _associate(client, tenant, numbers)
    sid = _call_sid()
    await _start(client, to, sid)

    slow.queue(agent_turn("We do kids' cuts too."))
    held = await _say(client, sid, 1, "Do you cut kids' hair?")
    assert (
        held.redirect_query == {"turn": "1", "attempt": "1"} and held.said == "One moment, please."
    )
    resumed = Twiml(await _signed(client, WAIT_PATH, {"CallSid": sid}, {"turn": 1, "attempt": 1}))
    assert resumed.said == "We do kids' cuts too." and resumed.gather_query == {"turn": "2"}

    # A worker that died after its webhook answered (a frozen serverless function): its
    # claim stops heartbeating, and the wait webhook takes the turn over and answers it.
    interaction = await _interaction(sid)
    async with async_session_factory() as session:
        session.add(
            VoiceTurn(
                id=uuid.uuid4(),
                business_id=tenant.business_id,
                interaction_id=interaction.id,
                turn=2,
                utterance="Are you open Saturday?",
                status="pending",
                claim=uuid.uuid4(),
                heartbeat_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        await session.commit()
    slow.delay = 0.0
    slow.queue(agent_turn("Sorry, we're closed on Saturdays."))
    taken = Twiml(await _signed(client, WAIT_PATH, {"CallSid": sid}, {"turn": 2, "attempt": 1}))
    assert taken.said == "Sorry, we're closed on Saturdays." and taken.gather_query == {"turn": "3"}
    assert await _message_count(interaction.id) == 5

    # A turn that never finishes: holds, then an apology with contacts and a hang-up.
    slow.delay = 30.0
    slow.queue(agent_turn("never heard"))
    stuck = await _say(client, sid, 3, "Can I bring my dog?")
    assert stuck.redirect_query == {"turn": "3", "attempt": "1"}
    last = voice["settings"].max_holds
    final = Twiml(await _signed(client, WAIT_PATH, {"CallSid": sid}, {"turn": 3, "attempt": last}))
    assert final.hangs_up and "taking longer than expected" in final.said
    # Stop the stuck worker so it doesn't outlive the test's event loop.
    for task in list(call_session._RUNNING):
        task.cancel()
    await asyncio.gather(*call_session._RUNNING, return_exceptions=True)
