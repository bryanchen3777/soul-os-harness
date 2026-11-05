"""EI-1 contract data types. Pure stdlib only; no runtime side effects."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Optional


_ID_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"


class Purpose(str, Enum):
    INTERACTIVE = "interactive"
    UTILITY = "utility"


class Scope(str, Enum):
    OWNER_PRIVATE = "owner_private"
    NONE = "none"


class MemoryPolicy(str, Enum):
    UTILITY_NO_MEMORY = "utility_no_memory"
    EPHEMERAL = "ephemeral"
    INTERACTIVE_CANDIDATE = "interactive_candidate"


class Source(str, Enum):
    EXTERNAL_INSTRUCTION = "external_instruction"
    EXTERNAL_HISTORY = "external_history"
    CURRENT_USER = "current_user"


@dataclass(frozen=True)
class RouteBinding:
    soul_alias: str
    backend_alias: str
    agent_id: str
    public_model_id: str
    backend_ref: str
    context_window_tokens: int


@dataclass(frozen=True)
class ClientProfile:
    client_id: str
    principal_id: str
    purpose: Purpose
    allowed_routes: tuple[str, ...]
    scope: Scope
    allow_persistent_memory: bool


@dataclass(frozen=True)
class CredentialBinding:
    digest: str = field(repr=False)
    profile: ClientProfile = field(repr=True)


@dataclass(frozen=True)
class Registry:
    routes: tuple[RouteBinding, ...]
    clients: tuple[ClientProfile, ...]
    credentials: tuple[CredentialBinding, ...]
    owner_principal_id: str
    backends: tuple[str, ...]


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    name: Optional[str] = None


@dataclass(frozen=True)
class RequestInput:
    model: str
    messages: tuple[Message, ...]
    stream: bool = False
    temperature: float = 0.5
    max_tokens: int = 512
    n: int = 1


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    profile: ClientProfile
    route: RouteBinding
    conversation_id: Optional[str]
    branch_id: str
    turn_id: Optional[str]
    idempotency_key: Optional[str]


@dataclass(frozen=True)
class ContentSegment:
    role: str
    content: str
    name: Optional[str]
    source: Source
    memory_candidate: bool = False


@dataclass(frozen=True)
class GenerationLimits:
    temperature: float
    max_tokens: int
    context_window_tokens: int
    provider_attempts: int = 1
    tool_rounds: int = 0


@dataclass(frozen=True)
class AdmissionDecision:
    context: RequestContext
    segments: tuple[ContentSegment, ...]
    current_user_text: str
    memory_policy: MemoryPolicy
    generation_limits: GenerationLimits


@dataclass(frozen=True)
class AuthenticatedClient:
    profile: ClientProfile

    @property
    def client_id(self) -> str:
        return self.profile.client_id

    @property
    def principal_id(self) -> str:
        return self.profile.principal_id


class InterfaceError(Exception):
    def __init__(self, status: int, code: str, param: Optional[str], message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.param = param
        self.message = message


def is_valid_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    n = len(value)
    if n < 1 or n > 128:
        return False
    if value == "." or value == "..":
        return False
    for ch in value:
        if ch not in _ID_CHARS:
            return False
    return True


def is_valid_public_model_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    n = len(value)
    if n < 1 or n > 128:
        return False
    if value == "." or value == "..":
        return False
    if value.strip() == "":
        return False
    if contains_surrogate(value):
        return False
    for ch in value:
        o = ord(ch)
        if ch == "/" or ch == "\\" or ch == "\n" or ch == "\r" or ch == "\x00":
            return False
        if o < 32 or o == 127:
            return False
    return True


def contains_surrogate(value: str) -> bool:
    for ch in value:
        o = ord(ch)
        if 0xD800 <= o <= 0xDFFF:
            return True
    return False
