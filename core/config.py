"""Settings for NetBandhu: one typed object, validated at import, every field defaulted.

    from core.config import settings
    settings.model_id            # "openrouter:openai/gpt-oss-20b"
    settings.FALLBACK            # "google_genai:gemini-3.1-flash-lite"

WHY A SETTINGS OBJECT (S03, S04)
    A model id or a path typed at a call site is a migration someone does by hand
    at 2am. Here every knob is a field with a default, read once, from `.env` or
    the environment, under the NETBANDHU_ prefix. Reading a missing key fails at
    startup instead of as a 404 an hour later.

PATHS
    Everything stateful lives under `.state/`, which is gitignored and rebuildable:
    the Qdrant folder, the checkpoint database, the memory store, the credit
    ledger. The corpus and the mock backend are committed, so the project runs on
    a fresh clone without any setup beyond `.env`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

# The providers' SDKs (ChatOpenRouter, ChatGroq, ChatGoogleGenerativeAI, the
# LangSmith client) read their keys from os.environ, NOT from this object. Loading
# the same file here means one .env serves both, and no feature has to remember
# which of the two places a key lives in. override=False: a real environment
# variable wins over the file, which is what a deploy wants.
load_dotenv(ROOT / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_prefix="NETBANDHU_",
        extra="ignore",
        case_sensitive=True,
    )

    APP_NAME: str = "NetBandhu"
    VERSION: str = "0.1.0"
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"

    # ── who serves the model ────────────────────────────────────────────────
    PROVIDER: str = "openrouter"
    MODEL: str = "openai/gpt-oss-20b"
    FALLBACK: str = "google_genai:gemini-3.1-flash-lite"
    """A whole provider string, tried when the primary still fails after its retries.

    Gemini, not groq: groq's gpt-oss-20b returned an EMPTY content string for a
    trivial prompt on this machine, and a fallback that answers nothing is worse
    than no fallback. See README, "measured, not assumed"."""

    EMBED_MODEL: str = "fastembed:BAAI/bge-small-en-v1.5"
    """The CPU model, on by default — and the reason is measured, not aesthetic.

    Going through Gemini's free tier for embeddings hit `429 RESOURCE_EXHAUSTED`
    on the very first ingest (limit: 100 embed requests per minute per project,
    and the corpus is 60+ chunks in one batch). Retrying on a 58-second timer in
    front of an audience is not a demo. fastembed needs no key and no network
    after the first download, and 384 dimensions are ample for four documents.

    Gemini is still available: set
        NETBANDHU_EMBED_MODEL=gemini-embedding-2
    and re-ingest. That writes a DIFFERENT collection, which is why the two can
    never be mixed by accident."""

    EMBED_DIM: int = 768
    """Only used when EMBED_MODEL is Gemini's; fastembed reports its own 384."""

    MAX_OUTPUT_TOKENS: int = Field(default=3500, ge=64, le=8192)
    """gpt-oss-20b is a reasoning model: its reasoning counts against this cap.

    At 2048 the eval saw two replies cut off mid-thought ("The late///<qualification:")
    because the model spent the budget thinking and had none left to answer. The
    symptom is a blank or mangled content field with finish_reason="length"."""
    REQUEST_TIMEOUT_S: float = Field(default=60.0, gt=0)
    MAX_RETRIES: int = Field(default=3, ge=1, le=8)
    TEMPERATURE: float = Field(default=0.0, ge=0.0, le=2.0)
    """Zero, because the eval is a measurement.

    Measured on this corpus and model, 22 Nov: at the provider's default the same
    three questions answered correctly in one run and refused in the next, and the
    scorecard moved with nothing but the seed. A number that changes when nothing
    changed is not a measurement."""
    REQUESTS_PER_SECOND: float = Field(default=1.0, gt=0)

    # ── keys: plain names, because the providers' SDKs read them ────────────
    # validation_alias is the provider's own name: the SDKs read OPENROUTER_API_KEY
    # and LANGSMITH_TRACING from the environment themselves, so a NETBANDHU_ prefix
    # here would leave both the SDK and this object looking at different variables.
    OPENROUTER_API_KEY: SecretStr = Field(default=SecretStr(""),
                                          validation_alias="OPENROUTER_API_KEY")
    GEMINI_API_KEY: SecretStr = Field(default=SecretStr(""),
                                      validation_alias="GEMINI_API_KEY")
    GROQ_API_KEY: SecretStr = Field(default=SecretStr(""),
                                    validation_alias="GROQ_API_KEY")

    LANGSMITH_TRACING: bool = Field(default=False, validation_alias="LANGSMITH_TRACING")
    LANGSMITH_PROJECT: str = Field(default="netbandhu",
                                   validation_alias="LANGSMITH_PROJECT")

    # ── retrieval ───────────────────────────────────────────────────────────
    CHUNK_SIZE: int = Field(default=900, ge=200, le=4000)
    CHUNK_OVERLAP: int = Field(default=120, ge=0)
    RAG_K: int = Field(default=4, ge=1, le=12)
    RAG_MAX_ATTEMPTS: int = Field(default=2, ge=1, le=4)
    """Searches per question in the corrective loop: the original, then at most
    one rewrite. Each extra attempt is a model call and a search."""

    QDRANT_PATH: Path = ROOT / ".state" / "qdrant"
    CHECKPOINT_DB: Path = ROOT / ".state" / "checkpoints.sqlite"
    MEMORY_DB: Path = ROOT / ".state" / "memory.sqlite"
    CREDIT_LEDGER: Path = ROOT / ".state" / "credits.jsonl"

    CORPUS_DIR: Path = ROOT / "corpus"
    DATA_FILE: Path = ROOT / "data" / "backend.yml"

    # ── the desks ───────────────────────────────────────────────────────────
    MAX_MODEL_CALLS_PER_RUN: int = Field(default=8, ge=2, le=30)
    MAX_TOOL_CALLS_PER_RUN: int = Field(default=12, ge=2, le=40)
    CREDIT_THRESHOLD_HOURS: float = 4.0
    """SLA §3.2: a credit is due only when downtime passes four continuous hours.

    Kept here as well as in the document because the CODE computes the amount;
    the document is what the subscriber is shown, this is what the program uses.
    A drift between the two is a test failure (tests/test_credits.py)."""

    @field_validator("MODEL", "FALLBACK")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("a model id cannot be empty — comment the line out instead")
        return v.strip()

    @property
    def model_id(self) -> str:
        """The primary, as a provider string: `openrouter:openai/gpt-oss-20b`."""
        return f"{self.PROVIDER}:{self.MODEL}"

    @property
    def embed_provider(self) -> str:
        return "fastembed" if self.EMBED_MODEL.startswith("fastembed:") else "google_genai"

    @property
    def embed_name(self) -> str:
        return self.EMBED_MODEL.removeprefix("fastembed:")

    @property
    def embed_dim(self) -> int:
        return 384 if self.embed_provider == "fastembed" else self.EMBED_DIM

    @property
    def collection(self) -> str:
        """One collection per embedding space, so two models never meet."""
        return f"policies__{self.embed_name.split('/')[-1]}-{self.embed_dim}"

    def ensure_dirs(self) -> None:
        for p in (self.QDRANT_PATH, self.CHECKPOINT_DB, self.MEMORY_DB,
                  self.CREDIT_LEDGER):
            p.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
