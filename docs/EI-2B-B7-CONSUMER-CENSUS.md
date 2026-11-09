# EI-2B B7 Event Consumer Census

Baseline: canonical `origin/main` `0c4137cce03ce8154e4bad6c6b6bcc84c2b51cb1`

Audit mode: source-only, read-only. No runtime, service, database or LLM started.

## Observed event flow

The current SoulEventBus is broadcast/subscription based. The user-message path can reach consciousness, memory middleware, heartbeat activity tracking, and token arbitration. The generated AGENT_SPEAK path reaches multiple independent consumers; a request/response adapter cannot assume it owns that event. AGENT_AUDIO_READY has its own outbound consumers. SESSION_END can create qualification/carryover effects and must preserve lineage too.

## Static subscriber inventory

| Event | Subscriber | Source | Observed consequence | B7 rule for an external private request |
| --- | --- | --- | --- | --- |
| USER_MESSAGE | per-agent consciousness | `src/agent/consciousness.py::register` | calls agent message flow and can initiate AGENT_INTENT | Permit only with trusted principal/agent/conversation context; do not derive identity from client payload |
| USER_MESSAGE | memory middleware | `src/memory/middleware.py::register` | increments/observes interaction state and provides memory context | Only interactive candidate under admission policy; utility/ephemeral must not create persistent or presence effects |
| USER_MESSAGE | Heartbeat activity tracker | `src/heartbeat/engine.py::start` | updates last user activity | External request must not count as live owner presence without qualified interactive context |
| AGENT_INTENT_PERCEIVED (production) / AGENT_INTENT_ENRICHED (legacy/test fallback) / AGENT_SPEAK | speaker token manager | `src/eventbus/token_manager.py::register` | arbitration/token grant for intent; releases current speaker on speak | Token ownership must bind to one trusted request context and be released only by that context |
| SPEAKER_TOKEN_GRANTED | LLMProxy | `src/llm/proxy.py::register` | runs full generation pipeline; publishes AGENT_SPEAK or stub | Must use request-local route/model and explicitly relay context; no global model mutation |
| AGENT_SPEAK | per-agent consciousness | `src/agent/consciousness.py::handle_event` | clears pending, may trigger other-agent followup | A private external response cannot clear unrelated pending or trigger a different agent; late/stub events require exact context match |
| AGENT_SPEAK | token manager | `src/eventbus/token_manager.py::register` | releases speaker token | Release only the token owned by the same request; event alone is insufficient evidence |
| AGENT_SPEAK | memory middleware | `src/memory/middleware.py::handle_event` | memory outcome/experience ingestion, cooldown and counters | Gate by trusted source/context and existing memory acceptance; record pending/skipped/completed honestly |
| AGENT_SPEAK | Gateway | `src/io/gateway.py::register` subscriber `io_gateway` | WebSocket output | Suppress external-request event; no cross-channel broadcast |
| AGENT_AUDIO_READY | Gateway | `src/io/gateway.py::register` subscriber `io_gateway_audio` | WebSocket audio output | Suppress all external-request audio |
| AGENT_SPEAK | ChannelRouter | `src/io/channels/router.py::start` subscriber `channel_router` | channel routing/outbox/fallback | Suppress external-request event; no fallback or private-to-public delivery |
| AGENT_SPEAK | FishTTSHandler | `src/llm/fish_tts_handler.py::register` | may schedule async speech synthesis | Suppress independent of `tts_enabled`; never treat scheduled work as cancelled after start |
| AGENT_SPEAK | Heartbeat silence tracker | `src/heartbeat/engine.py::start` | updates last agent speak/silence clock | Utility/replay/stub must not signal presence or reset silence |
| AGENT_SPEAK | SocialWorldEventProducer | `scripts/run_server.py` wiring, handler in `src/social/producer.py` | may publish SOCIAL_WORLD_EVENT subject to gate | Private external turn must not become public/social world event |
| AGENT_AUDIO_READY | ChannelRouter | `src/io/channels/router.py::start` subscriber `channel_router_audio` | channel audio output and recipient routing | Suppress; no “most recent user” or fallback recipient association |
| SESSION_END | conversation qualifier | `src/conversation_qualification/qualifier.py::register` | may qualify/promote a conversation | Carry trusted conversation lineage; background/utility/imported history must not qualify as lived interaction |
| SESSION_END | per-agent consciousness | `src/agent/consciousness.py::register` | inner-life carryover | Same lineage and context checks; an external session close cannot close another session |

LLMProxy subscribes to SPEAKER_TOKEN_GRANTED, not AGENT_SPEAK. Current census found no direct AGENT_SPEAK subscription in LLMProxy. `src/voice/input_router.py` and `src/voice/tts_service.py` publish events; they are producers, not subscribers. WorldPerception consumes WORLD_EVENT / SOCIAL_WORLD_EVENT and AGENT_INTENT_ENRICHED, so the social producer edge is the route by which a speak event can enter world perception. A USER_MESSAGE registration shown only in the SoulEventBus docstring is an example, not an actual subscriber, and is excluded.

## What this audit proves and does not prove

This is a static census of subscriptions and source-level consequences on the pinned revision. It does not prove runtime wiring cardinality for every server configuration, wrapper installation order, event payload preservation through every hop, or B7 isolation. The token manager uses a configurable intake set and always adds AGENT_SPEAK; feature flags control social producer and FishTTS registration. Run-server composition and tests must cover enabled/disabled combinations. There may be indirect subscribers hidden behind helper/factory registration; AST census must be added before claiming the inventory is exhaustive.

## Required EI-2B implementation gates

1. Introduce an explicit in-process trusted context registry/handle keyed by server-generated event/request identity. Client payload cannot mint, overwrite, or select handles. Bind request, principal, agent, scope, conversation, branch, turn, model route and journal generation.
2. Track interface-origin event IDs before publication. If an interface event loses its context at any hop, classify it as interface-origin-with-missing-context and fail closed; do not misclassify as legacy.
3. Install a B7 policy at every outbound/side-effect consumer before it acts. A missing/expired/mismatched context never falls through to legacy delivery. Preserve legacy behavior only for events positively identified as legacy.
4. The private HTTP result sink accepts only the exact context/request/turn and a valid non-stub response. Cancellation/timeout returns an explicit uncertain/error result; late replies cannot complete a later request.
5. Add AST census guard for subscribe/wiring changes and behavior tests using fake bus/runtime for every table row. Negative controls must prove no Gateway/channel/TTS/social/world leak, no cross-agent followup, no unrelated token/pending release, no heartbeat/presence mutation, and no false memory completion.
6. Keep Frozen event contracts, Agency stages, SAGE writers, world middleware, Journal state/epoch/idempotency and production configuration untouched. No HTTP listener, service, real model or production data in this ticket.

## Stop gates

Any existing consumer whose behavior cannot be fenced without changing a Frozen Contract requires a design stop and Owner escalation. B7 remains OPEN until source census, runtime wiring census, exact-context positive tests, missing-context fail-closed tests, and all side-effect negative tests pass.
