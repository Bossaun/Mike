"""Local synthetic voice protocol. No capture/provider/model/runtime integration.

Records are data, not consent to record or join a call. Draft previews contain
only explicit final text/provenance and never inferred agreements or tasks.
"""
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import json

MAX_RECORDS = 1000
MAX_TEXT = 2000
MAX_PREVIEW = 32000


class ProtocolError(ValueError):
    pass


def _id(value):
    if (type(value) is not str or not value or value != value.strip() or len(value) > 200
            or any(ord(char) < 32 for char in value)):
        raise ProtocolError('Explicit bounded identifier without control characters required')


def _utc(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ProtocolError('Explicit timezone-aware timestamp required')
    return value.astimezone(timezone.utc)


def _number(value, *, minimum=0):
    if type(value) is not int or not minimum <= value <= 1000000:
        raise ProtocolError('Explicit bounded nonnegative protocol integer required')


@dataclass(frozen=True)
class SessionKey:
    business: str
    source_id: str
    session_id: str

    def __post_init__(self):
        for value in (self.business, self.source_id, self.session_id): _id(value)


@dataclass(frozen=True)
class SessionSpec:
    key: SessionKey
    started_at: datetime
    expires_at: datetime
    capture_authorized: bool = field(default=False, init=False)

    def __post_init__(self):
        if type(self.key) is not SessionKey or _utc(self.started_at) >= _utc(self.expires_at):
            raise ProtocolError('Explicit session key and positive validity window required')


class Mode(Enum):
    ACTIVE = 'active'
    HALTED = 'halted'
    STOPPED = 'stopped'


def _envelope(row):
    _id(row.id)
    if type(row.key) is not SessionKey: raise ProtocolError('Exact SessionKey required')
    _number(row.sequence, minimum=1); _number(row.epoch); _utc(row.at); _id(row.evidence_id)


@dataclass(frozen=True)
class Control:
    id: str
    key: SessionKey
    sequence: int
    epoch: int
    at: datetime
    evidence_id: str
    kind: str  # halt / interrupt / stop / resume
    confirmed: bool

    def __post_init__(self):
        _envelope(self)
        if self.kind not in ('halt', 'interrupt', 'stop', 'resume') or type(self.confirmed) is not bool:
            raise ProtocolError('Explicit supported control and confirmation required')


@dataclass(frozen=True)
class Segment:
    id: str  # event identity, different from stable segment_id
    key: SessionKey
    sequence: int
    epoch: int
    at: datetime  # source emitted timestamp
    evidence_id: str
    segment_id: str
    revision: int
    start: datetime
    end: datetime
    final: bool
    text: str
    speaker_label: str | None

    def __post_init__(self):
        _envelope(self); _id(self.segment_id); _number(self.revision, minimum=1)
        if not _utc(self.start) < _utc(self.end) <= _utc(self.at):
            raise ProtocolError('Segment requires positive chronological capture/emission times')
        if type(self.final) is not bool or type(self.text) is not str or len(self.text) > MAX_TEXT or (self.final and not self.text.strip()):
            raise ProtocolError('Bounded explicit segment text/final status required')
        if self.speaker_label is not None: _id(self.speaker_label)


@dataclass(frozen=True)
class Speaker:
    id: str
    key: SessionKey
    sequence: int
    epoch: int
    at: datetime
    evidence_id: str
    label: str
    participant_id: str | None
    confirmed: bool

    def __post_init__(self):
        _envelope(self); _id(self.label)
        if type(self.confirmed) is not bool: raise ProtocolError('Explicit speaker confirmation required')
        if self.participant_id is not None: _id(self.participant_id)
        if self.confirmed and self.participant_id is None: raise ProtocolError('Confirmed speaker requires explicit participant identity')


@dataclass(frozen=True)
class SessionState:
    spec: SessionSpec
    mode: Mode
    epoch: int
    sequence: int
    last_at: datetime
    accept_after: datetime
    records: tuple[Control | Segment | Speaker, ...]


def start_session(spec: SessionSpec) -> SessionState:
    if type(spec) is not SessionSpec: raise ProtocolError('Validated session specification required')
    return SessionState(spec, Mode.ACTIVE, 0, 0, spec.started_at, spec.started_at, ())


def apply(state: SessionState, record: Control | Segment | Speaker) -> SessionState:
    """Immutable ordered reducer. Missing/reordered frames fail closed.

    Exact duplicate event replay is idempotent. Accepted controls are structural
    protocol events, not NLP. Unconfirmed halt/stop still block output; only an
    explicitly confirmed resume can re-enable the same unexpired session.
    """
    if type(state) is not SessionState or type(record) not in (Control, Segment, Speaker):
        raise ProtocolError('Validated protocol state/record required')
    if record.key != state.spec.key: raise ProtocolError('Record crosses business/source/session')
    for previous in state.records:
        if previous.id == record.id:
            if previous == record: return state
            raise ProtocolError('Conflicting duplicate event identity')
    if len(state.records) >= MAX_RECORDS: raise ProtocolError('Session record bound exceeded')
    if record.sequence != state.sequence + 1 or record.epoch != state.epoch:
        raise ProtocolError('Missing, reordered or stale-epoch record')
    if (not _utc(state.last_at) <= _utc(record.at) < _utc(state.spec.expires_at)
            or _utc(record.at) < _utc(state.spec.started_at)):
        raise ProtocolError('Record outside chronological valid session')
    mode, epoch, accept_after = state.mode, state.epoch, state.accept_after
    if type(record) is Control:
        if record.kind == 'resume':
            if mode is Mode.ACTIVE or not record.confirmed: raise ProtocolError('Resume requires explicit confirmation after halt/stop')
            mode, accept_after = Mode.ACTIVE, record.at
        else:
            if mode is not Mode.ACTIVE and not (mode is Mode.HALTED and record.kind == 'stop'):
                raise ProtocolError('Invalid halted/stopped transition')
            mode = Mode.STOPPED if record.kind == 'stop' else Mode.HALTED
            epoch += 1  # invalidates all outstanding partials/output tokens
    else:
        if mode is not Mode.ACTIVE: raise ProtocolError('No segment/speaker input while halted or stopped')
        if type(record) is Segment:
            if _utc(record.start) < _utc(accept_after): raise ProtocolError('Segment crosses paused/session boundary')
            revisions = [row for row in state.records if type(row) is Segment
                         and row.epoch == epoch and row.segment_id == record.segment_id]
            previous = revisions[-1] if revisions else None
            if record.revision != (previous.revision + 1 if previous else 1):
                raise ProtocolError('Missing or stale segment revision')
            if previous and previous.final and not record.final: raise ProtocolError('Final segment cannot regress to partial')
        else:
            confirmed = {row.participant_id for row in state.records if type(row) is Speaker
                         and row.epoch == epoch and row.label == record.label and row.confirmed}
            if record.confirmed and confirmed and confirmed != {record.participant_id}:
                raise ProtocolError('Speaker identity conflict; no inferred remapping')
    return replace(state, mode=mode, epoch=epoch, sequence=record.sequence, last_at=record.at,
                   accept_after=accept_after, records=(*state.records, record))


@dataclass(frozen=True)
class OutputToken:
    key: SessionKey
    epoch: int
    sequence: int


def _active(state, now):
    now = _utc(now)
    if state.mode is not Mode.ACTIVE: raise ProtocolError('Output blocked by halt/stop')
    if not _utc(state.last_at) <= now < _utc(state.spec.expires_at):
        raise ProtocolError('Output outside valid current session time')


def output_token(state: SessionState, *, now: datetime) -> OutputToken:
    _active(state, now)
    return OutputToken(state.spec.key, state.epoch, state.sequence)


def validate_output(state: SessionState, token: OutputToken, *, now: datetime) -> None:
    """Caller must validate against current state immediately before any output."""
    _active(state, now)
    if type(token) is not OutputToken or token != OutputToken(state.spec.key, state.epoch, state.sequence):
        raise ProtocolError('Stale or cross-session output token')


@dataclass(frozen=True)
class PreviewLine:
    segment_id: str
    revision: int
    epoch: int
    start: datetime
    end: datetime
    speaker_label: str | None
    participant_id: str | None
    text: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class MeetingPreview:
    key: SessionKey
    epoch: int
    sequence: int
    lines: tuple[PreviewLine, ...]
    transcript_jsonl: str
    draft_only: bool = field(default=True, init=False)
    review_required: bool = field(default=True, init=False)
    task_proposals: tuple = field(default=(), init=False)


def meeting_preview(state: SessionState, token: OutputToken, *, now: datetime) -> MeetingPreview:
    """Final segments only. JSONL quotes source text; no speaker/task inference.

    A fresh token after an explicit resume may review accepted earlier finals;
    old pre-interrupt tokens/partials can never become a post-resume output.
    The existing text-meeting pipeline is neither called nor duplicated.
    """
    validate_output(state, token, now=now)
    latest, speakers = {}, {}
    for row in state.records:
        if type(row) is Segment: latest[(row.epoch, row.segment_id)] = row
        if type(row) is Speaker: speakers[(row.epoch, row.label)] = row
    finals = sorted((row for row in latest.values() if row.final),
                    key=lambda row: (_utc(row.start), _utc(row.end), row.epoch, row.segment_id))
    lines, rendered = [], []
    for row in finals:
        speaker = speakers.get((row.epoch, row.speaker_label))
        participant = speaker.participant_id if speaker and speaker.confirmed else None
        evidence = (row.evidence_id,) + ((speaker.evidence_id,) if participant is not None else ())
        line = PreviewLine(row.segment_id, row.revision, row.epoch, row.start, row.end,
                           row.speaker_label, participant, row.text, evidence)
        body = json.dumps({'segment_id': line.segment_id, 'revision': line.revision, 'epoch': line.epoch,
            'start': _utc(line.start).isoformat(), 'end': _utc(line.end).isoformat(),
            'speaker_label': line.speaker_label, 'participant_id': line.participant_id,
            'text': line.text, 'evidence_ids': line.evidence_ids}, ensure_ascii=False)
        rendered.append(body); lines.append(line)
        if sum(len(value) + 1 for value in rendered) > MAX_PREVIEW:
            raise ProtocolError('Preview bound exceeded; explicitly split review scope')
    return MeetingPreview(state.spec.key, state.epoch, state.sequence, tuple(lines), '\n'.join(rendered))
